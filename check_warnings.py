#!/usr/bin/env python3
"""Check Met Office weather warnings and push new ones to your phone via ntfy.

Uses only the Python standard library. Configure with environment variables:

  NTFY_TOPIC   (required) your private ntfy topic name, e.g. "kevin-weather-x7f3q"
  REGIONS            feeds to track in full (default "UK", i.e. every warning)
  PRIORITY_REGIONS   regions flagged as "SSEN area" and sent at higher priority
                     (default "os,he,gr,ta,st,se,sw"; see REGION_NAMES)
  NTFY_SERVER  ntfy server URL (default "https://ntfy.sh")
  NTFY_TOKEN   optional access token if your ntfy topic is protected
  STATE_FILE   where seen warnings are remembered (default "state.json")
  DRY_RUN      set to "1" to print notifications instead of sending them
  RUN_SUMMARY_FILE   optional file to append a one-line run summary to (read by
                     heartbeat.py for the "still running" notification)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://weather.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/{code}"

REGION_NAMES = {
    "UK": "United Kingdom",
    "os": "Orkney & Shetland",
    "he": "Highlands & Eilean Siar",
    "gr": "Grampian",
    "st": "Strathclyde",
    "ta": "Central, Tayside & Fife",
    "dg": "SW Scotland, Lothian Borders",
    "ni": "Northern Ireland",
    "wl": "Wales",
    "nw": "North West England",
    "ne": "North East England",
    "yh": "Yorkshire & Humber",
    "wm": "West Midlands",
    "em": "East Midlands",
    "ee": "East of England",
    "sw": "South West England",
    "se": "London & South East England",
}

# SSEN Distribution licence areas, mapped onto Met Office warning regions:
# north of Scotland (SHEPD) and central southern England (SEPD).
DEFAULT_PRIORITY = "os,he,gr,ta,st,se,sw"

USER_AGENT = "met-office-alerts/1.0 (personal warning notifier)"
# Touch the state file at least this often so GitHub keeps the schedule alive
# (it pauses scheduled workflows after 60 days without repository activity).
KEEPALIVE_DAYS = 30


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _text(item: ET.Element, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse_feed(xml_bytes: bytes) -> list[dict]:
    """Return the warnings in an RSS feed as a list of dicts."""
    root = ET.fromstring(xml_bytes)
    warnings = []
    for item in root.iter("item"):
        title = _text(item, "title")
        link = _text(item, "link")
        description = _text(item, "description")
        guid = _text(item, "guid")
        pub_date = _text(item, "pubDate")
        # A reissued or updated warning gets a new pubDate/description, so it
        # counts as new. Prefer guid when the feed provides one.
        key_source = guid or "|".join([title, link, pub_date, description])
        warnings.append(
            {
                "id": hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "link": link,
                "description": description,
                "pub_date": pub_date,
            }
        )
    return warnings


def warning_colour(title: str) -> str | None:
    """Return "red", "amber" or "yellow" from a title like "Yellow warning of rain ..."."""
    m = re.search(r"\b(red|amber|yellow)\b(?=\s+warning)", title, re.IGNORECASE)
    return m.group(1).lower() if m else None


def priority_for(title: str, in_priority_area: bool = False) -> int:
    colour = warning_colour(title)
    if in_priority_area:
        return {"red": 5, "amber": 5, "yellow": 4}.get(colour, 4)
    return {"red": 5, "amber": 4, "yellow": 3}.get(colour, 3)


def colour_tag(title: str) -> str:
    return {"red": "red_circle", "amber": "orange_circle", "yellow": "yellow_circle"}.get(
        warning_colour(title), "warning"
    )


def send_ntfy(warning: dict, priority_regions: list[str]) -> None:
    title = warning["title"] or "Met Office weather warning"
    message = warning["description"] or warning["title"]
    tags = [colour_tag(title)]
    if priority_regions:
        names = ", ".join(REGION_NAMES.get(r, r) for r in priority_regions)
        title = "SSEN AREA: " + title
        message = f"Affects your priority area(s): {names}\n\n{message}"
        tags.insert(0, "zap")
    payload = {
        "title": title,
        "message": message[:3500],
        "priority": priority_for(warning["title"], bool(priority_regions)),
        "tags": tags,
    }
    if warning["link"]:
        payload["click"] = warning["link"]
        payload["actions"] = [{"action": "view", "label": "Open Met Office", "url": warning["link"]}]

    post_ntfy(payload)


def post_ntfy(payload: dict) -> None:
    """POST a JSON message to ntfy (the topic is filled in from NTFY_TOPIC)."""
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": os.environ.get("NTFY_TOPIC", ""), **payload}
    if os.environ.get("DRY_RUN") == "1":
        print("DRY RUN, would send:", json.dumps(payload, ensure_ascii=False))
        return

    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        server + "/", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def note_summary(line: str) -> None:
    """Append a line to RUN_SUMMARY_FILE (if set) for the heartbeat notification."""
    path = os.environ.get("RUN_SUMMARY_FILE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.strip() + "\n")
    except OSError as exc:
        print(f"could not write run summary: {exc}", file=sys.stderr)


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"seen": {}, "last_written": None}


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def _env_list(name: str, default: str) -> list[str]:
    return [r.strip() for r in os.environ.get(name, default).split(",") if r.strip()]


def run(now: dt.datetime | None = None, fetcher=fetch, sender=send_ntfy) -> int:
    """Read the whole-UK feed plus each priority region's feed and notify new warnings.

    Every UK warning is sent. Warnings that also appear in a priority region's
    feed (or name a priority region in their title) are flagged as SSEN-area
    and sent at higher priority.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    all_regions = _env_list("REGIONS", "UK")
    priority = [r for r in _env_list("PRIORITY_REGIONS", DEFAULT_PRIORITY) if r != "UK"]
    feeds = list(dict.fromkeys(all_regions + priority))  # keep order, drop duplicates
    state_path = os.environ.get("STATE_FILE", "state.json")
    state = load_state(state_path)
    old_seen: dict = state.get("seen", {})

    # Collect every warning once, remembering which priority regions it touches.
    warnings: dict[str, dict] = {}  # id -> warning
    by_title: dict[str, str] = {}  # normalised title -> id
    failed = []
    for region in feeds:
        try:
            items = parse_feed(fetcher(FEED_URL.format(code=region)))
        except Exception as exc:  # keep going for other regions
            print(f"[{region}] could not read feed: {exc}", file=sys.stderr)
            failed.append(region)
            continue
        print(f"[{region}] {len(items)} active warning(s)")
        for w in items:
            wid = w["id"] if w["id"] in warnings else by_title.get(_norm(w["title"]), w["id"])
            entry = warnings.setdefault(wid, {**w, "id": wid, "priority_regions": []})
            by_title.setdefault(_norm(w["title"]), wid)
            if region in priority and region not in entry["priority_regions"]:
                entry["priority_regions"].append(region)

    # A UK-wide item may name a priority region in its title without us having
    # matched it to that region's feed (e.g. if a regional feed was down).
    for w in warnings.values():
        lowered = w["title"].lower()
        for region in priority:
            name = REGION_NAMES.get(region, "").lower()
            if name and name in lowered and region not in w["priority_regions"]:
                w["priority_regions"].append(region)

    new_seen: dict = {}
    sent = 0
    for wid, w in warnings.items():
        if wid in old_seen:
            new_seen[wid] = old_seen[wid]
            continue
        flag = " [SSEN area]" if w["priority_regions"] else ""
        print(f"NEW{flag}: {w['title']}")
        try:
            sender(w, w["priority_regions"])
            sent += 1
        except Exception as exc:
            print(f"failed to send notification: {exc}", file=sys.stderr)
            continue  # not marked seen, so it is retried next run
        new_seen[wid] = {"title": w["title"], "first_seen": now.isoformat()}

    if failed:
        # Don't forget warnings just because a feed blipped, or they'd re-alert.
        for k, v in old_seen.items():
            new_seen.setdefault(k, v)

    last_written = state.get("last_written")
    stale = (
        last_written is None
        or now - dt.datetime.fromisoformat(last_written) > dt.timedelta(days=KEEPALIVE_DAYS)
    )
    if new_seen != old_seen or stale:
        save_state(state_path, {"seen": new_seen, "last_written": now.isoformat()})

    print(f"Sent {sent} notification(s).")
    ssen = sum(1 for w in warnings.values() if w["priority_regions"])
    line = f"Met Office: {len(warnings)} active warning(s), {ssen} in SSEN areas, {sent} new sent"
    if failed:
        line += f" (could not read: {', '.join(failed)})"
    note_summary(line)
    return 1 if len(failed) == len(feeds) else 0


def send_test() -> None:
    send_ntfy(
        {
            "title": "Test: Met Office warning notifier is working",
            "description": "You'll get a notification like this whenever a new warning is issued.",
            "link": "https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings",
        },
        [],
    )
    print("Sent test notification.")


if __name__ == "__main__":
    if not os.environ.get("NTFY_TOPIC") and os.environ.get("DRY_RUN") != "1":
        sys.exit(
            "NTFY_TOPIC is empty: add a repository secret named NTFY_TOPIC whose value is "
            "your ntfy topic name (Settings > Secrets and variables > Actions)."
        )
    if os.environ.get("SEND_TEST") == "1":
        send_test()
    sys.exit(run())
