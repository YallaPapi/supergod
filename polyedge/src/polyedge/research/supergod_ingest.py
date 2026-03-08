"""Ingest research results from supergod worker task branches."""

import json
import logging
import subprocess
from pathlib import Path

from polyedge.research.ingest import parse_factors_json
from polyedge.research.store import store_factors_batch

log = logging.getLogger(__name__)


def _run_git(repo_path: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_processed_commits(marker_path: Path) -> set[str]:
    if not marker_path.exists():
        return set()
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    commits = payload.get("processed_commits", [])
    return {str(c) for c in commits}


def _save_processed_commits(marker_path: Path, commits: set[str]) -> None:
    payload = {"processed_commits": sorted(commits)}
    marker_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _infer_market_id(filepath: str) -> str | None:
    # Accept common output patterns such as market_<id>.json or .../<id>.json.
    stem = Path(filepath).stem
    for prefix in ("market_", "mkt_", "market-"):
        if stem.startswith(prefix) and len(stem) > len(prefix):
            return stem[len(prefix):]
    if stem and stem != "output":
        return stem
    return None


async def ingest_supergod_results(repo_path: str = "/opt/polyedge") -> int:
    """Check for new supergod task branches, extract research output, parse + store factors."""
    marker_path = Path(repo_path) / ".supergod_ingested_commits.json"
    processed_commits = _load_processed_commits(marker_path)

    subprocess.run(["git", "fetch", "origin"], cwd=repo_path, capture_output=True)

    result = _run_git(repo_path, ["branch", "-r", "--sort=-committerdate"])

    to_store: list[dict] = []
    for line in result.stdout.strip().split("\n"):
        branch = line.strip()
        if not branch.startswith("origin/task/"):
            continue

        commit_proc = _run_git(repo_path, ["rev-parse", branch])
        commit_sha = (commit_proc.stdout or "").strip()
        if not commit_sha or commit_sha in processed_commits:
            continue

        try:
            diff = _run_git(repo_path, ["diff", "origin/main..." + branch, "--name-only"])
            for filepath in diff.stdout.strip().split("\n"):
                if not filepath.endswith(".json"):
                    continue
                content = _run_git(repo_path, ["show", f"{branch}:{filepath}"])
                factors = parse_factors_json(
                    content.stdout,
                    source="codex",
                    market_id=_infer_market_id(filepath),
                )
                to_store.extend(factors)
            processed_commits.add(commit_sha)
        except Exception as e:
            log.warning("Failed to process branch %s: %s", branch, e)

    factors_ingested = await store_factors_batch(to_store)
    _save_processed_commits(marker_path, processed_commits)
    return factors_ingested
