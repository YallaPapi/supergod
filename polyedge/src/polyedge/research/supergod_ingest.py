"""Ingest research results from supergod worker task branches."""

import logging
import subprocess

from polyedge.research.ingest import parse_factors_json

log = logging.getLogger(__name__)


async def ingest_supergod_results(repo_path: str = "/opt/polyedge") -> int:
    """Check for new supergod task branches, extract research output, parse factors."""
    subprocess.run(["git", "fetch", "origin"], cwd=repo_path, capture_output=True)

    result = subprocess.run(
        ["git", "branch", "-r", "--sort=-committerdate"],
        cwd=repo_path, capture_output=True, text=True,
    )

    factors_ingested = 0
    for line in result.stdout.strip().split("\n"):
        branch = line.strip()
        if not branch.startswith("origin/task/"):
            continue

        try:
            diff = subprocess.run(
                ["git", "diff", "origin/main..." + branch, "--name-only"],
                cwd=repo_path, capture_output=True, text=True,
            )
            for filepath in diff.stdout.strip().split("\n"):
                if not filepath.endswith(".json"):
                    continue
                content = subprocess.run(
                    ["git", "show", f"{branch}:{filepath}"],
                    cwd=repo_path, capture_output=True, text=True,
                )
                factors = parse_factors_json(content.stdout, source="codex")
                factors_ingested += len(factors)
        except Exception as e:
            log.warning("Failed to process branch %s: %s", branch, e)

    return factors_ingested
