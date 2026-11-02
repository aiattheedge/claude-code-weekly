"""Slides shown beside Ian's twin, one per script section.

A video gets slides when video/slides/<date>/slides.json exists:

    [{"section": "Hook", "html": "slide-01.html", "png": "out/slides/2026-11-02/slide-01.png"}, ...]

"section" is a "# Label" from the script. A slide shows from the first spoken word of its
section until the next slide starts. "html" is rendered to "png" (1920x1080, path relative to
the repo root) by scripts/render.js in the Render video workflow before the video is made.
"""
import json
import os
import re


class SlideError(ValueError):
    """slides.json does not fit the script."""


def slides_dir(repo_root: str, date: str) -> str:
    return os.path.join(repo_root, "video", "slides", date)


def load(repo_root: str, date: str):
    """Return the slide list for a date, or None when the video has no slides."""
    path = os.path.join(slides_dir(repo_root, date), "slides.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        slides = json.load(f)
    if not slides:
        raise SlideError(f"{path} lists no slides.")
    return slides


def check(slides: list, sections: tuple) -> None:
    """Every slide must name a section of the script, in script order, and the first must start at word 0."""
    labels = [label for label, _ in sections]
    last = -1
    for slide in slides:
        if slide.get("section") not in labels:
            raise SlideError(f"Slide {slide.get('html')!r} names section {slide.get('section')!r}, "
                             f"which is not a '# Label' in the script. Labels: {labels}.")
        index = labels.index(slide["section"])
        if index <= last:
            raise SlideError("Slides must follow the script's section order, one slide per section at most.")
        last = index
    first_word = dict(sections)[slides[0]["section"]]
    if first_word != 0:
        raise SlideError("The first slide must belong to the section where speaking starts.")


def _seconds(stamp: str) -> float:
    h, m, rest = stamp.strip().split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def cue_times(srt: str) -> list:
    """[(start_seconds, end_seconds, word_count)] for each caption cue."""
    cues = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        timing = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue
        start, end = (_seconds(part) for part in lines[timing].split("-->"))
        cues.append((start, end, len(" ".join(lines[timing + 1:]).split())))
    return cues


def word_time(word_index: int, total_words: int, cues: list, duration: float) -> float:
    """When a spoken word starts, from the captions. Caption word counts can differ a little
    from the script's, so positions are matched by fraction of the whole."""
    if word_index <= 0:
        return 0.0
    caption_words = sum(n for _, _, n in cues)
    if not cues or not caption_words or total_words <= 0:
        return duration * word_index / max(total_words, 1)
    target = word_index * caption_words / total_words
    seen = 0
    for start, end, n in cues:
        if seen + n > target:
            return start + (end - start) * (target - seen) / max(n, 1)
        seen += n
    return duration


def schedule(slides: list, sections: tuple, total_words: int, srt: str, duration: float) -> list:
    """[(png, start, end)] covering 0..duration."""
    starts = dict(sections)
    cues = cue_times(srt)
    times = [0.0] + [round(word_time(starts[s["section"]], total_words, cues, duration), 3) for s in slides[1:]]
    out = []
    for i, slide in enumerate(slides):
        end = times[i + 1] if i + 1 < len(slides) else duration
        if end - times[i] < 0.5:
            raise SlideError(f"Slide {slide['html']!r} would show for under half a second.")
        out.append((slide["png"], times[i], end))
    return out
