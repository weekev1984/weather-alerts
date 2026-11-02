import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_warnings as cw  # noqa: E402


def item(title, desc="Some disruption likely", date="Mon, 02 Nov 2026 10:00:00 GMT"):
    return (f"<item><title>{title}</title>"
            f"<link>https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings</link>"
            f"<description>{desc}</description><pubDate>{date}</pubDate></item>")


def feed(*items):
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>'
            + "".join(items) + "</channel></rss>").encode("utf-8")


WIND_NORTH = item("Yellow warning of wind affecting Highlands &amp; Eilean Siar, Grampian",
                  "Gusts of 70 mph – power cuts possible")
RAIN_NE = item("Yellow warning of rain affecting North East England")
SNOW_SE = item("Amber warning of snow affecting London &amp; South East England")


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STATE_FILE"] = os.path.join(self.tmp.name, "state.json")
        os.environ["REGIONS"] = "UK"
        os.environ.pop("PRIORITY_REGIONS", None)
        self.sent = []
        self.now = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, feeds, now=None):
        """feeds: dict of region code -> feed bytes (missing regions are empty, Exception raises)."""
        def fetcher(url):
            v = feeds.get(url.rsplit("/", 1)[1], feed())
            if isinstance(v, Exception):
                raise v
            return v
        return cw.run(now=now or self.now, fetcher=fetcher,
                      sender=lambda w, pr: self.sent.append((w["title"], sorted(pr))))

    def test_only_new_warnings_notify_once_across_feeds(self):
        feeds = {"UK": feed(WIND_NORTH, RAIN_NE), "he": feed(WIND_NORTH), "gr": feed(WIND_NORTH)}
        self.go(feeds)
        self.assertEqual(len(self.sent), 2)
        self.go(feeds)
        self.assertEqual(len(self.sent), 2)
        feeds["UK"] = feed(WIND_NORTH, RAIN_NE, SNOW_SE)
        feeds["se"] = feed(SNOW_SE)
        self.go(feeds)
        self.assertEqual(self.sent[-1], ("Amber warning of snow affecting London & South East England", ["se"]))

    def test_priority_area_flagged(self):
        self.go({"UK": feed(WIND_NORTH, RAIN_NE), "he": feed(WIND_NORTH), "gr": feed(WIND_NORTH)})
        sent = dict(self.sent)
        self.assertEqual(sent["Yellow warning of wind affecting Highlands & Eilean Siar, Grampian"], ["gr", "he"])
        self.assertEqual(sent["Yellow warning of rain affecting North East England"], [])

    def test_priority_from_title_when_regional_feed_down(self):
        self.go({"UK": feed(WIND_NORTH), "he": OSError("down"), "gr": OSError("down")})
        self.assertEqual(self.sent[0][1], ["gr", "he"])

    def test_regional_only_item_still_sent(self):
        self.go({"UK": OSError("down"), "se": feed(SNOW_SE)})
        self.assertEqual(self.sent, [("Amber warning of snow affecting London & South East England", ["se"])])

    def test_expired_warning_forgotten(self):
        self.go({"UK": feed(RAIN_NE)})
        self.go({})
        with open(os.environ["STATE_FILE"]) as f:
            self.assertEqual(json.load(f)["seen"], {})

    def test_feed_error_does_not_realert(self):
        self.go({"UK": feed(RAIN_NE)})
        self.go({"UK": OSError("down")})
        self.go({"UK": feed(RAIN_NE)})
        self.assertEqual(len(self.sent), 1)

    def test_all_feeds_down_fails(self):
        def boom(url):
            raise OSError("down")
        self.assertEqual(cw.run(now=self.now, fetcher=boom, sender=lambda w, r: None), 1)

    def test_failed_send_retried(self):
        def bad(w, r):
            raise OSError("ntfy down")
        cw.run(now=self.now, fetcher=lambda u: feed(RAIN_NE), sender=bad)
        self.go({"UK": feed(RAIN_NE)})
        self.assertEqual(len(self.sent), 1)

    def test_keepalive_rewrites_state(self):
        self.go({})
        path = os.environ["STATE_FILE"]
        first = json.load(open(path))["last_written"]
        self.go({}, now=self.now + dt.timedelta(days=1))
        self.assertEqual(json.load(open(path))["last_written"], first)
        self.go({}, now=self.now + dt.timedelta(days=31))
        self.assertNotEqual(json.load(open(path))["last_written"], first)

    def test_priority_levels(self):
        self.assertEqual(cw.priority_for("Yellow warning of snow", True), 4)
        self.assertEqual(cw.priority_for("Yellow warning of snow", False), 3)
        self.assertEqual(cw.priority_for("Amber warning of wind", False), 4)
        self.assertEqual(cw.priority_for("Amber warning of wind", True), 5)
        self.assertEqual(cw.priority_for("Red warning of wind", False), 5)
        self.assertEqual(cw.warning_colour("Yellow warning of rain affecting Hereford"), "yellow")
        self.assertIsNone(cw.warning_colour("Flood notice covered elsewhere"))


if __name__ == "__main__":
    unittest.main()
