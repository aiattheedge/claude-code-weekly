"""Finish a HeyGen render with ffmpeg: optional slides with the twin in a corner,
burned-in captions, an AI label and an end card."""
import json
import os
import subprocess

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CAPTION_STYLE = ("FontName=DejaVu Sans,FontSize=13,PrimaryColour=&H00FFFFFF,"
                 "BackColour=&H99000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=22")


# Slides layout: captions sit centred under the slide area, clear of the twin in the bottom-right corner.
SLIDES_CAPTION_STYLE = CAPTION_STYLE + ",Alignment=2,MarginR=140,MarginL=10"


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        check=True, capture_output=True, text=True).stdout
    info = json.loads(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": video.get("r_frame_rate") or "30/1",
        "sample_rate": int(audio["sample_rate"]) if audio else 48000,
        "channel_layout": (audio or {}).get("channel_layout") or "stereo",
        "duration": float(info["format"].get("duration") or 0),
    }


def _timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def estimate_srt(text: str, duration: float, words_per_cue: int = 8) -> str:
    """Fallback captions: spread the script's words evenly over the video length."""
    words = text.split()
    if not words or duration <= 0:
        return ""
    per_word = duration / len(words)
    cues = []
    for n, start in enumerate(range(0, len(words), words_per_cue), 1):
        chunk = words[start:start + words_per_cue]
        t0, t1 = start * per_word, min(duration, (start + len(chunk)) * per_word)
        cues.append(f"{n}\n{_timestamp(t0)} --> {_timestamp(t1)}\n{' '.join(chunk)}\n")
    return "\n".join(cues)


def _drawtext(textfile: str, font: str, size: int, x: str, y: str, box: bool = False, pad: int = 0) -> str:
    parts = [f"drawtext=fontfile={font}", f"textfile={textfile}", f"fontsize={size}",
             "fontcolor=white", f"x={x}", f"y={y}"]
    if box:
        parts += ["box=1", "boxcolor=black@0.55", f"boxborderw={pad}"]
    return ":".join(parts)


def with_slides(raw_video: str, schedule: list, out_path: str, *, width: int = 1920, height: int = 1080,
                twin_width: float = 0.30, twin_max_height: float = 0.50, margin: int = 40,
                border_px: int = 4, border_color: str = "0xff7a1a", background: str = "0x0b0b0a") -> None:
    """Write out_path: slides full screen, switching per schedule [(png, start, end)], with the
    twin video scaled into the bottom-right corner and its audio kept."""
    info = probe(raw_video)
    box_w, box_h = round(width * twin_width), round(height * twin_max_height)
    inputs, filters = ["-i", raw_video], []
    for i, (png, start, end) in enumerate(schedule, 1):
        inputs += ["-loop", "1", "-framerate", "30", "-t", f"{end - start:.3f}", "-i", png]
        filters.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background},setsar=1,fps=30[s{i}]")
    n = len(schedule)
    filters.append("".join(f"[s{i}]" for i in range(1, n + 1)) + f"concat=n={n}:v=1:a=0[slides]")
    filters.append(f"[0:v]scale=w={box_w}:h={box_h}:force_original_aspect_ratio=decrease,"
                   f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
                   f"pad=iw+{2 * border_px}:ih+{2 * border_px}:{border_px}:{border_px}:color={border_color},setsar=1[twin]")
    filters.append(f"[slides][twin]overlay=x=W-w-{margin}:y=H-h-{margin}:shortest=1,fps=30[v]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "0:a",
           "-t", f"{info['duration']:.3f}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", out_path]
    subprocess.run(cmd, check=True)


def finish(raw_video: str, srt: str, out_path: str, *, label: str, end_lines: list,
           end_seconds: int, workdir: str, caption_style: str = CAPTION_STYLE) -> None:
    """Write out_path: raw video with captions and label, followed by the end card."""
    info = probe(raw_video)
    w, h = info["width"], info["height"]
    unit = min(w, h)

    def textfile(name, text):
        path = os.path.join(workdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    label_file = textfile("label.txt", label)
    main = (f"[0:v]setsar=1,subtitles=filename={srt}:force_style='{caption_style}',"
            + _drawtext(label_file, FONT_BOLD, round(unit * 0.035), str(round(w * 0.03)),
                        str(round(h * 0.03)), box=True, pad=round(unit * 0.012))
            + "[main]")

    card_filters = []
    for i, line in enumerate(end_lines):
        first = i == 0
        size = round(unit * (0.07 if first else 0.035))
        size = min(size, round(w * 0.9 / (max(len(line), 1) * 0.6)))  # keep long lines on screen
        offset = -0.14 if first else -0.02 + (i - 1) * 0.075
        y = f"(h/2){round(unit * offset):+d}"
        card_filters.append(_drawtext(textfile(f"end{i}.txt", line), FONT_BOLD if first else FONT_REGULAR,
                                      size, "(w-text_w)/2", y))
    card = "[1:v]setsar=1" + "".join("," + f for f in card_filters) + "[card]"

    graph = ";".join([main, card, "[main][0:a][card][2:a]concat=n=2:v=1:a=1[v][a]"])
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", raw_video,
        "-f", "lavfi", "-i", f"color=c=0x14161a:s={w}x{h}:r={info['fps']}:d={end_seconds}",
        "-f", "lavfi", "-t", str(end_seconds),
        "-i", f"anullsrc=r={info['sample_rate']}:cl={info['channel_layout']}",
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path,
    ]
    subprocess.run(cmd, check=True)
