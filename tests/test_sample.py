import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "video"))
import heygen_sample as hs  # noqa: E402
from heygen_client import HeyGenError  # noqa: E402

LOOKS = [{"name": "Ian Michel", "id": "a", "supported_api_engines": ["avatar_iv"]},
         {"name": "Professional in blue blazer", "id": "b", "supported_api_engines": ["avatar_v", "avatar_iv"],
          "default_voice_id": "v2"}]


class SampleTest(unittest.TestCase):
    def test_find_look_exact_name(self):
        self.assertEqual(hs.find_look(LOOKS, "professional in blue blazer")["id"], "b")
        with self.assertRaises(HeyGenError):
            hs.find_look(LOOKS, "blazer")

    def test_pick_engine_in_request_order(self):
        self.assertEqual(hs.pick_engine(LOOKS[1], ["avatar_iv", "avatar_v"]), "avatar_iv")
        with self.assertRaises(HeyGenError):
            hs.pick_engine(LOOKS[0], ["avatar_v"])

    def test_find_voice_prefers_look_default_when_names_repeat(self):
        voices = [{"name": "Ian Michel", "voice_id": "v1"}, {"name": "Ian Michel", "voice_id": "v2"}]
        self.assertEqual(hs.find_voice(voices, "Ian Michel", "v2")["voice_id"], "v2")
        with self.assertRaises(HeyGenError):
            hs.find_voice(voices, "Ian Michel")

    def test_estimate_cost(self):
        self.assertEqual(hs.estimate_cost(" ".join(["w"] * 30), "avatar_iv", {"avatar_iv": 4.83}, 150), 0.97)

    def test_sample_request_is_under_cap(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        req = hs.load_request(os.path.join(root, "video", "samples", "blazer-look-2026-11-14.json"))
        for engine, price in {"avatar_iv": 4.83, "avatar_v": 7.20}.items():
            self.assertLessEqual(hs.estimate_cost(req["text"], engine, {engine: price}, 150), req["max_cost_usd"])


if __name__ == "__main__":
    unittest.main()
