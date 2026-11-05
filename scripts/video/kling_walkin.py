"""Make a short Kling clip of Ian walking up to the camera from one photo, through fal.ai.

Usage:
    python3 scripts/video/kling_walkin.py PHOTO OUT_DIR [--style walk_in|walk_up] [--prompt TEXT]
        [--duration 5|10] [--dry-run]

Styles:
    walk_in   the clip starts on the empty wall and ends on the photo, so Ian walks into frame
    walk_up   the clip starts on the photo and Ian walks toward the camera
Both fit the photo into a 1920x1080 frame, widening the wall from the photo's own edges.

Settings come from the "kling" block in video/config.json. The clip is silent (the HeyGen twin
does the talking), gets the AI label plus a "Made with Kling AI" credit, and is saved as
walkin-raw.mp4 (as Kling returned it), walkin.mp4 (labelled) and walkin.json (what was asked and paid).
A dry run checks the photo and settings and prints the cost without calling fal.
"""
import argparse
import base64
import json
import mimetypes
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compose import FONT_BOLD, _drawtext, probe  # noqa: E402
from fal_client import FalClient  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG = os.path.join(ROOT, "video", "config.json")
ALLOWED_DURATIONS = ("5", "10")
STYLES = ("walk_in", "walk_up")
FRAME_W, FRAME_H = 1920, 1080
EDGE_PX = 40  # width of the photo's left and right edge strips used to build the empty wall
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024


class WalkinError(RuntimeError):
    pass


def load_settings(path: str = CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if "kling" not in cfg:
        raise WalkinError("video/config.json has no \"kling\" block.")
    return cfg["kling"]


def estimate_cost(settings: dict, duration: str) -> float:
    rate = settings["price_per_second_usd"]["audio" if settings.get("generate_audio") else "silent"]
    return round(rate * int(duration), 2)


def photo_data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0]
    if mime not in IMAGE_TYPES:
        raise WalkinError(f"Photo must be JPEG, PNG or WebP, got {mime or 'unknown type'}: {path}")
    size = os.path.getsize(path)
    if size > MAX_PHOTO_BYTES:
        raise WalkinError(f"Photo is {size / 1e6:.1f} MB; keep it under {MAX_PHOTO_BYTES // 1_000_000} MB.")
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


def build_arguments(settings: dict, frames: dict, *, style: str = None, prompt: str = None,
                    duration: str = None) -> dict:
    """frames maps "photo" and "empty" to image URLs (data URIs in practice)."""
    style = style or settings["style"]
    if style not in STYLES:
        raise WalkinError(f"Style must be one of {STYLES}, got {style}.")
    duration = str(duration or settings["duration"])
    if duration not in ALLOWED_DURATIONS:
        raise WalkinError(f"Duration must be one of {ALLOWED_DURATIONS}, got {duration}.")
    args = {
        "prompt": (prompt or settings["prompts"][style]).strip(),
        "duration": duration,
        "generate_audio": bool(settings.get("generate_audio", False)),
        "negative_prompt": settings["negative_prompt"],
    }
    if style == "walk_in":
        args.update(start_image_url=frames["empty"], end_image_url=frames["photo"])
    else:
        args["start_image_url"] = frames["photo"]
    return args


def prepare_frames(photo: str, workdir: str) -> dict:
    """Write two 1920x1080 JPEGs: the photo fitted to the frame, and the same frame with nobody in it.

    The photo is scaled to the frame's height: a wide photo is cropped to the centre, a tall
    (full-length) photo is centred on wall made from its own edges.

    The empty wall is the photo's left edge strip fading into its right edge strip, so it keeps
    the wall's colour and light. A plain wall works best; a busy background will look smeared.
    """
    os.makedirs(workdir, exist_ok=True)
    empty = os.path.join(workdir, "frame-empty.jpg")
    fitted = os.path.join(workdir, "frame-photo.jpg")
    wall = (f"[0:v]split[l][r];"
            f"[l]crop={EDGE_PX}:ih:0:0,scale={FRAME_W}:{FRAME_H},setsar=1,format=rgb24[left];"
            f"[r]crop={EDGE_PX}:ih:iw-{EDGE_PX}:0,scale={FRAME_W}:{FRAME_H},setsar=1,format=rgb24[right];"
            f"[left][right]blend=all_expr='A*(1-X/W)+B*(X/W)',gblur=sigma=8")
    _ffmpeg_image(photo, wall + "[wall]", "[wall]", empty)
    fit = (wall + "[wall];"
           f"[0:v]scale=w=-2:h={FRAME_H},crop='min(iw,{FRAME_W})':{FRAME_H},setsar=1,format=rgb24[fg];"
           f"[wall][fg]overlay=x=(W-w)/2:y=0[out]")
    _ffmpeg_image(photo, fit, "[out]", fitted)
    return {"photo": fitted, "empty": empty}


def _ffmpeg_image(src: str, graph: str, label: str, out: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                    "-filter_complex", graph, "-map", label, "-frames:v", "1", "-q:v", "2", out], check=True)


def add_label(raw: str, out: str, label: str, workdir: str) -> None:
    """Burn the AI label (with the Kling credit) into the top-left corner, same style as the twin video."""
    info = probe(raw)
    w, h = info["width"], info["height"]
    unit = min(w, h)
    label_file = os.path.join(workdir, "walkin-label.txt")
    with open(label_file, "w", encoding="utf-8") as f:
        f.write(label)
    vf = "setsar=1," + _drawtext(label_file, FONT_BOLD, round(unit * 0.035), str(round(w * 0.03)),
                                 str(round(h * 0.03)), box=True, pad=round(unit * 0.012))
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw, "-vf", vf,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-an", "-movflags", "+faststart", out], check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("photo")
    ap.add_argument("out_dir")
    ap.add_argument("--style", default="")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--duration", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    settings = load_settings()
    photo_data_uri(args.photo)  # checks type and size before any work
    os.makedirs(args.out_dir, exist_ok=True)
    frames = prepare_frames(args.photo, args.out_dir)
    uris = {name: photo_data_uri(path) for name, path in frames.items()}
    arguments = build_arguments(settings, uris, style=args.style or None, prompt=args.prompt or None,
                                duration=args.duration or None)
    cost = estimate_cost(settings, arguments["duration"])
    if cost > settings["max_cost_usd"]:
        raise WalkinError(f"Estimated ${cost:.2f} is over the ${settings['max_cost_usd']:.2f} cap in config.")
    style = args.style or settings["style"]
    print(f"Style: {style}\nEndpoint: {settings['endpoint']}\nDuration: {arguments['duration']} s, "
          f"audio: {arguments['generate_audio']}\nEstimated cost: ${cost:.2f}\nPrompt: {arguments['prompt']}")
    if args.dry_run:
        print("Dry run: fal was not called and nothing was spent. Frames are in the out dir.")
        return 0

    client = FalClient(os.environ.get("FAL_KEY", ""), base_url=os.environ.get("FAL_QUEUE_URL") or None)
    job = client.submit(settings["endpoint"], arguments)
    print(f"Submitted fal request {job['request_id']}")
    result = client.wait(job, poll_seconds=settings.get("poll_seconds", 10),
                         timeout_minutes=settings.get("timeout_minutes", 20))
    url = (result.get("video") or {}).get("url")
    if not url:
        raise WalkinError(f"fal returned no video url: {json.dumps(result)[:300]}")
    raw = os.path.join(args.out_dir, "walkin-raw.mp4")
    client.download(url, raw)
    add_label(raw, os.path.join(args.out_dir, "walkin.mp4"), settings["label_text"], args.out_dir)
    record = {k: v for k, v in arguments.items() if not k.endswith("_image_url")}
    record.update(style=style, endpoint=settings["endpoint"], request_id=job["request_id"], estimated_cost_usd=cost,
                  duration_seconds=probe(raw)["duration"])
    with open(os.path.join(args.out_dir, "walkin.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    print(f"Saved walkin.mp4 ({record['duration_seconds']:.1f} s), estimated ${cost:.2f}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:  # noqa: BLE001  (report any failure as a clean CI error)
        print(f"::error::{err}")
        sys.exit(1)
