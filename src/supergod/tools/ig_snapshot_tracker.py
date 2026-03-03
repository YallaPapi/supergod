"""Browser-based Instagram tracker with daily snapshots and SQLite storage.

This tool is intentionally API-free. It uses browser sessions/cookies from
real profiles (CDP or persistent Chromium profile dirs) and captures:

1) profile snapshot metrics (posts/followers/following),
2) recent post metrics (likes/views/comments when visible),
3) screenshot evidence.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

PROFILE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "posts": [
        re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+posts?\b", re.IGNORECASE),
        re.compile(r"\bposts?\s*[:\-]?\s*(?P<num>\d[\d,\.]*\s*[kmb]?)\b", re.IGNORECASE),
    ],
    "followers": [
        re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+followers?\b", re.IGNORECASE),
        re.compile(r"\bfollowers?\s*[:\-]?\s*(?P<num>\d[\d,\.]*\s*[kmb]?)\b", re.IGNORECASE),
    ],
    "following": [
        re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+following\b", re.IGNORECASE),
        re.compile(r"\bfollowing\s*[:\-]?\s*(?P<num>\d[\d,\.]*\s*[kmb]?)\b", re.IGNORECASE),
    ],
}

POST_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "likes": [
        re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+likes?\b", re.IGNORECASE),
        re.compile(r"\bliked by .*? and (?P<num>\d[\d,\.]*\s*[kmb]?) others", re.IGNORECASE),
    ],
    "views": [
        re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+views?\b", re.IGNORECASE),
    ],
    "comments": [
        re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+comments?\b", re.IGNORECASE),
    ],
}

PROFILE_SCRIPT = """
() => {
  const texts = new Set();
  const add = (v) => {
    if (!v) return;
    const s = String(v).replace(/\\s+/g, " ").trim();
    if (!s) return;
    texts.add(s);
  };
  add(document.title);
  add(document.querySelector('meta[property="og:description"]')?.content);
  add(document.querySelector('meta[name="description"]')?.content);

  document.querySelectorAll("header *").forEach((el) => {
    add(el.getAttribute("aria-label"));
    add(el.getAttribute("title"));
    if (el.childElementCount === 0) add(el.textContent);
  });
  document.querySelectorAll("main section ul li, header section ul li, ul li").forEach((el) => {
    add(el.textContent);
    add(el.getAttribute("aria-label"));
    add(el.getAttribute("title"));
  });

  const links = [];
  document.querySelectorAll('article a[href*="/p/"], article a[href*="/reel/"]').forEach((a) => {
    const href = a.getAttribute("href");
    if (!href) return;
    try {
      links.push(new URL(href, location.origin).toString());
    } catch (_) {}
  });

  return {
    url: location.href,
    username_hint: document.querySelector("header h2, header h1")?.textContent?.trim() || "",
    texts: Array.from(texts).slice(0, 600),
    post_links: Array.from(new Set(links)).slice(0, 100),
  };
}
"""

POST_SCRIPT = """
() => {
  const texts = new Set();
  const add = (v) => {
    if (!v) return;
    const s = String(v).replace(/\\s+/g, " ").trim();
    if (!s) return;
    texts.add(s);
  };
  add(document.title);
  add(document.querySelector('meta[property="og:description"]')?.content);
  add(document.querySelector('meta[name="description"]')?.content);
  document.querySelectorAll("article *, section *").forEach((el) => {
    add(el.getAttribute("aria-label"));
    add(el.getAttribute("title"));
    if (el.childElementCount === 0) add(el.textContent);
  });
  return {
    url: location.href,
    texts: Array.from(texts).slice(0, 700),
  };
}
"""


@dataclass
class AccountTarget:
    account_id: str
    profile_url: str
    username: str = ""
    cdp_url: str = ""
    user_data_dir: str = ""
    executable_path: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "AccountTarget":
        account_id = str(item.get("account_id", "")).strip()
        profile_url = str(item.get("profile_url", "")).strip()
        if not account_id:
            raise ValueError("account_id is required for each account")
        if not profile_url:
            raise ValueError(f"profile_url is required for account '{account_id}'")
        cdp_url = str(item.get("cdp_url", "")).strip()
        user_data_dir = str(item.get("user_data_dir", "")).strip()
        if not cdp_url and not user_data_dir:
            raise ValueError(
                f"account '{account_id}' must set either cdp_url or user_data_dir"
            )
        return cls(
            account_id=account_id,
            profile_url=profile_url,
            username=str(item.get("username", "")).strip(),
            cdp_url=cdp_url,
            user_data_dir=user_data_dir,
            executable_path=str(item.get("executable_path", "")).strip(),
            tags=[str(x).strip() for x in item.get("tags", []) if str(x).strip()],
        )


@dataclass
class SnapshotResult:
    account_id: str
    captured_at: str
    status: str
    error: str = ""
    username: str = ""
    profile_url: str = ""
    posts: int | None = None
    followers: int | None = None
    following: int | None = None
    screenshot_path: str = ""
    profile_raw: dict[str, Any] = field(default_factory=dict)
    post_snapshots: list[dict[str, Any]] = field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_compact_count(raw: str) -> int | None:
    s = (raw or "").strip().lower().replace(",", "")
    if not s:
        return None
    m = re.search(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>[kmb])?\+?$", s)
    if not m:
        return None
    num = float(m.group("num"))
    unit = (m.group("unit") or "").lower()
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    return int(round(num * mult.get(unit, 1)))


def extract_count(texts: list[str], metric: str) -> tuple[int | None, str]:
    pats = PROFILE_PATTERNS.get(metric, [])
    for text in texts:
        for pat in pats:
            m = pat.search(text or "")
            if not m:
                continue
            parsed = parse_compact_count(m.group("num"))
            if parsed is not None:
                return parsed, text
    return None, ""


def extract_post_metric(texts: list[str], metric: str) -> tuple[int | None, str]:
    pats = POST_PATTERNS.get(metric, [])
    for text in texts:
        for pat in pats:
            m = pat.search(text or "")
            if not m:
                continue
            parsed = parse_compact_count(m.group("num"))
            if parsed is not None:
                return parsed, text
    return None, ""


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value or "").strip("_.") or "unknown"


def _infer_username(profile_url: str, fallback: str = "") -> str:
    if fallback:
        return fallback.strip().lstrip("@")
    path = urlparse(profile_url).path or ""
    chunks = [c for c in path.split("/") if c]
    if chunks:
        return chunks[0].strip().lstrip("@")
    return ""


def _safe_click(page: Any, selector: str) -> None:
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            loc.click(timeout=800)
    except Exception:
        pass


def _dismiss_common_popups(page: Any) -> None:
    # Best effort. Ignore failures.
    _safe_click(page, "button:has-text('Only allow essential cookies')")
    _safe_click(page, "button:has-text('Allow all cookies')")
    _safe_click(page, "button:has-text('Not Now')")
    _safe_click(page, "button:has-text('Not now')")


def _open_context(playwright: Any, account: AccountTarget, headless: bool, timeout_ms: int) -> tuple[Any, Any, bool]:
    # Returns (browser, context, owns_context).
    # owns_context=True means this tool launched it and should close it.
    if account.cdp_url:
        browser = playwright.chromium.connect_over_cdp(account.cdp_url, timeout=timeout_ms)
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = browser.new_context()
        return browser, context, False
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=account.user_data_dir,
        executable_path=account.executable_path or None,
        headless=headless,
        viewport={"width": 1400, "height": 2200},
    )
    return None, context, True


def capture_account_snapshot(
    *,
    playwright: Any,
    account: AccountTarget,
    out_dir: Path,
    post_limit: int,
    post_screenshots: int,
    headless: bool,
    timeout_sec: int,
    close_cdp_browser: bool,
) -> SnapshotResult:
    captured_at = utc_now_iso()
    timeout_ms = max(5, int(timeout_sec)) * 1000
    username = _infer_username(account.profile_url, account.username)
    result = SnapshotResult(
        account_id=account.account_id,
        captured_at=captured_at,
        status="error",
        username=username,
        profile_url=account.profile_url,
    )

    browser = None
    context = None
    profile_page = None
    owns_context = False
    try:
        browser, context, owns_context = _open_context(
            playwright,
            account,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        profile_page = context.new_page()
        profile_page.goto(
            account.profile_url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        profile_page.wait_for_timeout(2500)
        _dismiss_common_popups(profile_page)
        profile_data = profile_page.evaluate(PROFILE_SCRIPT)
        texts = [str(t) for t in profile_data.get("texts", []) if str(t).strip()]

        posts, _ = extract_count(texts, "posts")
        followers, _ = extract_count(texts, "followers")
        following, _ = extract_count(texts, "following")

        today_dir = out_dir / "screenshots" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
        account_dir = today_dir / _sanitize_name(account.account_id)
        account_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%H%M%S")
        profile_shot = account_dir / f"profile_{stamp}.png"
        profile_page.screenshot(path=str(profile_shot), full_page=True)

        post_rows: list[dict[str, Any]] = []
        links = [str(x) for x in profile_data.get("post_links", []) if str(x).strip()]
        links = links[: max(0, int(post_limit))]
        for idx, post_url in enumerate(links, start=1):
            post_page = None
            try:
                post_page = context.new_page()
                post_page.goto(post_url, wait_until="domcontentloaded", timeout=timeout_ms)
                post_page.wait_for_timeout(1800)
                _dismiss_common_popups(post_page)
                post_payload = post_page.evaluate(POST_SCRIPT)
                post_texts = [str(t) for t in post_payload.get("texts", []) if str(t).strip()]
                likes, likes_src = extract_post_metric(post_texts, "likes")
                views, views_src = extract_post_metric(post_texts, "views")
                comments, comments_src = extract_post_metric(post_texts, "comments")
                shot_path = ""
                if idx <= max(0, int(post_screenshots)):
                    shot = account_dir / f"post_{idx:02d}_{stamp}.png"
                    post_page.screenshot(path=str(shot), full_page=True)
                    shot_path = str(shot.resolve())
                post_rows.append(
                    {
                        "index": idx,
                        "post_url": post_url,
                        "likes": likes,
                        "views": views,
                        "comments": comments,
                        "metric_evidence": {
                            "likes_text": likes_src,
                            "views_text": views_src,
                            "comments_text": comments_src,
                        },
                        "raw": post_payload,
                        "screenshot_path": shot_path,
                    }
                )
            except Exception as e:
                post_rows.append(
                    {
                        "index": idx,
                        "post_url": post_url,
                        "error": str(e),
                    }
                )
            finally:
                if post_page is not None:
                    try:
                        post_page.close()
                    except Exception:
                        pass

        result.posts = posts
        result.followers = followers
        result.following = following
        result.screenshot_path = str(profile_shot.resolve())
        result.profile_raw = profile_data
        result.post_snapshots = post_rows
        result.status = "ok"
        return result
    except Exception as e:
        result.error = str(e)
        return result
    finally:
        if profile_page is not None:
            try:
                profile_page.close()
            except Exception:
                pass
        if context is not None and owns_context:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None and close_cdp_browser:
            try:
                browser.close()
            except Exception:
                pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            accounts_total INTEGER NOT NULL DEFAULT 0,
            accounts_success INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            profile_url TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            last_seen_at TEXT
        );

        CREATE TABLE IF NOT EXISTS profile_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            posts INTEGER,
            followers INTEGER,
            following INTEGER,
            profile_url TEXT NOT NULL,
            screenshot_path TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS post_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            post_index INTEGER NOT NULL,
            post_url TEXT NOT NULL,
            likes INTEGER,
            views INTEGER,
            comments INTEGER,
            screenshot_path TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_profile_snapshots_run ON profile_snapshots(run_id);
        CREATE INDEX IF NOT EXISTS idx_profile_snapshots_account ON profile_snapshots(account_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_post_snapshots_run ON post_snapshots(run_id);
        """
    )
    conn.commit()


def load_accounts(config_path: Path) -> list[AccountTarget]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    rows = data.get("accounts", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("config must contain a non-empty 'accounts' list")
    return [AccountTarget.from_dict(dict(x)) for x in rows]


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    config_path = Path(args.config).resolve()
    out_dir = Path(args.output_dir).resolve()
    db_path = Path(args.db).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    accounts = load_accounts(config_path)
    run_id = uuid.uuid4().hex[:12]
    started_at = utc_now_iso()
    run_log = out_dir / "runs" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{run_id}.jsonl"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO runs (run_id, started_at, status, accounts_total)
        VALUES (?, ?, ?, ?)
        """,
        (run_id, started_at, "running", len(accounts)),
    )
    conn.commit()

    ok = 0
    fail = 0
    with sync_playwright() as playwright:
        for account in accounts:
            result = capture_account_snapshot(
                playwright=playwright,
                account=account,
                out_dir=out_dir,
                post_limit=args.post_limit,
                post_screenshots=args.post_screenshots,
                headless=args.headless,
                timeout_sec=args.timeout_sec,
                close_cdp_browser=args.close_cdp_browser,
            )
            if result.status == "ok":
                ok += 1
            else:
                fail += 1

            conn.execute(
                """
                INSERT INTO accounts (account_id, username, profile_url, tags_json, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    username=excluded.username,
                    profile_url=excluded.profile_url,
                    tags_json=excluded.tags_json,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    account.account_id,
                    result.username or account.username,
                    account.profile_url,
                    json.dumps(account.tags, ensure_ascii=True),
                    result.captured_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO profile_snapshots
                (run_id, account_id, captured_at, status, error, posts, followers, following, profile_url, screenshot_path, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.account_id,
                    result.captured_at,
                    result.status,
                    result.error,
                    result.posts,
                    result.followers,
                    result.following,
                    result.profile_url,
                    result.screenshot_path,
                    json.dumps(result.profile_raw, ensure_ascii=True),
                ),
            )
            for row in result.post_snapshots:
                conn.execute(
                    """
                    INSERT INTO post_snapshots
                    (run_id, account_id, captured_at, post_index, post_url, likes, views, comments, screenshot_path, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        result.account_id,
                        result.captured_at,
                        int(row.get("index", 0)),
                        str(row.get("post_url", "")),
                        row.get("likes"),
                        row.get("views"),
                        row.get("comments"),
                        str(row.get("screenshot_path", "")),
                        json.dumps(row, ensure_ascii=True),
                    ),
                )
            conn.commit()
            _write_jsonl(
                run_log,
                {
                    "ts": result.captured_at,
                    "run_id": run_id,
                    "account_id": result.account_id,
                    "status": result.status,
                    "error": result.error,
                    "posts": result.posts,
                    "followers": result.followers,
                    "following": result.following,
                    "screenshot_path": result.screenshot_path,
                },
            )
            log.info(
                "snapshot account=%s status=%s followers=%s posts=%s",
                result.account_id,
                result.status,
                result.followers,
                result.posts,
            )

    finished_at = utc_now_iso()
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "accounts_total": len(accounts),
        "accounts_success": ok,
        "error_count": fail,
        "db_path": str(db_path),
        "run_log": str(run_log),
    }
    conn.execute(
        """
        UPDATE runs
        SET finished_at=?, status=?, accounts_success=?, error_count=?, summary_json=?
        WHERE run_id=?
        """,
        (
            finished_at,
            "ok" if fail == 0 else "partial",
            ok,
            fail,
            json.dumps(summary, ensure_ascii=True),
            run_id,
        ),
    )
    conn.commit()
    conn.close()
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Daily Instagram browser snapshot tracker (no API).",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON config listing accounts and browser profile connection.",
    )
    parser.add_argument(
        "--db",
        default=".ig_tracker/ig_tracker.db",
        help="SQLite database path for snapshots.",
    )
    parser.add_argument(
        "--output-dir",
        default=".ig_tracker",
        help="Output root for screenshots and run JSONL logs.",
    )
    parser.add_argument(
        "--post-limit",
        type=int,
        default=6,
        help="How many recent posts to inspect per account.",
    )
    parser.add_argument(
        "--post-screenshots",
        type=int,
        default=2,
        help="How many of inspected posts get screenshot evidence.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=45,
        help="Per navigation timeout in seconds.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Headless mode for accounts using user_data_dir launch mode.",
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=0,
        help="If > 0, run forever with this interval (e.g. 1440 for daily).",
    )
    parser.add_argument(
        "--close-cdp-browser",
        action="store_true",
        help="Close CDP browser process after each account (off by default).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logs.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    interval = max(0, int(args.interval_minutes))
    if interval == 0:
        summary = run_once(args)
        print(json.dumps(summary, ensure_ascii=True))
        return 0 if summary.get("error_count", 1) == 0 else 2

    while True:
        summary = run_once(args)
        print(json.dumps(summary, ensure_ascii=True))
        time.sleep(interval * 60)


if __name__ == "__main__":
    raise SystemExit(main())
