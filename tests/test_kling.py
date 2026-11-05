"""Tests for the Kling walk-in step. Run: python3 -m unittest discover -s tests -v

The end-to-end test runs kling_walkin.py against a local fake of the fal queue API,
so it needs ffmpeg but never calls fal or spends credit.
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "video"))

import kling_walkin  # noqa: E402

SETTINGS = kling_walkin.load_settings()
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _make_jpeg(path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x480",
                    "-frames:v", "1", path], check=True)


class SettingsTests(unittest.TestCase):
    def test_config_is_silent_and_capped(self):
        self.assertFalse(SETTINGS["generate_audio"])
        self.assertLessEqual(kling_walkin.estimate_cost(SETTINGS, "10"), SETTINGS["max_cost_usd"])

    def test_cost(self):
        self.assertEqual(kling_walkin.estimate_cost(SETTINGS, "5"), 0.35)
        self.assertEqual(kling_walkin.estimate_cost({**SETTINGS, "generate_audio": True}, "5"), 0.70)

    FRAMES = {"photo": "data:photo", "empty": "data:empty"}

    def test_walk_in_starts_empty_and_ends_on_photo(self):
        args = kling_walkin.build_arguments(SETTINGS, self.FRAMES, style="walk_in")
        self.assertEqual((args["start_image_url"], args["end_image_url"]), ("data:empty", "data:photo"))
        self.assertEqual(args["duration"], "5")
        self.assertIs(args["generate_audio"], False)

    def test_walk_up_starts_on_photo(self):
        args = kling_walkin.build_arguments(SETTINGS, self.FRAMES, style="walk_up")
        self.assertEqual(args["start_image_url"], "data:photo")
        self.assertNotIn("end_image_url", args)

    def test_prompt_override_and_bad_values(self):
        args = kling_walkin.build_arguments(SETTINGS, self.FRAMES, prompt=" Wave. ", duration="10")
        self.assertEqual((args["prompt"], args["duration"]), ("Wave.", "10"))
        with self.assertRaises(kling_walkin.WalkinError):
            kling_walkin.build_arguments(SETTINGS, self.FRAMES, duration="7")
        with self.assertRaises(kling_walkin.WalkinError):
            kling_walkin.build_arguments(SETTINGS, self.FRAMES, style="dance")

    def test_photo_type_checked(self):
        with tempfile.NamedTemporaryFile(suffix=".gif") as f:
            with self.assertRaises(kling_walkin.WalkinError):
                kling_walkin.photo_data_uri(f.name)


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class FrameTests(unittest.TestCase):
    def test_frames_are_1080p_for_tall_and_wide_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            for size in ("320x480", "1000x400"):
                photo = os.path.join(tmp, f"p{size}.jpg")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"color=c=gray:s={size}", "-frames:v", "1", photo], check=True)
                frames = kling_walkin.prepare_frames(photo, os.path.join(tmp, size))
                for path in frames.values():
                    info = kling_walkin.probe(path)
                    self.assertEqual((info["width"], info["height"]), (1920, 1080), path)


class _FakeFal(http.server.BaseHTTPRequestHandler):
    calls = []
    video_path = ""

    def log_message(self, *a):
        pass

    def _json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append(("POST", self.path, self.headers.get("Authorization"), body))
        base = f"http://127.0.0.1:{self.server.server_port}"
        self._json({"request_id": "req1", "status_url": f"{base}/status", "response_url": f"{base}/result"})

    def do_GET(self):
        self.calls.append(("GET", self.path))
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/status":
            self._json({"status": "COMPLETED"})
        elif self.path == "/result":
            self._json({"video": {"url": f"{base}/video.mp4"}})
        else:
            with open(self.video_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            self.wfile.write(data)


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.photo = os.path.join(self.tmp, "ian.jpg")
        _make_jpeg(self.photo)
        _FakeFal.video_path = os.path.join(self.tmp, "fake.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=s=640x360:r=24:d=2", "-pix_fmt", "yuv420p", _FakeFal.video_path], check=True)
        _FakeFal.calls = []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _FakeFal)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self.tmp)

    def _run(self, *extra):
        env = {**os.environ, "FAL_KEY": "test-key",
               "FAL_QUEUE_URL": f"http://127.0.0.1:{self.server.server_port}"}
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "video", "kling_walkin.py"),
                               self.photo, os.path.join(self.tmp, "out"), *extra],
                              env=env, capture_output=True, text=True)

    def test_dry_run_never_calls_fal(self):
        res = self._run("--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("$0.35", res.stdout)
        self.assertEqual(_FakeFal.calls, [])

    def test_render_saves_labelled_clip(self):
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        post = _FakeFal.calls[0]
        self.assertEqual(post[1], "/" + SETTINGS["endpoint"])
        self.assertEqual(post[2], "Key test-key")
        self.assertTrue(post[3]["start_image_url"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(post[3]["end_image_url"].startswith("data:image/jpeg;base64,"))
        self.assertIs(post[3]["generate_audio"], False)
        out = os.path.join(self.tmp, "out")
        for name in ("walkin-raw.mp4", "walkin.mp4", "walkin.json"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)
        record = json.load(open(os.path.join(out, "walkin.json")))
        self.assertNotIn("start_image_url", record)
        self.assertNotIn("end_image_url", record)
        self.assertEqual(record["style"], "walk_in")
        self.assertEqual(record["request_id"], "req1")


if __name__ == "__main__":
    unittest.main()
