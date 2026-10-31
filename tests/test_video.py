"""Tests for the HeyGen video step. Run: python3 -m unittest discover -s tests -v

The end-to-end test runs make_video.py against a local fake of the HeyGen API,
so it needs ffmpeg but never calls HeyGen or spends credits.
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
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "video"))

import compose  # noqa: E402
import heygen_client  # noqa: E402
import make_video  # noqa: E402
import script_file  # noqa: E402

SAMPLE = os.path.join(ROOT, "video", "samples", "script-2026-11-02-duet.md")
GOOD = "---\ntitle: Test\ndate: 2026-11-02\n---\n# Label\nThis is an AI-generated video.\n\n<!-- note -->\nSecond paragraph.\n"


class ScriptFileTests(unittest.TestCase):
    def test_parse_drops_labels_and_comments(self):
        s = script_file.parse(GOOD)
        self.assertEqual(s.spoken_text, "This is an AI-generated video.\n\nSecond paragraph.")
        self.assertEqual((s.title, s.date), ("Test", "2026-11-02"))

    def test_overrides(self):
        s = script_file.parse(GOOD.replace("date:", "engine: avatar_iv\ndate:"))
        self.assertEqual(s.overrides, {"engine": "avatar_iv"})

    def test_rejects_bad_front_matter(self):
        for text in ("no front matter", "---\ntitle: x\n---\nAI words", GOOD.replace("2026-11-02", "Nov 2")):
            with self.assertRaises(script_file.ScriptError):
                script_file.parse(text)

    def test_checks(self):
        s = script_file.parse(GOOD)
        script_file.check(s, max_words=50, disclosure_words=10)
        with self.assertRaises(script_file.ScriptError):
            script_file.check(s, max_words=3, disclosure_words=10)
        with self.assertRaises(script_file.ScriptError):
            script_file.check(script_file.parse(GOOD.replace("AI-generated", "made")), max_words=50, disclosure_words=10)
        with self.assertRaises(script_file.ScriptError):
            script_file.check(s, max_words=50, disclosure_words=10, blocked_terms=["second"])

    def test_sample_passes(self):
        with open(SAMPLE, encoding="utf-8") as f:
            s = script_file.parse(f.read())
        cfg = make_video.load_config()
        script_file.check(s, max_words=cfg["limits"]["max_words"],
                          disclosure_words=cfg["disclosure"]["require_ai_in_first_words"])
        self.assertTrue(500 <= s.word_count <= 700, s.word_count)


class LookTests(unittest.TestCase):
    LOOKS = [{"id": "a", "name": "Old", "status": "failed", "supported_api_engines": ["avatar_v"]},
             {"id": "b", "name": "Ian office", "status": "completed", "supported_api_engines": ["avatar_iv"]},
             {"id": "c", "name": "Ian studio", "status": "completed", "supported_api_engines": ["avatar_iv", "avatar_v"]}]

    def test_picks_first_finished_look_with_engine(self):
        self.assertEqual(make_video.choose_look(self.LOOKS, engine="avatar_v")["id"], "c")

    def test_by_name_and_id(self):
        self.assertEqual(make_video.choose_look(self.LOOKS, engine="avatar_iv", avatar_name="office")["id"], "b")
        with self.assertRaises(heygen_client.HeyGenError):
            make_video.choose_look(self.LOOKS, engine="avatar_v", avatar_id="b")

    def test_unknown_name_lists_looks(self):
        with self.assertRaisesRegex(heygen_client.HeyGenError, "Ian studio"):
            make_video.choose_look(self.LOOKS, engine="avatar_v", avatar_name="beach")

    def test_none(self):
        with self.assertRaises(heygen_client.HeyGenError):
            make_video.choose_look([], engine="avatar_v")


class CaptionTests(unittest.TestCase):
    def test_estimate_srt(self):
        srt = compose.estimate_srt("one two three four five", 10.0, words_per_cue=2)
        self.assertIn("1\n00:00:00,000 --> 00:00:04,000\none two", srt)
        self.assertIn("3\n00:00:08,000 --> 00:00:10,000\nfive", srt)


def _fake_video(path, seconds=3):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=640x360:r=25",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000", "-t", str(seconds), "-ac", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path], check=True)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _fake_video(os.path.join(self.tmp, "raw.mp4"))
        with open(os.path.join(self.tmp, "subs.srt"), "w") as f:
            f.write("1\n00:00:00,000 --> 00:00:02,000\nThis is an AI-generated video.\n")
        self.requests = []
        tmp, log = self.tmp, self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                log.append(("GET", self.path, self.headers.get("x-api-key")))
                base = f"http://127.0.0.1:{self.server.server_port}"
                if self.path.startswith("/v3/avatars/looks"):
                    self._json({"data": [{"id": "look1", "name": "Ian", "status": "completed",
                                          "default_voice_id": "voice1",
                                          "supported_api_engines": ["avatar_iv", "avatar_v"]}],
                                "has_more": False})
                elif self.path == "/v3/videos/vid1":
                    self._json({"data": {"status": "completed", "video_url": base + "/files/raw.mp4",
                                         "subtitle_url": base + "/files/subs.srt", "duration": 3}})
                elif self.path.startswith("/files/"):
                    with open(os.path.join(tmp, self.path.split("/")[-1]), "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                log.append(("POST", self.path, body, self.headers.get("Idempotency-Key")))
                self._json({"data": {"video_id": "vid1", "status": "waiting"}})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def _run(self, *args):
        env = {"HEYGEN_API_KEY": "test-key", "VIDEO_OUT_DIR": self.tmp}
        # Use the real config, minus the chosen look, so these tests don't depend on Ian's HeyGen account.
        config = make_video.load_config()
        config["heygen"].update(avatar_id="", avatar_name="")
        with mock.patch.dict(os.environ, env), mock.patch.object(heygen_client, "BASE_URL", self.base), \
                mock.patch.object(make_video, "load_config", return_value=config):
            return make_video.main([SAMPLE, *args])

    def test_dry_run_never_creates_a_video(self):
        self.assertEqual(self._run("--dry-run"), 0)
        self.assertFalse([r for r in self.requests if r[0] == "POST"])

    def test_render_and_finish(self):
        self.assertEqual(self._run(), 0)
        post = next(r for r in self.requests if r[0] == "POST")
        self.assertEqual(post[2]["engine"], {"type": "avatar_v"})
        self.assertEqual((post[2]["avatar_id"], post[2]["voice_id"]), ("look1", "voice1"))
        self.assertTrue(post[3].startswith("ccw-video-"))
        final = os.path.join(self.tmp, "2026-11-02", "claude-code-latest-2026-11-02.mp4")
        info = compose.probe(final)
        self.assertAlmostEqual(info["duration"], 3 + 5, delta=0.5)  # render + end card
        self.assertEqual((info["width"], info["height"]), (640, 360))


if __name__ == "__main__":
    unittest.main()
