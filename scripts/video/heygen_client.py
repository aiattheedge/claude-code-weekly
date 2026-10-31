"""Minimal HeyGen v3 API client (stdlib only).

Endpoints, as documented at developers.heygen.com:
    GET  /v3/avatars/looks   list looks (avatar_type, ownership, limit, token)
    POST /v3/videos          create an avatar video (Idempotency-Key supported)
    GET  /v3/videos/{id}     video status: pending, processing, completed, failed
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.heygen.com"
RETRY_STATUS = {429, 500, 502, 503, 504}


class HeyGenError(RuntimeError):
    pass


class HeyGenClient:
    def __init__(self, api_key: str, base_url: str = None, retries: int = 4):
        if not api_key:
            raise HeyGenError("HEYGEN_API_KEY is not set.")
        self._key = api_key
        self._base = (base_url or BASE_URL).rstrip("/")
        self._retries = retries

    def _request(self, method, path, *, query=None, body=None, headers=None):
        url = self._base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req_headers = {"x-api-key": self._key, "Accept": "application/json",
                       "User-Agent": "claude-code-weekly-video/1.0"}
        if data is not None:
            req_headers["Content-Type"] = "application/json"
        req_headers.update(headers or {})
        for attempt in range(self._retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as err:
                detail = err.read().decode(errors="replace")[:500]
                if err.code in RETRY_STATUS and attempt < self._retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise HeyGenError(f"{method} {path} failed with HTTP {err.code}: {detail}") from err
            except urllib.error.URLError as err:
                if attempt < self._retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise HeyGenError(f"{method} {path} failed: {err.reason}") from err
        raise HeyGenError(f"{method} {path} failed after retries.")

    def list_twin_looks(self):
        looks, token = [], None
        while True:
            page = self._request("GET", "/v3/avatars/looks", query={
                "avatar_type": "digital_twin", "ownership": "private", "limit": 50, "token": token})
            looks.extend(page.get("data") or [])
            token = page.get("next_token")
            if not page.get("has_more") or not token:
                return looks

    def create_video(self, payload: dict, idempotency_key: str) -> str:
        resp = self._request("POST", "/v3/videos", body=payload,
                             headers={"Idempotency-Key": idempotency_key})
        video_id = (resp.get("data") or {}).get("video_id")
        if not video_id:
            raise HeyGenError(f"Create video returned no video_id: {json.dumps(resp)[:500]}")
        return video_id

    def get_video(self, video_id: str) -> dict:
        return self._request("GET", f"/v3/videos/{urllib.parse.quote(video_id)}").get("data") or {}

    def wait_for_video(self, video_id: str, *, poll_seconds: int, timeout_minutes: int, log=print) -> dict:
        deadline = time.monotonic() + timeout_minutes * 60
        last = None
        while time.monotonic() < deadline:
            data = self.get_video(video_id)
            status = data.get("status")
            if status != last:
                log(f"HeyGen status: {status}")
                last = status
            if status == "completed":
                return data
            if status == "failed":
                raise HeyGenError(f"Render failed: {data.get('failure_code')} {data.get('failure_message')}")
            time.sleep(poll_seconds)
        raise HeyGenError(f"Render not finished after {timeout_minutes} minutes. "
                          "Re-run within 24 hours to pick up the same video without a new charge.")


def download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "claude-code-weekly-video/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
