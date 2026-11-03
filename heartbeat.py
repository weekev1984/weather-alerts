#!/usr/bin/env python3
"""Send a low-priority "the checker ran" notification after every scheduled run.

Lets you confirm the hourly schedule is working. It only adds this one extra
message; real warnings and preliminary storm alerts are sent regardless.

  HEARTBEAT          "on" (default) or "off". Set the repository variable
                     HEARTBEAT to off to stop these messages.
  RUN_SUMMARY_FILE   file the other scripts appended their one-line summaries to
  RUN_URL            optional link to this Actions run (tapping the message opens it)
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import check_warnings as cw

OFF_VALUES = {"off", "false", "0", "no", "disabled"}
INTERVAL_HOURS = 1   # matches the hourly cron-job.org job (see README)
CRON_MINUTE = 0


def next_scheduled(now: dt.datetime) -> dt.datetime:
    """Next cron slot (every INTERVAL_HOURS hours UTC, at CRON_MINUTE) after now."""
    t = now.astimezone(dt.timezone.utc).replace(minute=CRON_MINUTE, second=0, microsecond=0)
    t = t.replace(hour=t.hour - t.hour % INTERVAL_HOURS)
    while t <= now:
        t += dt.timedelta(hours=INTERVAL_HOURS)
    return t


def enabled() -> bool:
    return os.environ.get("HEARTBEAT", "on").strip().lower() not in OFF_VALUES


def _local(t: dt.datetime) -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("Europe/London"))
    except Exception:
        return t


def build_payload(now: dt.datetime, summary: list[str], run_url: str = "") -> dict:
    local = _local(now)
    nxt = _local(next_scheduled(now))
    lines = summary or ["No summary recorded (a check may have failed, see the Actions log)."]
    message = "\n".join(lines) + f"\n\nNext scheduled check around {nxt:%H:%M}."
    payload = {
        "title": f"Weather check ran {local:%a %d %b %H:%M} ({local.tzname() or 'UTC'})",
        "message": message,
        "priority": 2,  # low: arrives silently
        "tags": ["white_check_mark"],
    }
    if run_url:
        payload["click"] = run_url
    return payload


def read_summary(path: str | None) -> list[str]:
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def run(now: dt.datetime | None = None, poster=cw.post_ntfy) -> int:
    if not enabled():
        print("Heartbeat is off (HEARTBEAT variable), not sending.")
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    payload = build_payload(now, read_summary(os.environ.get("RUN_SUMMARY_FILE")),
                            os.environ.get("RUN_URL", ""))
    poster(payload)
    print("Sent heartbeat notification.")
    return 0


if __name__ == "__main__":
    if not os.environ.get("NTFY_TOPIC") and os.environ.get("DRY_RUN") != "1":
        sys.exit("NTFY_TOPIC is empty: see README for setup.")
    sys.exit(run())
