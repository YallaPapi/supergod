# IG Reel Watch (Single Observer, No API)

This tracks reel views from target profiles using one observer browser session.

It does **not** log into each target account.
It visits target profile pages and records the last 3 reels (configurable).

## Outputs

- `dashboard/reel_views_sheet.csv` (spreadsheet-friendly)
- `dashboard/latest.json`
- `dashboard/index.html` (local dashboard)
- screenshot evidence for reels and profile reels grid
- SQLite history DB

## Install

```bash
pip install -e ".[tracking]"
playwright install chromium
```

## Config

Use [ig_reel_watch.example.json](/C:/Users/asus/Desktop/projects/supergod/deploy/ig_reel_watch.example.json) as template.

Observer options:
- `cdp_url` for an already-running browser profile (AdsPower/Chrome CDP)
- OR `user_data_dir` (+ optional `executable_path`) for persistent local profile
- OR `adspower_user_id` (+ `adspower_api_base`) to auto-start/stop AdsPower profile

Targets:
- list of profile URLs to monitor

## One Run

```bash
supergod-ig-reel-watch --config ./deploy/ig_reel_watch.example.json --top-reels 3
```

## Daily Run

```bash
supergod-ig-reel-watch --config ./deploy/ig_reel_watch.example.json --top-reels 3 --interval-minutes 1440
```

## Dashboard

Open:

```text
.ig_reel_watch/dashboard/index.html
```
