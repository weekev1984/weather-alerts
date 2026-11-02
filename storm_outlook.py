#!/usr/bin/env python3
"""Preliminary storm alerts: a heads-up days before any Met Office warning.

Every run it downloads ensemble forecasts of wind gusts from Open-Meteo (free,
no API key) for a set of UK locations, weighted towards SSEN's areas. An
ensemble is ~50 slightly different runs of the same weather model, so the
share of runs ("members") that bring damaging gusts to a place is a rough
probability. When that probability reaches PRELIM_PROBABILITY anywhere, a
clearly labelled PRELIMINARY notification is sent.

Each storm is sent once. It is sent again only if it gets markedly more likely
(crosses the next band: 50%, then 70%) or newly reaches an SSEN area. Storms
whose dates shift by a day between runs count as the same storm.

Configure with environment variables (plus NTFY_* / DRY_RUN as for check_warnings.py):

  PRELIM_PROBABILITY   minimum chance, in percent, to alert (default 30)
  PRELIM_MIN_LEAD_HOURS  ignore the next N hours, which formal warnings cover (default 12)
  PRELIM_DAYS          how many days ahead to look (default 10)
  ENSEMBLE_MODELS      Open-Meteo ensemble models (default "ecmwf_ifs025,gfs025")
  PRELIM_STATE_FILE    where sent storms are remembered (default "storm_state.json")
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.parse

import check_warnings as cw

API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
MORE_INFO_URL = "https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings"

SSEN_NORTH = "SSEN north of Scotland"
SSEN_SOUTH = "SSEN central southern England"
REST_OF_UK = "Rest of UK"

# (name, area, latitude, longitude, gust threshold in mph)
# Thresholds roughly follow the gusts in Met Office yellow wind warnings:
# ~55 mph inland, 60-65 mph on coasts, 70+ mph in the very exposed north and
# west where gusts like that are routine in winter.
LOCATIONS = [
    ("Lerwick", SSEN_NORTH, 60.15, -1.15, 75),
    ("Kirkwall", SSEN_NORTH, 58.98, -2.96, 70),
    ("Stornoway", SSEN_NORTH, 58.21, -6.39, 70),
    ("Wick", SSEN_NORTH, 58.44, -3.09, 70),
    ("Inverness", SSEN_NORTH, 57.48, -4.22, 60),
    ("Aberdeen", SSEN_NORTH, 57.15, -2.09, 60),
    ("Fort William", SSEN_NORTH, 56.82, -5.11, 60),
    ("Oban", SSEN_NORTH, 56.41, -5.47, 65),
    ("Perth", SSEN_NORTH, 56.40, -3.43, 55),
    ("Reading", SSEN_SOUTH, 51.45, -0.97, 55),
    ("Oxford", SSEN_SOUTH, 51.75, -1.26, 55),
    ("Salisbury", SSEN_SOUTH, 51.07, -1.79, 55),
    ("Portsmouth", SSEN_SOUTH, 50.80, -1.09, 60),
    ("Bournemouth", SSEN_SOUTH, 50.72, -1.88, 60),
    ("Isle of Wight", SSEN_SOUTH, 50.67, -1.30, 65),
    ("Belfast", REST_OF_UK, 54.60, -5.93, 60),
    ("Glasgow", REST_OF_UK, 55.86, -4.25, 55),
    ("Edinburgh", REST_OF_UK, 55.95, -3.19, 55),
    ("Newcastle", REST_OF_UK, 54.98, -1.61, 55),
    ("Manchester", REST_OF_UK, 53.48, -2.24, 55),
    ("Aberystwyth", REST_OF_UK, 52.41, -4.08, 65),
    ("Cardiff", REST_OF_UK, 51.48, -3.18, 55),
    ("Plymouth", REST_OF_UK, 50.37, -4.14, 60),
    ("Penzance", REST_OF_UK, 50.12, -5.54, 65),
    ("London", REST_OF_UK, 51.51, -0.13, 50),
    ("Norwich", REST_OF_UK, 52.63, 1.30, 55),
]
SSEN_AREAS = (SSEN_NORTH, SSEN_SOUTH)
BANDS = (30, 50, 70)  # re-alert when a storm climbs into a higher band


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def forecast_url(model: str, days: int) -> str:
    q = {
        "latitude": ",".join(str(loc[2]) for loc in LOCATIONS),
        "longitude": ",".join(str(loc[3]) for loc in LOCATIONS),
        "hourly": "wind_gusts_10m",
        "models": model,
        "forecast_days": days,
        "wind_speed_unit": "mph",
        "timezone": "Europe/London",
    }
    return API_URL + "?" + urllib.parse.urlencode(q)


def member_series(hourly: dict) -> list[list]:
    """Every ensemble member's hourly gusts (control run included)."""
    return [v for k, v in hourly.items() if k.startswith("wind_gusts_10m") and isinstance(v, list)]


def daily_chances(response: list | dict, now: dt.datetime, min_lead_hours: int) -> dict:
    """Return {(location index, "YYYY-MM-DD"): (members over threshold, members, peak gusts)}."""
    if isinstance(response, dict):
        response = [response]
    if len(response) != len(LOCATIONS):
        raise ValueError(f"expected {len(LOCATIONS)} locations, got {len(response)}")
    earliest = now + dt.timedelta(hours=min_lead_hours)
    out = {}
    for i, loc in enumerate(response):
        threshold = LOCATIONS[i][4]
        hourly = loc.get("hourly") or {}
        times = hourly.get("time") or []
        offset = dt.timedelta(seconds=loc.get("utc_offset_seconds", 0))
        members = member_series(hourly)
        if not members:
            raise ValueError("no wind_gusts_10m data in forecast")
        days: dict[str, list[int]] = {}  # date -> hour indexes
        for h, t in enumerate(times):
            local = dt.datetime.fromisoformat(t)
            if local.replace(tzinfo=dt.timezone.utc) - offset < earliest:
                continue
            days.setdefault(t[:10], []).append(h)
        for day, hours in days.items():
            peaks = []
            for series in members:
                vals = [series[h] for h in hours if h < len(series) and series[h] is not None]
                if vals:
                    peaks.append(max(vals))
            if peaks:
                out[(i, day)] = (sum(p >= threshold for p in peaks), len(peaks), peaks)
    if not out and response and any(response[0].get("hourly", {}).get("time") or []):
        raise ValueError("forecast has no gust values (model may not provide wind_gusts_10m)")
    return out


def assess(responses: list, now: dt.datetime, min_lead_hours: int) -> dict:
    """Combine models: {(location index, day): {"prob": %, "median": mph, "max": mph}}."""
    merged: dict = {}
    for resp in responses:
        for key, (hit, n, peaks) in daily_chances(resp, now, min_lead_hours).items():
            h0, n0, p0 = merged.get(key, (0, 0, []))
            merged[key] = (h0 + hit, n0 + n, p0 + peaks)
    result = {}
    for key, (hit, n, peaks) in merged.items():
        peaks = sorted(peaks)
        result[key] = {"prob": round(100 * hit / n), "median": round(peaks[len(peaks) // 2]),
                       "max": round(peaks[-1])}
    return result


def find_storms(chances: dict, min_prob: int) -> list[dict]:
    """Group days with a high chance of damaging gusts into storms (runs of consecutive days)."""
    flagged: dict[str, list] = {}
    for (i, day), c in chances.items():
        if c["prob"] >= min_prob:
            flagged.setdefault(day, []).append((i, c))
    storms: list[dict] = []
    for day in sorted(flagged):
        d = dt.date.fromisoformat(day)
        if storms and d - dt.date.fromisoformat(storms[-1]["end"]) <= dt.timedelta(days=1):
            storm = storms[-1]
            storm["end"] = day
        else:
            storm = {"start": day, "end": day, "places": {}}
            storms.append(storm)
        for i, c in flagged[day]:
            name = LOCATIONS[i][0]
            best = storm["places"].get(name)
            if best is None or c["prob"] > best["prob"]:
                storm["places"][name] = {**c, "day": day, "area": LOCATIONS[i][1],
                                         "threshold": LOCATIONS[i][4]}
    for s in storms:
        s["prob"] = max(p["prob"] for p in s["places"].values())
        s["areas"] = sorted({p["area"] for p in s["places"].values()},
                            key=[SSEN_NORTH, SSEN_SOUTH, REST_OF_UK].index)
    return storms


def band(prob: int) -> int:
    return max([b for b in BANDS if prob >= b], default=0)


def _overlaps(a: dict, b: dict, slack_days: int = 1) -> bool:
    slack = dt.timedelta(days=slack_days)
    a0, a1 = dt.date.fromisoformat(a["start"]), dt.date.fromisoformat(a["end"])
    b0, b1 = dt.date.fromisoformat(b["start"]), dt.date.fromisoformat(b["end"])
    return a0 <= b1 + slack and b0 <= a1 + slack


def _day_label(day: str) -> str:
    d = dt.date.fromisoformat(day)
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b")


def build_payload(storm: dict, update: bool) -> dict:
    when = _day_label(storm["start"])
    if storm["end"] != storm["start"]:
        when += " to " + _day_label(storm["end"])
    ssen = [a for a in storm["areas"] if a in SSEN_AREAS]
    title = f"PRELIMINARY: possible storm {when}"
    if update:
        title = "UPDATE " + title
    if ssen:
        title = "SSEN AREA " + title
    lines = [f"About a {storm['prob']}% chance of damaging gusts. Not a Met Office warning yet; "
             "forecasts this far ahead often change.", ""]
    for area in storm["areas"]:
        places = sorted(((n, p) for n, p in storm["places"].items() if p["area"] == area),
                        key=lambda np: -np[1]["prob"])
        lines.append(area + ":")
        for name, p in places[:5]:
            lines.append(f"  {name} {_day_label(p['day'])}: {p['prob']}% chance of "
                         f"{p['threshold']}+ mph gusts (median {p['median']}, highest {p['max']} mph)")
    lines += ["", "Source: ECMWF and GFS ensemble forecasts via Open-Meteo."]
    # Kept below the urgent level used for real amber/red warnings.
    priority = 4 if ssen or storm["prob"] >= 70 else 3
    return {
        "title": title,
        "message": "\n".join(lines)[:3500],
        "priority": priority,
        "tags": (["zap"] if ssen else []) + ["cyclone", "hourglass"],
        "click": MORE_INFO_URL,
    }


def run(now: dt.datetime | None = None, fetcher=cw.fetch, poster=cw.post_ntfy) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    min_prob = _env_int("PRELIM_PROBABILITY", 30)
    min_lead = _env_int("PRELIM_MIN_LEAD_HOURS", 12)
    days = max(1, min(_env_int("PRELIM_DAYS", 10), 15))
    models = cw._env_list("ENSEMBLE_MODELS", "ecmwf_ifs025,gfs025")
    state_path = os.environ.get("PRELIM_STATE_FILE", "storm_state.json")

    responses = []
    for model in models:
        try:
            resp = json.loads(fetcher(forecast_url(model, days)))
            daily_chances(resp, now, min_lead)  # validate before using it
            responses.append(resp)
            print(f"[{model}] forecast loaded")
        except Exception as exc:
            print(f"[{model}] could not use forecast: {exc}", file=sys.stderr)
    if not responses:
        print("No ensemble forecasts available, skipping storm outlook.", file=sys.stderr)
        return 1

    chances = assess(responses, now, min_lead)
    if chances:
        (i, day), top = max(chances.items(), key=lambda kv: kv[1]["prob"])
        print(f"Highest chance: {top['prob']}% of {LOCATIONS[i][4]}+ mph gusts at "
              f"{LOCATIONS[i][0]} on {day} (median {top['median']}, highest {top['max']} mph)")
    storms = find_storms(chances, min_prob)
    state = cw.load_state(state_path)
    today = now.date()
    known = [s for s in state.get("storms", [])
             if dt.date.fromisoformat(s["end"]) >= today - dt.timedelta(days=1)]
    sent = 0
    for storm in storms:
        print(f"Possible storm {storm['start']}..{storm['end']}: {storm['prob']}% "
              f"({', '.join(storm['areas'])})")
        match = next((k for k in known if _overlaps(k, storm)), None)
        new_ssen = bool(match) and any(a in SSEN_AREAS and a not in match["areas"]
                                       for a in storm["areas"])
        if match and band(storm["prob"]) <= match["band"] and not new_ssen:
            # Same storm, no meaningful change: keep tracking its dates quietly.
            match["start"], match["end"] = min(match["start"], storm["start"]), max(match["end"], storm["end"])
            continue
        try:
            poster(build_payload(storm, update=bool(match)))
            sent += 1
        except Exception as exc:
            print(f"failed to send notification: {exc}", file=sys.stderr)
            continue
        record = {"start": storm["start"], "end": storm["end"], "band": band(storm["prob"]),
                  "areas": storm["areas"], "first_sent": now.isoformat()}
        if match:
            record["first_sent"] = match["first_sent"]
            record["band"] = max(record["band"], match["band"])
            record["areas"] = sorted(set(match["areas"]) | set(storm["areas"]))
            record["start"] = min(match["start"], storm["start"])
            record["end"] = max(match["end"], storm["end"])
            known.remove(match)
        known.append(record)

    known.sort(key=lambda s: s["start"])
    if known != state.get("storms", []) or "storms" not in state:
        cw.save_state(state_path, {"storms": known})
    print(f"Sent {sent} preliminary storm notification(s).")
    return 0


if __name__ == "__main__":
    if not os.environ.get("NTFY_TOPIC") and os.environ.get("DRY_RUN") != "1":
        sys.exit("NTFY_TOPIC is empty: see README for setup.")
    sys.exit(run())
