"""Import curated Claude agent markdown files into the Supergod skill library."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from supergod.skills.catalog import AGENTS_DIR, INDEX_PATH, LIBRARY_DIR, PACK_DEFINITIONS


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        return {}, text

    frontmatter_raw = match.group(1)
    body = match.group(2)
    out: dict[str, str] = {}
    for raw_line in frontmatter_raw.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip().strip("'\"")
    return out, body


def _normalize_body(body: str) -> str:
    normalized = body.replace("\r\n", "\n")
    # Normalize i2v local project roots into placeholders.
    normalized = normalized.replace(
        r"C:\Users\asus\Desktop\projects\i2v",
        "{PROJECT_ROOT}",
    )
    normalized = normalized.replace(
        "C:/Users/asus/Desktop/projects/i2v",
        "{PROJECT_ROOT}",
    )
    normalized = normalized.replace(
        r"C:\Users\asus\Desktop\projects",
        "{PROJECTS_ROOT}",
    )
    normalized = normalized.replace(
        "C:/Users/asus/Desktop/projects",
        "{PROJECTS_ROOT}",
    )
    return normalized.strip() + "\n"


def _extract_rules(body: str, limit: int = 8) -> list[str]:
    rules: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if (
            "ALWAYS" in upper
            or "NEVER" in upper
            or "DO NOT" in upper
            or "MUST" in upper
        ):
            rules.append(line.lstrip("- ").strip())
        if len(rules) >= limit:
            break
    return rules


def _extract_tags(skill_id: str, description: str, rules: list[str]) -> list[str]:
    text = " ".join([skill_id, description, *rules]).lower()
    words = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text)
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "when",
        "this",
        "that",
        "your",
        "use",
        "using",
        "agent",
        "specialist",
        "project",
        "follow",
    }
    tags: list[str] = []
    for word in words:
        if word in stop:
            continue
        if word not in tags:
            tags.append(word)
        if len(tags) >= 24:
            break
    return tags


def import_curated_agents(
    source_dir: str,
    include_project_specific: bool = True,
) -> dict[str, object]:
    src = Path(source_dir)
    if not src.exists():
        raise FileNotFoundError(f"Source agent directory not found: {src}")

    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    skills: list[dict[str, object]] = []
    missing: list[str] = []
    written: list[str] = []

    for pack_name, pack in PACK_DEFINITIONS.items():
        if pack_name == "project-i2v" and not include_project_specific:
            continue
        for skill_id in pack["skills"]:
            source_file = src / f"{skill_id}.md"
            if not source_file.exists():
                missing.append(skill_id)
                continue

            raw = source_file.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(raw)
            description = frontmatter.get("description", "").strip()
            if not description:
                description = f"Imported guidance for {skill_id}."
            normalized_body = _normalize_body(body)
            rules = _extract_rules(normalized_body)
            tags = _extract_tags(skill_id, description, rules)
            imported_name = f"{skill_id}.md"
            imported_path = AGENTS_DIR / imported_name

            rendered = (
                f"# {skill_id}\n\n"
                f"- source: `{source_file}`\n"
                f"- pack: `{pack_name}`\n\n"
                f"## Description\n\n{description}\n\n"
                f"## Instructions\n\n"
                f"{normalized_body}"
            )
            imported_path.write_text(rendered, encoding="utf-8")
            written.append(imported_name)

            skills.append(
                {
                    "id": skill_id,
                    "pack": pack_name,
                    "description": description,
                    "rules": rules,
                    "tags": tags,
                    "project_specific": skill_id.startswith("i2v-"),
                    "source_file": str(source_file),
                    "imported_file": f"agents/{imported_name}",
                }
            )

    # Deduplicate by id in case a skill appears in multiple packs.
    deduped: dict[str, dict[str, object]] = {}
    for skill in skills:
        deduped[skill["id"]] = skill
    skills = sorted(deduped.values(), key=lambda s: str(s["id"]))

    index = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "packs": PACK_DEFINITIONS,
        "skills": skills,
        "stats": {
            "total_skills": len(skills),
            "missing_skills": len(missing),
            "written_files": len(written),
        },
        "missing": sorted(missing),
    }
    INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import curated Claude agents into Supergod skill library."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Path to .claude/agents directory.",
    )
    parser.add_argument(
        "--exclude-project-specific",
        action="store_true",
        help="Skip the project-i2v pack.",
    )
    args = parser.parse_args()

    index = import_curated_agents(
        source_dir=args.source,
        include_project_specific=not args.exclude_project_specific,
    )
    print(
        json.dumps(
            {
                "index_path": str(INDEX_PATH),
                "total_skills": index["stats"]["total_skills"],
                "missing_skills": index["stats"]["missing_skills"],
            }
        )
    )


if __name__ == "__main__":
    main()
