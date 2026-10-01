# Taunton EV charger monitor

Logs the live status of every public charging bay within 3 miles of Taunton
town centre, every 5 minutes. The data comes from the free open-data feeds that
operators must publish under the Public Charge Point Regulations 2023.

## What it records

| File | Contents |
|---|---|
| `data/status/YYYY-MM-DD.csv` | One row per bay per poll: time, operator, site, bay, status (`AVAILABLE`, `CHARGING`, `BLOCKED`, `OUTOFORDER`, …), max kW |
| `data/sites.csv` | Register of every bay found: address, postcode, location, distance from centre, connectors |
| `data/poll_log.csv` | Health check per feed per poll: did it work, how many sites and bays, any error |

## Setup (about 10 minutes)

1. **Create a new GitHub repository**, e.g. `taunton-ev-monitor`.
   Make it **public**. GitHub Actions minutes are unlimited on public repos.
   On a private repo, polling every 5 minutes uses roughly 8,600 minutes a month,
   well over the 2,000 free. The data is open government data anyway, and any
   keys you add stay hidden as secrets.
2. **Upload these files**, keeping the folder structure
   (`poller.py`, `feeds.yaml`, `README.md`, `.github/workflows/poll.yml`).
   On github.com: *Add file → Upload files*, then drag the unzipped folder in.
   The `.github` folder is hidden on Mac; press Cmd+Shift+. in Finder to show it.
3. **Allow the workflow to save data:** *Settings → Actions → General →
   Workflow permissions → Read and write permissions → Save*.
4. **Start it:** *Actions tab → Poll Taunton EV chargers → Run workflow*.
   After that it runs on its own every ~5 minutes.
   Check the run log: each feed shows `[OK ]` or `[ERR]` with the number of
   Taunton sites found.

## Adding more operators

Two feeds are switched on from the start because they need no key:
**MFG EV Power** (Priory Bridge Road) and **Clenergy EV**.

The rest need a free key or a signed agreement first:

| Operator | Taunton sites | How to get access |
|---|---|---|
| bp pulse | M&S East Street | Request a key via bp pulse's PCPR help page (served through Eco-Movement) |
| InstaVolt | Deane House, Blackbrook | Email InstaVolt; they send an Open Data Agreement, then the URL and credentials |
| Smart Charge | Sainsbury's Hankridge | Contact Smart Charge and ask for their PCPR open-data feed |
| Swarco eVolt / Somerset Council | Crescent, Belvedere Rd, Wood St, Castle St | Contact Swarco eVolt or the council's EV team and ask for the PCPR feed |

When a key arrives:
1. *Settings → Secrets and variables → Actions → New repository secret*.
   Use the name shown under `token_env` in `feeds.yaml` (e.g. `BP_PULSE_TOKEN`).
2. In `feeds.yaml`, set that feed's `url` (if it says `REPLACE_…`) and `enabled: true`.

If an operator gives a different auth format (e.g. `Bearer`), change
`auth_scheme` for that feed.

## Notes

- GitHub can delay scheduled runs by a few minutes at busy times. This is fine
  for hourly and daily patterns.
- `CHARGING` usually means a car is plugged in. A car left connected after it
  finishes may still show as `CHARGING`.
- Check `data/poll_log.csv` now and then; an operator changing its URL shows up
  there as an error.
- Test locally without writing anything: `pip install pyyaml && python poller.py --dry-run`
