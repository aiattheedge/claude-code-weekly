"""Make one short HeyGen test clip through the API, to check a look and voice before a full render.

Reads a sample request (video/samples/<name>.json), finds the look and voice by name, picks the first
engine the look supports from the request's list, refuses to run if the estimate is over max_cost_usd,
renders once, and saves the clip plus a record of what was used and the API balance before and after.

    python3 scripts/video/heygen_sample.py video/samples/<name>.json OUT_DIR [--dry-run]
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from heygen_client import HeyGenClient, HeyGenError, download  # noqa: E402

REQUIRED = ("look_name", "voice_name", "engines", "text", "max_cost_usd")


def load_request(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        req = json.load(f)
    missing = [k for k in REQUIRED if not req.get(k)]
    if missing:
        raise HeyGenError(f"{path} is missing {missing}.")
    return req


def look_group(look: dict) -> str:
    return look.get("group_id") or look.get("avatar_group_id") or (look.get("group") or {}).get("id") or ""


def find_look(looks: list, name: str, group_id: str = "") -> dict:
    """The one finished private look whose name matches exactly (ignoring case), in the avatar
    group_id when given (two avatars can each have a look with the same name)."""
    matches = [l for l in looks if (l.get("name") or "").strip().lower() == name.strip().lower()
               and l.get("status") not in ("processing", "failed")]
    if group_id and len(matches) > 1:
        in_group = [l for l in matches if look_group(l) == group_id]
        if not in_group:
            raise HeyGenError(f"No look named {name!r} is in group {group_id}. Matches: {matches}")
        matches = in_group
    if len(matches) != 1:
        raise HeyGenError(f"Expected one look named {name!r}, found {len(matches)}. Looks: "
                          f"{[(l.get('name'), l.get('id'), l.get('avatar_type'), l.get('supported_api_engines')) for l in looks]}")
    return matches[0]


def pick_engine(look: dict, engines: list) -> str:
    supported = look.get("supported_api_engines")
    for engine in engines:
        if supported is None or engine in supported:
            return engine
    raise HeyGenError(f"Look {look.get('name')!r} supports {supported}, none of {engines}.")


def find_voice(voices: list, name: str, preferred_id: str = "") -> dict:
    """The voice named `name`; when several share the name, the look's default voice among them."""
    matches = [v for v in voices if (v.get("name") or "").strip().lower() == name.strip().lower()]
    if len(matches) > 1 and preferred_id:
        matches = [v for v in matches if v.get("voice_id") == preferred_id] or matches
    if len(matches) != 1:
        first = name.split()[0].lower()
        near = [v for v in voices if first in (v.get("name") or "").lower() or v.get("voice_id") == preferred_id
                or v.get("type") in ("private", "custom", "clone") or v.get("is_custom")]
        raise HeyGenError(f"Expected one voice named {name!r}, found {len(matches)}. Look default voice: "
                          f"{preferred_id!r}. Voices like it: {near[:40]}")
    return matches[0]


def estimate_cost(text: str, engine: str, prices: dict, words_per_minute: int) -> float:
    price = prices.get(engine)
    if price is None:
        raise HeyGenError(f"No price for engine {engine} in video/config.json limits.price_per_minute_usd.")
    return round(len(text.split()) / words_per_minute * price, 2)


def list_voices(client: HeyGenClient) -> list:
    """Private and public voices, every page. Tries the v3 list first, then v2."""
    errors = []
    for path in ("/v3/voices", "/v2/voices"):
        voices, token = [], None
        try:
            while True:
                resp = client._request("GET", path, query={"limit": 100, "token": token} if path.startswith("/v3") else None)
                data = resp.get("data")
                voices.extend((data.get("voices") if isinstance(data, dict) else data) or [])
                token = resp.get("next_token") or (data.get("next_token") if isinstance(data, dict) else None)
                if not token or not (resp.get("has_more") or (isinstance(data, dict) and data.get("has_more"))):
                    break
        except HeyGenError as err:
            errors.append(str(err))
            continue
        if voices:
            print(f"Listed {len(voices)} voices from {path}")
            return voices
    raise HeyGenError(f"Could not list voices: {errors}")


def private_voices(client: HeyGenClient, default_id: str) -> list:
    """Compact list of the account's own voices, from every listing that answers. Free."""
    found = {}
    tries = [("/v3/voices", {"type": "private", "limit": 100}), ("/v3/voices", {"ownership": "private", "limit": 100}),
             ("/v2/voices", None)]
    for path, query in tries:
        try:
            resp = client._request("GET", path, query=query)
        except HeyGenError as err:
            print(f"{path} {query}: {err}")
            continue
        data = resp.get("data")
        voices = (data.get("voices") if isinstance(data, dict) else data) or []
        own = [v for v in voices if v.get("type") not in ("public",) and not v.get("is_public")
               or v.get("voice_id") == default_id]
        print(f"{path} {query}: {len(voices)} voices, {len(own)} not public")
        for v in own:
            found.setdefault(v.get("voice_id"), {k: v.get(k) for k in ("name", "voice_id", "type", "available_engines",
                                                                         "default_engine", "language")})
    for path in (f"/v3/voices/{default_id}", f"/v2/voices/{default_id}"):
        try:
            print(f"{path}: {json.dumps(client._request('GET', path))[:600]}")
        except HeyGenError as err:
            print(f"{path}: {err}")
    return list(found.values())


def api_balance(client: HeyGenClient) -> dict:
    """The raw remaining-quota answer, for the record. Never fails the run."""
    for path in ("/v3/users/me", "/v2/user/remaining_quota"):
        try:
            return {"endpoint": path, "response": client._request("GET", path)}
        except HeyGenError as err:
            last = str(err)
    return {"error": last}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("request")
    parser.add_argument("out_dir")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(repo_root, "video", "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    req = load_request(args.request)
    client = HeyGenClient(os.environ.get("HEYGEN_API_KEY", ""))

    look = find_look(client.list_looks(), req["look_name"], req.get("group_id", ""))
    engine = pick_engine(look, req["engines"])
    own = private_voices(client, look.get("default_voice_id", ""))
    print("Own voices: " + json.dumps(own))
    if req.get("voice_id"):
        voice = next((v for v in own if v.get("voice_id") == req["voice_id"]), {"voice_id": req["voice_id"], "name": req["voice_name"]})
    else:
        voice = find_voice(own, req["voice_name"], look.get("default_voice_id", ""))
    limits = config["limits"]
    cost = estimate_cost(req["text"], engine, limits["price_per_minute_usd"], limits["words_per_minute"])
    print(f"Look: {look.get('name')} ({look.get('id')}, {look.get('avatar_type')}, engines {look.get('supported_api_engines')})")
    print(f"Voice: {voice.get('name')} ({voice.get('voice_id')})")
    print(f"Engine: {engine}, {len(req['text'].split())} words, estimated ${cost:.2f}")
    if cost > float(req["max_cost_usd"]):
        raise HeyGenError(f"Estimated ${cost:.2f} is over the ${req['max_cost_usd']} cap.")

    os.makedirs(args.out_dir, exist_ok=True)
    record = {"look_name": look.get("name"), "look_id": look.get("id"), "group_id": look_group(look), "avatar_type": look.get("avatar_type"),
              "supported_api_engines": look.get("supported_api_engines"), "engine": engine,
              "voice_name": voice.get("name"), "voice_id": voice.get("voice_id"),
              "words": len(req["text"].split()), "estimated_cost_usd": cost,
              "balance_before": api_balance(client)}
    if args.dry_run:
        record["dry_run"] = True
        with open(os.path.join(args.out_dir, "sample.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        print("Dry run: nothing rendered or charged.")
        return 0

    payload = {"type": "avatar", "avatar_id": look["id"], "voice_id": voice["voice_id"], "script": req["text"],
               "title": f"Sample: {req['look_name']}", "resolution": config["heygen"]["resolution"],
               "aspect_ratio": config["heygen"]["aspect_ratio"], "engine": {"type": engine}}
    key = "ccw-sample-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:40]
    video_id = client.create_video(payload, key)
    print(f"HeyGen video id: {video_id}")
    data = client.wait_for_video(video_id, poll_seconds=limits["poll_seconds"], timeout_minutes=20)
    download(data["video_url"], os.path.join(args.out_dir, "sample.mp4"))
    record.update(video_id=video_id, duration_seconds=data.get("duration"), balance_after=api_balance(client))
    with open(os.path.join(args.out_dir, "sample.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HeyGenError as err:
        print(f"::error::{err}")
        sys.exit(1)
