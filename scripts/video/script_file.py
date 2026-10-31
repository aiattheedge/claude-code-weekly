"""Parse and check a spoken video script (video/script-YYYY-MM-DD.md).

Format:
    ---
    title: How I'm using Claude Duet
    date: 2026-11-02
    ---
    Spoken paragraphs. Lines starting with # are section labels and are not
    spoken. HTML comments (<!-- ... -->) are notes and are not spoken.

Optional front-matter keys override video/config.json for this video:
engine, aspect_ratio, resolution, avatar_id, voice_id.
"""
import dataclasses
import datetime
import re

REQUIRED_KEYS = ("title", "date")
OVERRIDE_KEYS = ("engine", "aspect_ratio", "resolution", "avatar_id", "voice_id")


class ScriptError(ValueError):
    """The script file breaks the format or a content rule."""


@dataclasses.dataclass(frozen=True)
class Script:
    title: str
    date: str
    spoken_text: str
    overrides: dict

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
    paragraphs, current = [], []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))
    spoken = "\n\n".join(paragraphs)
    if not spoken:
        raise ScriptError("Script has no spoken text.")

    overrides = {k: meta[k] for k in OVERRIDE_KEYS if meta.get(k)}
    return Script(title=meta["title"], date=meta["date"], spoken_text=spoken, overrides=overrides)


def check(script: Script, *, max_words: int, disclosure_words: int, blocked_terms=()) -> None:
    """Raise ScriptError when the script breaks a content rule."""
    if script.word_count > max_words:
        raise ScriptError(f"Script has {script.word_count} words; the limit is {max_words}.")
    opening = script.spoken_text.split()[:disclosure_words]
    if not any(re.sub(r"\W", "", w) == "AI" or w.startswith("AI-") for w in opening):
        raise ScriptError(f"The first {disclosure_words} spoken words must say the video is AI-generated.")
    full = f"{script.title}\n{script.spoken_text}".lower()
    for term in blocked_terms:
        if term and term.lower() in full:
            raise ScriptError("Script contains a blocked term (see VIDEO_BLOCKED_TERMS).")
