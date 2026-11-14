"""Turn a spoken script into a finished HeyGen video of Ian.

Usage:
    python3 scripts/video/make_video.py video/script-2026-11-02.md --dry-run
    python3 scripts/video/make_video.py video/script-2026-11-02.md

Dry run checks the script, estimates cost and (when HEYGEN_API_KEY is set) picks
the twin look and voice. It never calls the paid create-video endpoint.

Environment:
    HEYGEN_API_KEY        required for a render (repo secret)
    HEYGEN_AVATAR_ID      optional look id override (repo variable)
    HEYGEN_VOICE_ID       optional voice id override (repo variable)
    VIDEO_BLOCKED_TERMS   optional comma-separated words the script must not contain
    VIDEO_OUT_DIR         output folder (default out/video)
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compose  # noqa: E402
import script_file  # noqa: E402
import slides as slide_deck  # noqa: E402
from heygen_client import HeyGenClient, HeyGenError, download  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(REPO_ROOT, "video", "config.json")


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_settings(config: dict, script: script_file.Script, env=os.environ) -> dict:
    """Merge config, repo variables and script front matter (later wins)."""
    settings = dict(config["heygen"])
    if env.get("HEYGEN_AVATAR_ID"):
        settings["avatar_id"] = env["HEYGEN_AVATAR_ID"]
    if env.get("HEYGEN_VOICE_ID"):
        settings["voice_id"] = env["HEYGEN_VOICE_ID"]
    settings.update(script.overrides)
    return settings


def video_disclosure(config: dict, script: script_file.Script):
    """Return (on-screen label, end-card lines) for the script's disclosure mode."""
    lines = list(config["end_card"]["lines"])
    if script.disclosure == "post":
        # Disclosed in the post text instead: no label, and no end-card line about AI.
        return "", [l for l in lines if not script_file.DISCLOSURE.search(l.lower())]
    return config["disclosure"]["label_text"], lines


def choose_look(looks: list, *, engine: str, avatar_id: str = "", avatar_name: str = "") -> dict:
    """Pick the twin look to render with, or explain why none fits."""
    if avatar_id:
        match = next((l for l in looks if l.get("id") == avatar_id), None)
        if not match:
            raise HeyGenError(f"No private look has id {avatar_id}. Looks found: "
                              f"{[(l.get('name'), l.get('id'), l.get('avatar_type'), l.get('supported_api_engines')) for l in looks]}.")
        candidates = [match]
    else:
        finished = [l for l in looks if l.get("status") not in ("processing", "failed")]
        candidates = finished
        if avatar_name:
            candidates = [l for l in finished if avatar_name.lower() in (l.get("name") or "").lower()]
        if not candidates:
            if avatar_name and finished:
                raise HeyGenError(f"No finished look is named like {avatar_name!r}. "
                                  f"Looks found: {[l.get('name') for l in finished]}.")
            raise HeyGenError("No finished private digital twin look found. Create the twin in HeyGen first.")
    for look in candidates:
        engines = look.get("supported_api_engines")
        if engines is None or engine in engines:
            return look
    raise HeyGenError(f"No matching look supports engine {engine}. "
                      f"Supported: {[l.get('supported_api_engines') for l in candidates]}. "
                      "Set engine to one of these in video/config.json or the script.")


def estimate(script: script_file.Script, config: dict, engine: str, price_override=None) -> dict:
    """Words at the configured pace times the engine's price, or the script's own price_per_minute_usd
    (photo-avatar looks bill below the digital twin rate)."""
    limits = config["limits"]
    minutes = script.word_count / limits["words_per_minute"]
    price = float(price_override) if price_override else limits["price_per_minute_usd"].get(engine)
    return {"words": script.word_count, "minutes": round(minutes, 1),
            "cost_usd": round(minutes * price, 2) if price is not None else None}


def _is_true(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "yes", "1")


def build_payload(script: script_file.Script, settings: dict, look_id: str, voice_id: str) -> dict:
    engine = {"type": settings["engine"]}
    avatar_v = settings["engine"] == "avatar_v"
    if avatar_v and _is_true(settings.get("more_blinking")):
        engine["more_blinking"] = True
    payload = {
        "type": "avatar",
        "avatar_id": look_id,
        "voice_id": voice_id,
        "script": script.spoken_text,
        "title": f"{script.date} {script.title}",
        "resolution": settings["resolution"],
        "aspect_ratio": settings["aspect_ratio"],
        "engine": engine,
        "caption": {"file_format": "srt"},
    }
    # Avatar V only: natural-language direction for body motion and hand gestures.
    if avatar_v and settings.get("motion_prompt"):
        payload["motion_prompt"] = settings["motion_prompt"]
    if settings.get("pitch") not in (None, ""):
        payload["voice_settings"] = {"engine_settings": {"engine_type": "heygen_voice",
                                                         "pitch": float(settings["pitch"])}}
    return payload


def check_cost(est: dict, settings: dict) -> None:
    """Refuse a render whose estimate is over the script's max_cost_usd."""
    cap = settings.get("max_cost_usd")
    if cap and (est["cost_usd"] is None or est["cost_usd"] > float(cap)):
        raise HeyGenError(f"Estimated ${est['cost_usd']} is over the ${cap} cap in the script.")


def api_balance(client: HeyGenClient):
    """The API wallet balance in USD, for the record. Never fails the run."""
    try:
        data = client._request("GET", "/v3/users/me").get("data") or {}
        return (data.get("wallet") or {}).get("remaining_balance")
    except HeyGenError:
        return None


def idempotency_key(payload: dict) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:40]
    return f"ccw-video-{digest}"


def write_summary(lines: list) -> None:
    text = "\n".join(lines) + "\n"
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("script", help="path to video/script-YYYY-MM-DD.md")
    parser.add_argument("--dry-run", action="store_true", help="check and estimate only; spend nothing")
    args = parser.parse_args(argv)

    config = load_config()
    with open(args.script, encoding="utf-8") as f:
        script = script_file.parse(f.read())
    blocked = [t.strip() for t in os.environ.get("VIDEO_BLOCKED_TERMS", "").split(",")]
    script_file.check(script, max_words=config["limits"]["max_words"],
                      disclosure_words=config["disclosure"]["require_ai_in_first_words"],
                      blocked_terms=blocked)
    settings = resolve_settings(config, script)
    est = estimate(script, config, settings["engine"], settings.get("price_per_minute_usd"))
    check_cost(est, settings)
    deck = slide_deck.load(REPO_ROOT, script.date)
    if deck:
        slide_deck.check(deck, script.sections)
    summary = [f"## Video: {script.title} ({script.date})",
               f"- Script: `{args.script}`, {est['words']} words, about {est['minutes']} min",
               f"- Engine: {settings['engine']}, estimated cost ${est['cost_usd']}",
               f"- Layout: {f'{len(deck)} slides, twin in the corner' if deck else 'twin full frame'}"]

    api_key = os.environ.get("HEYGEN_API_KEY", "")
    if args.dry_run and not api_key:
        write_summary(summary + ["- Dry run: HEYGEN_API_KEY not set, skipped the twin lookup."])
        return 0

    client = HeyGenClient(api_key)
    # A pinned look id may be a generated look (new outfit or setting), which HeyGen lists outside
    # the digital twin type, so search every private look for it.
    looks = client.list_looks() if settings.get("avatar_id") else client.list_twin_looks()
    look = choose_look(looks, engine=settings["engine"],
                       avatar_id=settings.get("avatar_id", ""), avatar_name=settings.get("avatar_name", ""))
    voice_id = settings.get("voice_id") or look.get("default_voice_id")
    if not voice_id:
        raise HeyGenError("The look has no default voice. Set voice_id in video/config.json or HEYGEN_VOICE_ID.")
    summary.append(f"- Look: {look.get('name')} (`{look.get('id')}`, {look.get('avatar_type')}, "
                   f"engines {look.get('supported_api_engines')}), voice `{voice_id}`")
    if args.dry_run:
        write_summary(summary + ["- Dry run: nothing was rendered or charged."])
        return 0

    if deck:  # check before paying HeyGen
        missing = [s["png"] for s in deck if not os.path.exists(os.path.join(REPO_ROOT, s["png"]))]
        if missing:
            raise slide_deck.SlideError(f"Slide images were not rendered: {missing}")

    out_dir = os.path.join(os.environ.get("VIDEO_OUT_DIR", os.path.join(REPO_ROOT, "out", "video")), script.date)
    os.makedirs(out_dir, exist_ok=True)
    payload = build_payload(script, settings, look["id"], voice_id)
    balance_before = api_balance(client)
    video_id = client.create_video(payload, idempotency_key(payload))
    print(f"HeyGen video id: {video_id}")
    limits = config["limits"]
    data = client.wait_for_video(video_id, poll_seconds=limits["poll_seconds"],
                                 timeout_minutes=limits["poll_timeout_minutes"])

    raw = os.path.join(out_dir, "heygen-raw.mp4")
    download(data["video_url"], raw)
    srt = os.path.join(out_dir, "captions.srt")
    if data.get("subtitle_url"):
        download(data["subtitle_url"], srt)
        caption_source = "HeyGen"
    else:
        with open(srt, "w", encoding="utf-8") as f:
            f.write(compose.estimate_srt(script.spoken_text, compose.probe(raw)["duration"]))
        caption_source = "estimated from the script (HeyGen sent no subtitles)"

    final = os.path.join(out_dir, f"claude-code-latest-{script.date}.mp4")
    base, caption_style = raw, compose.CAPTION_STYLE
    if deck:
        with open(srt, encoding="utf-8") as f:
            timing = slide_deck.schedule(deck, script.sections, script.word_count, f.read(),
                                         compose.probe(raw)["duration"])
        timing = [(os.path.join(REPO_ROOT, png), start, end) for png, start, end in timing]
        layout = config.get("layout", {})
        base = os.path.join(out_dir, "with-slides.mp4")
        compose.with_slides(raw, timing, base, twin_width=layout.get("twin_width", 0.30),
                            twin_max_height=layout.get("twin_max_height", 0.50),
                            margin=layout.get("margin_px", 40))
        caption_style = compose.SLIDES_CAPTION_STYLE
        summary.append("- Slides: " + ", ".join(f"{os.path.basename(p)} at {round(t0)}s" for p, t0, _ in timing))
    label, end_lines = video_disclosure(config, script)
    compose.finish(base, srt, final, label=label,
                   end_lines=end_lines, end_seconds=config["end_card"]["seconds"],
                   workdir=out_dir, caption_style=caption_style)
    seconds = data.get("duration") or compose.probe(raw)["duration"]
    with open(os.path.join(out_dir, "render.json"), "w", encoding="utf-8") as f:
        json.dump({"video_id": video_id, "look_id": look["id"], "voice_id": voice_id,
                   "engine": settings["engine"], "voice_settings": payload.get("voice_settings"),
                   "duration_seconds": seconds, "captions": caption_source,
                   "balance_before_usd": balance_before, "balance_after_usd": api_balance(client)},
                  f, indent=2)
    write_summary(summary + [f"- Rendered {round(float(seconds) / 60, 1)} min, HeyGen id `{video_id}`",
                             f"- Captions: {caption_source}", f"- Output: `{final}`"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (script_file.ScriptError, HeyGenError, slide_deck.SlideError) as err:
        print(f"::error::{err}")
        sys.exit(1)
