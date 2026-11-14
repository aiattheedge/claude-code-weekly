"""Parse and check a spoken video script (video/script-YYYY-MM-DD.md).

Format:
    ---
    title: How I'm using Claude Duet
    date: 2026-11-02
    ---
    Spoken paragraphs. Lines starting with # are section labels and are not
    spoken. HTML comments (<!-- ... -->) are notes and are not spoken.

Each "# Label" line also starts a section. Slides (video/slides/<date>/slides.json)
switch at section starts, so a label names the slide that shows while it is spoken.

Optional front-matter keys override video/config.json for this video:
engine, aspect_ratio, resolution, avatar_id, voice_id, motion_prompt, more_blinking,
pitch (HeyGen voice pitch in semitones, -4 to 4), price_per_minute_usd (this look's rate, for the
cost estimate) and max_cost_usd (refuse to render when the estimate is higher).
"""
import dataclasses
import datetime
import re

REQUIRED_KEYS = ("title", "date")
DISCLOSURE = re.compile(r"\bai-generated\b|\bai twin\b|\bdigital twin\b")
DISCLOSURE_MODES = ("video", "post")  # "post": the disclosure is in the post text, not the video
OVERRIDE_KEYS = ("engine", "aspect_ratio", "resolution", "avatar_id", "voice_id", "motion_prompt", "more_blinking",
                 "pitch", "price_per_minute_usd", "max_cost_usd")


class ScriptError(ValueError):
    """The script file breaks the format or a content rule."""


@dataclasses.dataclass(frozen=True)
class Script:
    title: str
    date: str
    spoken_text: str
    overrides: dict
    sections: tuple = ()  # (label, index of the first spoken word after the label)
    disclosure: str = "video"  # "video": spoken line, on-screen label and end-card line; "post": none in the video

    @property
    def word_count(self) -> int:
        return len(self.spoken_text.split())


def parse(text: str) -> Script:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.S)
    if not match:
        raise ScriptError("Script must start with a front-matter block between two '---' lines.")
    meta = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ScriptError(f"Front-matter line is not 'key: value': {line!r}")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    for key in REQUIRED_KEYS:
        if not meta.get(key):
            raise ScriptError(f"Front matter is missing '{key}'.")
    try:
        datetime.date.fromisoformat(meta["date"])
    except ValueError as err:
        raise ScriptError(f"date must be YYYY-MM-DD, got {meta['date']!r}") from err

    body = re.sub(r"<!--.*?-->", "", match.group(2), flags=re.S)
    paragraphs, current, sections, words = [], [], [], 0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            sections.append((stripped.lstrip("#").strip(), words))
            continue
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(stripped)
        words += len(stripped.split())
    if current:
        paragraphs.append(" ".join(current))
    spoken = "\n\n".join(paragraphs)
    if not spoken:
        raise ScriptError("Script has no spoken text.")

    disclosure = meta.get("disclosure", "video")
    if disclosure not in DISCLOSURE_MODES:
        raise ScriptError(f"disclosure must be one of {DISCLOSURE_MODES}, got {disclosure!r}")
    overrides = {k: meta[k] for k in OVERRIDE_KEYS if meta.get(k)}
    return Script(title=meta["title"], date=meta["date"], spoken_text=spoken, overrides=overrides,
                  sections=tuple(sections), disclosure=disclosure)


def check(script: Script, *, max_words: int, disclosure_words: int, blocked_terms=()) -> None:
    """Raise ScriptError when the script breaks a content rule."""
    if script.word_count > max_words:
        raise ScriptError(f"Script has {script.word_count} words; the limit is {max_words}.")
    # The hook may come first, but the spoken disclosure must follow within disclosure_words.
    # A bare "AI" (as in "AI news") isn't enough: it must say "AI-generated", "AI twin" or "digital twin".
    opening = " ".join(script.spoken_text.split()[:disclosure_words]).lower()
    if script.disclosure == "video" and not DISCLOSURE.search(opening):
        raise ScriptError(f"The first {disclosure_words} spoken words must say the video is AI-generated "
                          '("AI-generated", "AI twin" or "digital twin").')
    full = f"{script.title}\n{script.spoken_text}".lower()
    for term in blocked_terms:
        if term and term.lower() in full:
            raise ScriptError("Script contains a blocked term (see VIDEO_BLOCKED_TERMS).")
