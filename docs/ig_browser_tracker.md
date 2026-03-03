# IG Browser Tracker (No API)

This tracker captures Instagram metrics from real browser sessions/profiles, not the Instagram API.

## What It Stores

- Per-account snapshot (daily or any interval):
  - `posts`, `followers`, `following`
  - full-page profile screenshot
  - raw page text evidence
- Recent post checks:
  - `likes`, `views`, `comments` when visible
  - optional post screenshots
- Persistence:
  - SQLite DB
  - JSONL run log

## Install

```bash
pip install -e ".[tracking]"
playwright install chromium
```

## Config Format

Create a JSON file:

```json
{
  "accounts": [
    {
      "account_id": "ig-main-01",
      "username": "your_handle",
      "profile_url": "https://www.instagram.com/your_handle/",
      "cdp_url": "http://127.0.0.1:9222",
      "tags": ["main", "client-a"]
    },
    {
      "account_id": "ig-alt-02",
      "profile_url": "https://www.instagram.com/another_handle/",
      "user_data_dir": "C:/browser_profiles/ig-alt-02",
      "executable_path": "C:/Program Files/Google/Chrome/Application/chrome.exe",
      "tags": ["alt"]
    }
  ]
}
```

Rules:
- Set either `cdp_url` or `user_data_dir` per account.
- `cdp_url` mode uses an already-running browser profile.
- `user_data_dir` mode launches a persistent browser profile from disk.

## One-Off Run

```bash
supergod-ig-snapshot --config ./ig_accounts.json --post-limit 6 --post-screenshots 2
```

## Daily Run (every 24h)

```bash
supergod-ig-snapshot --config ./ig_accounts.json --interval-minutes 1440
```

## Output Paths

- DB: `.ig_tracker/ig_tracker.db`
- Screenshots: `.ig_tracker/screenshots/YYYY-MM-DD/<account_id>/...`
- Run logs: `.ig_tracker/runs/*.jsonl`

