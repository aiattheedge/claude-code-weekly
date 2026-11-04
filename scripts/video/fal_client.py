"""Minimal fal.ai queue client (stdlib only), used for Kling clips.

fal queue REST flow (docs.fal.ai, "Queue"):
    POST https://queue.fal.run/{endpoint}        submit; returns request_id, status_url, response_url
    GET  {status_url}                            IN_QUEUE, IN_PROGRESS or COMPLETED
    GET  {response_url}                          the model's output once COMPLETED
Auth header: "Authorization: Key <FAL_KEY>".
"""
import json
import time
import urllib.error
import urllib.request

QUEUE_URL = "https://queue.fal.run"
RETRY_STATUS = {429, 500, 502, 503, 504}


class FalError(RuntimeError):
    pass


class FalClient:
    def __init__(self, api_key: str, base_url: str = None, retries: int = 4):
        if not api_key:
            raise FalError("FAL_KEY is not set.")
        self._key = api_key
        self._base = (base_url or QUEUE_URL).rstrip("/")
        self._retries = retries

    def _request(self, method: str, url: str, body=None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Key {self._key}", "Accept": "application/json",
                   "User-Agent": "claude-code-weekly-video/1.0"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(self._retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as err:
                detail = err.read().decode(errors="replace")[:500]
                # Never retry a submit: a retried POST could be billed twice.
                if method == "GET" and err.code in RETRY_STATUS and attempt < self._retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise FalError(f"{method} {url} failed with HTTP {err.code}: {detail}") from err
            except urllib.error.URLError as err:
                if method == "GET" and attempt < self._retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise FalError(f"{method} {url} failed: {err.reason}") from err
        raise FalError(f"{method} {url} failed after retries.")

    def submit(self, endpoint: str, arguments: dict) -> dict:
        job = self._request("POST", f"{self._base}/{endpoint.strip('/')}", arguments)
        if not job.get("request_id"):
            raise FalError(f"fal returned no request_id: {json.dumps(job)[:300]}")
        return job

    def wait(self, job: dict, *, poll_seconds: int = 10, timeout_minutes: int = 20) -> dict:
        deadline = time.monotonic() + timeout_minutes * 60
        while True:
            status = self._request("GET", job["status_url"])
            state = status.get("status")
            if state == "COMPLETED":
                if status.get("error"):
                    raise FalError(f"fal job {job['request_id']} failed: {status['error']}")
                return self._request("GET", job["response_url"])
            if state not in ("IN_QUEUE", "IN_PROGRESS"):
                raise FalError(f"fal job {job['request_id']} has unexpected status: {json.dumps(status)[:300]}")
            if time.monotonic() > deadline:
                raise FalError(f"fal job {job['request_id']} still {state} after {timeout_minutes} min.")
            time.sleep(poll_seconds)

    @staticmethod
    def download(url: str, path: str) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "claude-code-weekly-video/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
