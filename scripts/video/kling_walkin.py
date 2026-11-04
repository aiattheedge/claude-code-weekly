"""Make a short Kling clip of Ian walking up to the camera from one photo, through fal.ai.

Usage:
    python3 scripts/video/kling_walkin.py PHOTO OUT_DIR [--prompt TEXT] [--duration 5|10] [--dry-run]

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


def build_arguments(settings: dict, image_uri: str, *, prompt: str = None, duration: str = None) -> dict:
    duration = str(duration or settings["duration"])
    if duration not in ALLOWED_DURATIONS:
        raise WalkinError(f"Duration must be one of {ALLOWED_DURATIONS}, got {duration}.")
    return {
        "prompt": (prompt or settings["prompt"]).strip(),
        "start_image_url": image_uri,
        "duration": duration,
        "generate_audio": bool(settings.get("generate_audio", False)),
        "negative_prompt": settings["negative_prompt"],
    }


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
    ap.add_argument("--prompt", default="")
    ap.add_argument("--duration", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    settings = load_settings()
    image_uri = photo_data_uri(args.photo)
    arguments = build_arguments(settings, image_uri, prompt=args.prompt or None,
                                duration=args.duration or None)
    cost = estimate_cost(settings, arguments["duration"])
    if cost > settings["max_cost_usd"]:
        raise WalkinError(f"Estimated ${cost:.2f} is over the ${settings['max_cost_usd']:.2f} cap in config.")
    print(f"Endpoint: {settings['endpoint']}\nDuration: {arguments['duration']} s, "
          f"audio: {arguments['generate_audio']}\nEstimated cost: ${cost:.2f}\nPrompt: {arguments['prompt']}")
    if args.dry_run:
        print("Dry run: fal was not called and nothing was spent.")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
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
    record = {k: v for k, v in arguments.items() if k != "start_image_url"}
    record.update(endpoint=settings["endpoint"], request_id=job["request_id"], estimated_cost_usd=cost,
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
