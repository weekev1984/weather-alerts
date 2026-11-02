import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import storm_outlook as so  # noqa: E402

NOW = dt.datetime(2026, 11, 2, 8, tzinfo=dt.timezone.utc)
DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
TIMES = [f"{d}T{h:02d}:00" for d in DAYS for h in range(24)]


def forecast(storms=None, members=10):
    """Fake Open-Meteo response. storms: {(place, day): number of members with 90 mph gusts}."""
    storms = storms or {}
    names = [loc[0] for loc in so.LOCATIONS]
    out = []
    for name in names:
        hourly = {"time": TIMES}
        for m in range(members):
            series = [20.0] * len(TIMES)
            for (place, day), n in storms.items():
                if place == name and m < n:
                    series[DAYS.index(day) * 24 + 15] = 90.0
            key = "wind_gusts_10m" if m == 0 else f"wind_gusts_10m_member{m:02d}"
            hourly[key] = series
        out.append({"utc_offset_seconds": 0, "hourly": hourly})
    return json.dumps(out).encode()


class StormOutlookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PRELIM_STATE_FILE"] = os.path.join(self.tmp.name, "storm_state.json")
        os.environ["ENSEMBLE_MODELS"] = "ecmwf_ifs025"
        for k in ("PRELIM_PROBABILITY", "PRELIM_MIN_LEAD_HOURS", "PRELIM_DAYS"):
            os.environ.pop(k, None)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, body, now=NOW):
        return so.run(now=now, fetcher=lambda url: body, poster=self.sent.append)

    def test_quiet_weather_sends_nothing(self):
        self.assertEqual(self.go(forecast()), 0)
        self.assertEqual(self.sent, [])

    def test_below_threshold_chance_sends_nothing(self):
        self.go(forecast({("Inverness", "2026-11-05"): 2}))  # 20%
        self.assertEqual(self.sent, [])

    def test_likely_storm_in_ssen_area_is_labelled_preliminary(self):
        self.go(forecast({("Inverness", "2026-11-05"): 4, ("Glasgow", "2026-11-05"): 3}))
        self.assertEqual(len(self.sent), 1)
        p = self.sent[0]
        self.assertEqual(p["title"], "SSEN AREA PRELIMINARY: possible storm Thu 5 Nov")
        self.assertIn("40% chance", p["message"])
        self.assertIn("Inverness", p["message"])
        self.assertIn("Glasgow", p["message"])
        self.assertIn("Not a Met Office warning", p["message"])
        self.assertEqual(p["priority"], 4)

    def test_storm_elsewhere_is_normal_priority(self):
        self.go(forecast({("Cardiff", "2026-11-04"): 3}))
        self.assertEqual(self.sent[0]["title"], "PRELIMINARY: possible storm Wed 4 Nov")
        self.assertEqual(self.sent[0]["priority"], 3)

    def test_same_storm_is_not_sent_twice_even_if_it_shifts_a_day(self):
        self.go(forecast({("Inverness", "2026-11-05"): 4}))
        self.go(forecast({("Inverness", "2026-11-05"): 4}))
        self.go(forecast({("Inverness", "2026-11-06"): 3}))
        self.assertEqual(len(self.sent), 1)

    def test_storm_getting_more_likely_sends_an_update(self):
        self.go(forecast({("Cardiff", "2026-11-05"): 3}))
        self.go(forecast({("Cardiff", "2026-11-05"): 4}))  # still 30-49% band
        self.go(forecast({("Cardiff", "2026-11-05"): 6}))  # now 50%+
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1]["title"].startswith("UPDATE PRELIMINARY"))
        self.go(forecast({("Cardiff", "2026-11-05"): 4}))  # dropping back doesn't re-alert
        self.assertEqual(len(self.sent), 2)

    def test_storm_spreading_to_ssen_area_sends_an_update(self):
        self.go(forecast({("Cardiff", "2026-11-05"): 3}))
        self.go(forecast({("Cardiff", "2026-11-05"): 3, ("Reading", "2026-11-05"): 3}))
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1]["title"].startswith("SSEN AREA UPDATE"))

    def test_separate_storms_alert_separately(self):
        self.go(forecast({("Lerwick", "2026-11-03"): 5, ("Lerwick", "2026-11-06"): 5}))
        self.assertEqual(len(self.sent), 2)

    def test_next_few_hours_are_ignored(self):
        self.go(forecast({("Inverness", "2026-11-02"): 8}))  # storm at 15:00, 7h away
        self.assertEqual(self.sent, [])

    def test_old_storms_are_forgotten(self):
        self.go(forecast({("Inverness", "2026-11-04"): 4}))
        later = NOW + dt.timedelta(days=4)
        self.go(forecast(), now=later)
        with open(os.environ["PRELIM_STATE_FILE"]) as f:
            self.assertEqual(json.load(f)["storms"], [])

    def test_bad_forecast_fails_without_sending(self):
        self.assertEqual(self.go(b"{}"), 1)
        self.assertEqual(self.sent, [])

    def test_models_are_combined(self):
        os.environ["ENSEMBLE_MODELS"] = "ecmwf_ifs025,gfs025"
        bodies = {"ecmwf_ifs025": forecast({("Oxford", "2026-11-05"): 4}), "gfs025": forecast()}
        so.run(now=NOW, fetcher=lambda url: bodies[url.split("models=")[1].split("&")[0]],
               poster=self.sent.append)
        self.assertEqual(self.sent, [])  # 4 of 20 members = 20%, below 30%
        bodies["gfs025"] = forecast({("Oxford", "2026-11-05"): 4})
        so.run(now=NOW, fetcher=lambda url: bodies[url.split("models=")[1].split("&")[0]],
               poster=self.sent.append)
        self.assertEqual(len(self.sent), 1)  # 8 of 20 = 40%


if __name__ == "__main__":
    unittest.main()
