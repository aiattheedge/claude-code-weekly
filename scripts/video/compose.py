"""Finish a HeyGen render with ffmpeg: burned-in captions, an AI label and an end card."""
import json
import os
import subprocess

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CAPTION_STYLE = ("FontName=DejaVu Sans,FontSize=13,PrimaryColour=&H00FFFFFF,"
                 "BackColour=&H99000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=22")


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


def finish(raw_video: str, srt: str, out_path: str, *, label: str, end_lines: list,
           end_seconds: int, workdir: str) -> None:
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
    main = (f"[0:v]setsar=1,subtitles=filename={srt}:force_style='{CAPTION_STYLE}',"
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
