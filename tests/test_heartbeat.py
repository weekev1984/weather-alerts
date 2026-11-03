import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_warnings as cw  # noqa: E402
import heartbeat  # noqa: E402

NOW = dt.datetime(2026, 11, 3, 8, 7, tzinfo=dt.timezone.utc)


class HeartbeatTests(unittest.TestCase):
    def test_sends_summary_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "summary.txt")
            with mock.patch.dict(os.environ, {"RUN_SUMMARY_FILE": path}, clear=False):
                os.environ.pop("HEARTBEAT", None)
                cw.note_summary("Met Office: 2 active warning(s)")
                cw.note_summary("Storm outlook: highest 10%")
                sent = []
                self.assertEqual(heartbeat.run(NOW, poster=sent.append), 0)
        self.assertEqual(len(sent), 1)
        p = sent[0]
        self.assertEqual(p["priority"], 2)
        self.assertIn("Weather check ran", p["title"])
        self.assertIn("2 active warning(s)", p["message"])
        self.assertIn("highest 10%", p["message"])

    def test_off_sends_nothing(self):
        for value in ("off", "OFF", "false", "0"):
            with mock.patch.dict(os.environ, {"HEARTBEAT": value}):
                sent = []
                heartbeat.run(NOW, poster=sent.append)
                self.assertEqual(sent, [], value)

    def test_next_scheduled(self):
        self.assertEqual(heartbeat.next_scheduled(NOW), NOW.replace(hour=12, minute=7))
        late = NOW.replace(hour=11, minute=59)
        self.assertEqual(heartbeat.next_scheduled(late), NOW.replace(hour=12, minute=7))
        night = NOW.replace(hour=22, minute=30)
        self.assertEqual(heartbeat.next_scheduled(night),
                         dt.datetime(2026, 11, 4, 0, 7, tzinfo=dt.timezone.utc))

    def test_missing_summary_still_sends(self):
        p = heartbeat.build_payload(NOW, [])
        self.assertIn("No summary recorded", p["message"])

    def test_note_summary_without_file_is_noop(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUN_SUMMARY_FILE", None)
            cw.note_summary("ignored")  # must not raise


if __name__ == "__main__":
    unittest.main()
