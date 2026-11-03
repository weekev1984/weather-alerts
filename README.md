# Met Office warning notifier

Checks the Met Office's official weather warnings RSS feed every hour and
sends a push notification to your phone (via [ntfy](https://ntfy.sh)) whenever
a new warning appears. Runs for free on GitHub Actions. No server needed.

## How it works

1. Every hour, GitHub Actions runs `check_warnings.py`.
2. It reads the warnings feed for your region(s) from
   `https://weather.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/<code>`.
3. Any warning it hasn't seen before is pushed to your ntfy topic. Updated or
   reissued warnings count as new.
4. It remembers what it has sent in `state.json`, committed back to the repo.

It tracks **every** UK warning, and flags the ones in SSEN Distribution's
areas (north of Scotland and central southern England) so they stand out:

| | SSEN area | Elsewhere in the UK |
|---|---|---|
| Title | starts `SSEN AREA:` with a ⚡ | plain |
| Message | lists which of your areas are affected | the warning text |
| Yellow | high priority | normal priority |
| Amber | urgent (bypasses quiet mode if you allow it) | high priority |
| Red | urgent | urgent |

Tapping a notification opens the Met Office warnings page.

The SSEN areas are mapped onto Met Office warning regions as
`os,he,gr,ta,st` (north of Scotland: Orkney & Shetland, Highlands & Eilean Siar,
Grampian, Central/Tayside/Fife, Strathclyde for Argyll) and `se,sw`
(central southern England: Hampshire, Berkshire, Oxfordshire, Isle of Wight,
Wiltshire, Dorset and neighbours). Met Office regions are broader than SSEN's
licence areas, so some flagged warnings (e.g. Glasgow or Kent) will be just
outside the network.

## Preliminary storm alerts

Separately from the official warnings, `storm_outlook.py` looks up to 10 days
ahead for storms, so you get a heads-up before the Met Office issues anything.
It runs in the same hourly job.

- **Source:** free ensemble forecasts of wind gusts from
  [Open-Meteo](https://open-meteo.com/en/docs/ensemble-api): ECMWF (51 runs)
  and GFS (31 runs) of the same forecast, each slightly different. The share of
  runs showing damaging gusts at a place is used as the chance of a storm.
- **Where:** 26 places, weighted to SSEN areas (Lerwick, Kirkwall, Stornoway,
  Wick, Inverness, Aberdeen, Fort William, Oban, Perth; Reading, Oxford,
  Salisbury, Portsmouth, Bournemouth, Isle of Wight) plus 11 across the rest of
  the UK.
- **What counts as damaging:** gusts around the level of a Met Office yellow
  wind warning: 50-55 mph inland, 60-65 mph on coasts, 70-75 mph in Orkney,
  Shetland, the Western Isles and Caithness. Edit `LOCATIONS` in
  `storm_outlook.py` to change places or thresholds.
- **When it alerts:** at least a **30% chance** at any place, more than 12 hours
  ahead (formal warnings cover the short term). Change the 30 with a repository
  variable `PRELIM_PROBABILITY`.
- **How it looks:** titles start `PRELIMINARY: possible storm Thu 5 Nov` (with
  `SSEN AREA` in front when an SSEN place is affected), and the message lists
  each place's chance and expected gusts. Normal priority, or high for SSEN
  areas or a 70%+ chance, so they never look like a real amber or red warning.
- **No duplicates:** each storm is sent once, remembered in `storm_state.json`.
  It is sent again (as `UPDATE`) only if it moves up a band (50%, then 70%) or
  newly reaches an SSEN area. A storm whose timing shifts by a day between
  forecasts counts as the same storm.

These are early signals, not forecasts you should act on alone: many will fade
as the day gets closer. If the outlook can't be fetched, the official warnings
still run as normal.

## Heartbeat ("it's still running") messages

After every run you also get a quiet, low-priority message such as
`Weather check ran Tue 03 Nov 08:07 (GMT)` with a one-line summary of the Met
Office check and the storm outlook (including the highest storm chance found),
plus when the next check is due. Tapping it opens the run's log.

To turn these off: **Settings → Secrets and variables → Actions → Variables →
New repository variable**, name `HEARTBEAT`, value `off`. Set it back to `on`
(or delete the variable) to turn them on again. Warnings and preliminary storm
alerts are always sent, whatever this is set to.

## Setup (about 5 minutes)

1. **Phone:** install the ntfy app
   ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy),
   [iPhone](https://apps.apple.com/app/ntfy/id1625396347)). Tap **+** and
   subscribe to a topic name that's hard to guess, e.g. `kevin-weather-7fq2x9`.
   Anyone who knows the name can read it, so don't use something obvious.
2. **GitHub:** create a new repository (private is fine) and upload these files.
3. In the repo go to **Settings → Secrets and variables → Actions**:
   - **Secrets** tab → *New repository secret* → name `NTFY_TOPIC`, value your topic name.
   - (Optional) **Variables** tab → *New repository variable* → name
     `PRIORITY_REGIONS`, to change which regions are flagged (default
     `os,he,gr,ta,st,se,sw`).
4. Go to **Actions → Check Met Office warnings → Run workflow** to test it
   straight away. Tick "Also send a test notification" to check your phone gets
   it. Any warnings in force right now will be sent on this first run.

## Region codes

| Code | Region | Code | Region |
|---|---|---|---|
| `UK` | Whole UK (default) | `wl` | Wales |
| `os` | Orkney & Shetland | `nw` | North West England |
| `he` | Highlands & Eilean Siar | `ne` | North East England |
| `gr` | Grampian | `yh` | Yorkshire & Humber |
| `st` | Strathclyde | `wm` | West Midlands |
| `ta` | Central, Tayside & Fife | `em` | East Midlands |
| `dg` | SW Scotland, Lothian Borders | `ee` | East of England |
| `ni` | Northern Ireland | `sw` | South West England |
| | | `se` | London & South East England |

## Notes

- **ntfy (free) tradeoffs:** anyone who guesses your topic name can read or
  send to it, so pick a random one. ntfy.sh keeps messages for 12 hours, so if
  your phone is off for longer you could miss one (the Met Office page always
  has the full list). On iPhone, delivery goes through ntfy's Apple relay and
  is normally a few seconds.
- **Timing:** GitHub's scheduled runs can start a few minutes (occasionally
  longer) after the scheduled time. Edit the `cron` line in
  `.github/workflows/check-warnings.yml` to change how often it checks, e.g.
  `"7 */4 * * *"` for every 4 hours. If you change it, also update
  `INTERVAL_HOURS` in `heartbeat.py` so the "next check" time stays right.
- **Keeping the schedule alive:** GitHub pauses schedules in repos with no
  activity for 60 days, so the script rewrites `state.json` at least every 30 days.
- **Test locally:** `DRY_RUN=1 python check_warnings.py` prints what it would send.
  Run the tests with `python -m unittest discover -s tests`.
