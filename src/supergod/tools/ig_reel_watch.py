"""Instagram reel view tracker using a single observer browser session.

No Instagram API. No login cycling across 100 accounts.
One observer session visits target profile pages and captures:
- last N reel URLs (default 3)
- current view count for each reel
- screenshot evidence
- CSV "sheet" rows
- local HTML dashboard + JSON feed
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

VIEW_PATTERNS = [
    re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+views?\b", re.IGNORECASE),
    re.compile(r"(?P<num>\d[\d,\.]*\s*[kmb]?)\s+plays?\b", re.IGNORECASE),
]

REEL_LINKS_SCRIPT = """
() => {
  const links = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/reel/"]')) {
    const href = a.getAttribute("href");
    if (!href) continue;
    let abs = "";
    try {
      abs = new URL(href, location.origin).toString();
    } catch (_) { continue; }
    if (seen.has(abs)) continue;
    seen.add(abs);
    links.push({
      reel_url: abs,
      text: (a.innerText || "").trim(),
      aria_label: (a.getAttribute("aria-label") || "").trim()
    });
  }
  return links;
}
"""

REEL_TEXT_SCRIPT = """
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
  for (const el of document.querySelectorAll("article *, section *, header *")) {
    add(el.getAttribute("aria-label"));
    add(el.getAttribute("title"));
    if (el.childElementCount === 0) add(el.textContent);
  }
  return { url: location.href, texts: Array.from(texts).slice(0, 1000) };
}
"""


@dataclass
class ObserverConfig:
    cdp_url: str = ""
    user_data_dir: str = ""
    executable_path: str = ""
    adspower_api_base: str = "http://local.adspower.net:50325"
    adspower_user_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ObserverConfig":
        cfg = cls(
            cdp_url=str(data.get("cdp_url", "")).strip(),
            user_data_dir=str(data.get("user_data_dir", "")).strip(),
            executable_path=str(data.get("executable_path", "")).strip(),
            adspower_api_base=str(
                data.get("adspower_api_base", "http://local.adspower.net:50325")
            ).strip(),
            adspower_user_id=str(data.get("adspower_user_id", "")).strip(),
        )
        if not cfg.cdp_url and not cfg.user_data_dir and not cfg.adspower_user_id:
            raise ValueError(
                "observer must set cdp_url or user_data_dir or adspower_user_id"
            )
        return cfg


@dataclass
class TargetProfile:
    target_id: str
    profile_url: str
    label: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TargetProfile":
        target_id = str(data.get("target_id", "")).strip()
        profile_url = str(data.get("profile_url", "")).strip()
        if not target_id:
            raise ValueError("target_id is required for each target")
        if not profile_url:
            raise ValueError(f"profile_url is required for target '{target_id}'")
        return cls(
            target_id=target_id,
            profile_url=profile_url,
            label=str(data.get("label", "")).strip(),
            tags=[str(x).strip() for x in data.get("tags", []) if str(x).strip()],
        )


@dataclass
class ReelSnapshot:
    target_id: str
    profile_url: str
    reel_url: str
    reel_position: int
    views: int | None
    evidence_text: str
    screenshot_path: str
    raw_json: dict[str, Any]
    error: str = ""


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


def extract_views(texts: list[str]) -> tuple[int | None, str]:
    for text in texts:
        for pat in VIEW_PATTERNS:
            m = pat.search(text or "")
            if not m:
                continue
            parsed = parse_compact_count(m.group("num"))
            if parsed is not None:
                return parsed, text
    return None, ""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            target_count INTEGER NOT NULL DEFAULT 0,
            snapshots_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS reel_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            target_id TEXT NOT NULL,
            profile_url TEXT NOT NULL,
            reel_url TEXT NOT NULL,
            reel_position INTEGER NOT NULL,
            views INTEGER,
            evidence_text TEXT NOT NULL DEFAULT '',
            screenshot_path TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_reel_snapshots_run ON reel_snapshots(run_id);
        CREATE INDEX IF NOT EXISTS idx_reel_snapshots_target ON reel_snapshots(target_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_reel_snapshots_reel ON reel_snapshots(reel_url, captured_at);
        """
    )
    conn.commit()


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value or "").strip("_.") or "unknown"


def _profile_username(profile_url: str) -> str:
    path = urlparse(profile_url).path or ""
    parts = [x for x in path.split("/") if x]
    return parts[0] if parts else ""


def _target_reels_url(profile_url: str) -> str:
    base = profile_url.strip()
    if not base.endswith("/"):
        base += "/"
    return base + "reels/"


def _dismiss_common_popups(page: Any) -> None:
    labels = [
        "Only allow essential cookies",
        "Allow all cookies",
        "Not Now",
        "Not now",
    ]
    for label in labels:
        try:
            btn = page.locator(f"button:has-text('{label}')").first
            if btn.count() > 0:
                btn.click(timeout=700)
        except Exception:
            pass


def _open_observer_context(
    *,
    playwright: Any,
    observer: ObserverConfig,
    headless: bool,
    timeout_ms: int,
) -> tuple[Any, Any, bool]:
    # Returns (browser, context, owns_context).
    if observer.cdp_url:
        browser = playwright.chromium.connect_over_cdp(observer.cdp_url, timeout=timeout_ms)
        if browser.contexts:
            return browser, browser.contexts[0], False
        return browser, browser.new_context(), False
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=observer.user_data_dir,
        executable_path=observer.executable_path or None,
        headless=headless,
        viewport={"width": 1400, "height": 2200},
    )
    return None, context, True


def _load_config(path: Path) -> tuple[ObserverConfig, list[TargetProfile]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    observer = ObserverConfig.from_dict(dict(payload.get("observer", {})))
    targets_raw = payload.get("targets", [])
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ValueError("config must contain non-empty 'targets' list")
    targets = [TargetProfile.from_dict(dict(x)) for x in targets_raw]
    return observer, targets


def _http_get_json(url: str, timeout_sec: int = 20) -> dict[str, Any]:
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body or "{}")


def _extract_adspower_cdp(data: dict[str, Any]) -> str:
    # Different AdsPower versions return slightly different fields.
    candidates = [
        data.get("ws", {}).get("puppeteer"),
        data.get("ws", {}).get("playwright"),
        data.get("ws", {}).get("cdp"),
        data.get("debug_port"),
        data.get("ws_endpoint"),
    ]
    for c in candidates:
        if not c:
            continue
        s = str(c).strip()
        if not s:
            continue
        if s.startswith("ws://") or s.startswith("wss://") or s.startswith("http://") or s.startswith("https://"):
            return s
        if s.isdigit():
            return f"http://127.0.0.1:{s}"
    return ""


def _start_adspower_profile(observer: ObserverConfig) -> str:
    base = observer.adspower_api_base.rstrip("/")
    user_id = observer.adspower_user_id.strip()
    if not user_id:
        return ""
    url = f"{base}/api/v1/browser/start?user_id={user_id}"
    payload = _http_get_json(url)
    code = payload.get("code", 0)
    if code not in (0, "0", None):
        raise RuntimeError(f"AdsPower start failed code={code} msg={payload.get('msg')}")
    data = payload.get("data") or {}
    cdp = _extract_adspower_cdp(data)
    if not cdp:
        raise RuntimeError(f"AdsPower start returned no CDP endpoint: {payload}")
    return cdp


def _stop_adspower_profile(observer: ObserverConfig) -> None:
    base = observer.adspower_api_base.rstrip("/")
    user_id = observer.adspower_user_id.strip()
    if not user_id:
        return
    url = f"{base}/api/v1/browser/stop?user_id={user_id}"
    try:
        _http_get_json(url)
    except Exception as e:
        log.warning("adspower stop failed user_id=%s error=%s", user_id, e)


def _capture_target(
    *,
    context: Any,
    target: TargetProfile,
    top_reels: int,
    shot_dir: Path,
    timeout_ms: int,
) -> tuple[list[ReelSnapshot], str]:
    snapshots: list[ReelSnapshot] = []
    username = _profile_username(target.profile_url) or target.target_id
    target_dir = shot_dir / _sanitize_name(target.target_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    profile_page = context.new_page()
    try:
        reels_url = _target_reels_url(target.profile_url)
        profile_page.goto(reels_url, wait_until="domcontentloaded", timeout=timeout_ms)
        profile_page.wait_for_timeout(2200)
        _dismiss_common_popups(profile_page)
        grid_shot = target_dir / "reels_grid.png"
        profile_page.screenshot(path=str(grid_shot), full_page=True)
        links_raw = profile_page.evaluate(REEL_LINKS_SCRIPT) or []
        links = [dict(x) for x in links_raw if isinstance(x, dict)]
        if not links:
            raise RuntimeError(f"no reel links found for {username}")
        links = links[: max(1, int(top_reels))]

        for i, row in enumerate(links, start=1):
            reel_url = str(row.get("reel_url", "")).strip()
            if not reel_url:
                continue
            reel_page = context.new_page()
            try:
                reel_page.goto(reel_url, wait_until="domcontentloaded", timeout=timeout_ms)
                reel_page.wait_for_timeout(1800)
                _dismiss_common_popups(reel_page)
                payload = reel_page.evaluate(REEL_TEXT_SCRIPT) or {}
                texts = [str(t) for t in payload.get("texts", []) if str(t).strip()]
                views, evidence = extract_views(texts)
                reel_shot = target_dir / f"reel_{i:02d}.png"
                reel_page.screenshot(path=str(reel_shot), full_page=True)
                snapshots.append(
                    ReelSnapshot(
                        target_id=target.target_id,
                        profile_url=target.profile_url,
                        reel_url=reel_url,
                        reel_position=i,
                        views=views,
                        evidence_text=evidence,
                        screenshot_path=str(reel_shot.resolve()),
                        raw_json={"profile_link_meta": row, "reel_payload": payload},
                    )
                )
            except Exception as e:
                snapshots.append(
                    ReelSnapshot(
                        target_id=target.target_id,
                        profile_url=target.profile_url,
                        reel_url=reel_url,
                        reel_position=i,
                        views=None,
                        evidence_text="",
                        screenshot_path="",
                        raw_json={"profile_link_meta": row},
                        error=str(e),
                    )
                )
            finally:
                try:
                    reel_page.close()
                except Exception:
                    pass
    finally:
        try:
            profile_page.close()
        except Exception:
            pass
    return snapshots, str(grid_shot.resolve())


def _write_sheet_csv(csv_path: Path, captured_at: str, run_id: str, snapshots: list[ReelSnapshot]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    exists = csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "captured_at",
                "run_id",
                "target_id",
                "profile_url",
                "reel_position",
                "reel_url",
                "views",
                "evidence_text",
                "screenshot_path",
                "error",
            ],
        )
        if not exists:
            writer.writeheader()
        for s in snapshots:
            writer.writerow(
                {
                    "captured_at": captured_at,
                    "run_id": run_id,
                    "target_id": s.target_id,
                    "profile_url": s.profile_url,
                    "reel_position": s.reel_position,
                    "reel_url": s.reel_url,
                    "views": s.views,
                    "evidence_text": s.evidence_text,
                    "screenshot_path": s.screenshot_path,
                    "error": s.error,
                }
            )


def _latest_dashboard_rows(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            s.target_id,
            s.profile_url,
            s.reel_url,
            s.reel_position,
            s.views,
            s.error,
            s.screenshot_path,
            (
                SELECT x.views
                FROM reel_snapshots x
                WHERE x.reel_url = s.reel_url
                  AND x.run_id != s.run_id
                  AND x.views IS NOT NULL
                ORDER BY x.captured_at DESC
                LIMIT 1
            ) AS prev_views
        FROM reel_snapshots s
        WHERE s.run_id = ?
        ORDER BY s.target_id, s.reel_position ASC
        """,
        (run_id,),
    ).fetchall()
    out = []
    for r in rows:
        cur = r["views"]
        prev = r["prev_views"]
        delta = None
        if cur is not None and prev is not None:
            delta = int(cur) - int(prev)
        out.append(
            {
                "target_id": r["target_id"],
                "profile_url": r["profile_url"],
                "reel_url": r["reel_url"],
                "reel_position": r["reel_position"],
                "views": cur,
                "prev_views": prev,
                "delta_views": delta,
                "error": r["error"],
                "screenshot_path": r["screenshot_path"],
            }
        )
    return out


def _render_dashboard_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    rows_html = []
    for r in rows:
        delta = r.get("delta_views")
        delta_txt = ""
        if delta is not None:
            sign = "+" if delta >= 0 else ""
            delta_txt = f"{sign}{delta}"
        views_txt = "-" if r.get("views") is None else str(r["views"])
        rows_html.append(
            "<tr>"
            f"<td>{escape(str(r['target_id']))}</td>"
            f"<td><a href=\"{escape(str(r['profile_url']))}\" target=\"_blank\">profile</a></td>"
            f"<td>{escape(str(r['reel_position']))}</td>"
            f"<td><a href=\"{escape(str(r['reel_url']))}\" target=\"_blank\">reel</a></td>"
            f"<td>{escape(views_txt)}</td>"
            f"<td>{escape(delta_txt)}</td>"
            f"<td>{escape(str(r.get('error') or ''))}</td>"
            "</tr>"
        )
    rows_block = "\n".join(rows_html) or "<tr><td colspan='7'>No rows.</td></tr>"
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>IG Reel Watch Dashboard</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 16px; background: #f6f8fb; color: #111; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .card {{ background: #fff; border: 1px solid #d8deea; border-radius: 10px; padding: 10px; }}
    .label {{ color: #556; font-size: 12px; }}
    .value {{ font-size: 20px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8deea; }}
    th, td {{ border-bottom: 1px solid #e5eaf2; text-align: left; padding: 8px; font-size: 13px; }}
    th {{ background: #eef3fb; }}
    a {{ color: #0057b8; text-decoration: none; }}
  </style>
</head>
<body>
  <h2>IG Reel Watch Dashboard</h2>
  <div>Run: {escape(str(summary.get('run_id', '')))} | Captured: {escape(str(summary.get('finished_at', '')))}</div>
  <div class="cards">
    <div class="card"><div class="label">Targets</div><div class="value">{summary.get('target_count', 0)}</div></div>
    <div class="card"><div class="label">Snapshots</div><div class="value">{summary.get('snapshots_count', 0)}</div></div>
    <div class="card"><div class="label">Errors</div><div class="value">{summary.get('error_count', 0)}</div></div>
    <div class="card"><div class="label">Sheet</div><div class="value"><a href="./reel_views_sheet.csv">CSV</a></div></div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Target</th><th>Profile</th><th>#</th><th>Reel</th><th>Views</th><th>Delta</th><th>Error</th>
      </tr>
    </thead>
    <tbody>{rows_block}</tbody>
  </table>
</body>
</html>"""


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    cfg_path = Path(args.config).resolve()
    out_dir = Path(args.output_dir).resolve()
    db_path = Path(args.db).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    observer, targets = _load_config(cfg_path)
    adspower_started = False
    original_cdp = observer.cdp_url
    if observer.adspower_user_id and not observer.cdp_url:
        observer.cdp_url = _start_adspower_profile(observer)
        adspower_started = True
        log.info("adspower started user_id=%s cdp=%s", observer.adspower_user_id, observer.cdp_url)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    run_id = uuid.uuid4().hex[:12]
    started_at = utc_now_iso()
    conn.execute(
        "INSERT INTO runs (run_id, started_at, status, target_count) VALUES (?, ?, ?, ?)",
        (run_id, started_at, "running", len(targets)),
    )
    conn.commit()

    all_snaps: list[ReelSnapshot] = []
    grid_shots: dict[str, str] = {}
    timeout_ms = max(5, int(args.timeout_sec)) * 1000

    with sync_playwright() as pw:
        browser = None
        context = None
        owns_context = False
        try:
            browser, context, owns_context = _open_observer_context(
                playwright=pw,
                observer=observer,
                headless=args.headless,
                timeout_ms=timeout_ms,
            )
            shot_root = out_dir / "screenshots" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for t in targets:
                try:
                    snaps, grid_shot = _capture_target(
                        context=context,
                        target=t,
                        top_reels=args.top_reels,
                        shot_dir=shot_root,
                        timeout_ms=timeout_ms,
                    )
                    grid_shots[t.target_id] = grid_shot
                    all_snaps.extend(snaps)
                    log.info("target=%s reels=%d", t.target_id, len(snaps))
                except Exception as e:
                    log.error("target=%s failed error=%s", t.target_id, e)
                    all_snaps.append(
                        ReelSnapshot(
                            target_id=t.target_id,
                            profile_url=t.profile_url,
                            reel_url="",
                            reel_position=0,
                            views=None,
                            evidence_text="",
                            screenshot_path="",
                            raw_json={},
                            error=str(e),
                        )
                    )
        finally:
            if context is not None and owns_context:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None and args.close_cdp_browser:
                try:
                    browser.close()
                except Exception:
                    pass

    captured_at = utc_now_iso()
    error_count = 0
    for s in all_snaps:
        if s.error:
            error_count += 1
        conn.execute(
            """
            INSERT INTO reel_snapshots
            (run_id, captured_at, target_id, profile_url, reel_url, reel_position, views, evidence_text, screenshot_path, error, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                captured_at,
                s.target_id,
                s.profile_url,
                s.reel_url,
                s.reel_position,
                s.views,
                s.evidence_text,
                s.screenshot_path,
                s.error,
                json.dumps(s.raw_json, ensure_ascii=True),
            ),
        )
    conn.commit()

    sheet_csv = out_dir / "dashboard" / "reel_views_sheet.csv"
    _write_sheet_csv(sheet_csv, captured_at, run_id, all_snaps)

    finished_at = utc_now_iso()
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "target_count": len(targets),
        "snapshots_count": len(all_snaps),
        "error_count": error_count,
        "db_path": str(db_path),
        "sheet_csv": str(sheet_csv),
        "grid_screenshots": grid_shots,
    }
    conn.execute(
        """
        UPDATE runs
        SET finished_at=?, status=?, snapshots_count=?, error_count=?
        WHERE run_id=?
        """,
        (
            finished_at,
            "ok" if error_count == 0 else "partial",
            len(all_snaps),
            error_count,
            run_id,
        ),
    )
    conn.commit()

    dashboard_rows = _latest_dashboard_rows(conn, run_id)
    dash_dir = out_dir / "dashboard"
    dash_dir.mkdir(parents=True, exist_ok=True)
    (dash_dir / "latest.json").write_text(
        json.dumps({"summary": summary, "rows": dashboard_rows}, indent=2),
        encoding="utf-8",
    )
    (dash_dir / "index.html").write_text(
        _render_dashboard_html(summary, dashboard_rows),
        encoding="utf-8",
    )
    conn.close()
    if adspower_started:
        _stop_adspower_profile(observer)
        observer.cdp_url = original_cdp
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Track view counts for last N reels on target IG profiles.",
    )
    parser.add_argument("--config", required=True, help="JSON config path")
    parser.add_argument("--db", default=".ig_reel_watch/ig_reel_watch.db", help="SQLite path")
    parser.add_argument("--output-dir", default=".ig_reel_watch", help="Output root")
    parser.add_argument("--top-reels", type=int, default=3, help="How many recent reels per target")
    parser.add_argument("--timeout-sec", type=int, default=45, help="Page timeout in seconds")
    parser.add_argument("--headless", action="store_true", help="Headless mode for user_data_dir observer")
    parser.add_argument("--close-cdp-browser", action="store_true", help="Close CDP browser after run")
    parser.add_argument("--interval-minutes", type=int, default=0, help="Loop interval; 0 = one run")
    parser.add_argument("--verbose", action="store_true", help="Verbose logs")
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
