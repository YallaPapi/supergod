"""Runtime selection and prompt injection for skill packs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from supergod.shared.config import (
    SKILLS_ENABLED,
    SKILLS_INCLUDE_PROJECT_SPECIFIC,
    SKILLS_MAX_CHARS,
    SKILLS_MAX_SKILLS,
    SKILLS_PROFILE,
)
from supergod.skills.catalog import BASE_PACKS, INDEX_PATH, PACK_DEFINITIONS


_PACK_OVERRIDE_RE = re.compile(r"(?im)^\s*packs?\s*:\s*([a-z0-9_,\- ]+)\s*$")


def _load_index(index_path: Path | None = None) -> dict[str, Any]:
    if index_path is None:
        index_path = INDEX_PATH
    if not index_path.exists():
        return {
            "packs": PACK_DEFINITIONS,
            "skills": [],
            "stats": {"total_skills": 0},
            "missing": [],
        }
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "packs": PACK_DEFINITIONS,
            "skills": [],
            "stats": {"total_skills": 0},
            "missing": [],
        }


def _normalize_text(text: str) -> str:
    return text.lower().strip()


def _parse_pack_overrides(task_prompt: str, subtask_prompt: str) -> list[str]:
    found: list[str] = []
    for candidate in (task_prompt, subtask_prompt):
        for match in _PACK_OVERRIDE_RE.findall(candidate or ""):
            for raw in match.split(","):
                name = raw.strip().lower()
                if name and name in PACK_DEFINITIONS and name not in found:
                    found.append(name)
    return found


def _select_packs(task_prompt: str, subtask_prompt: str, repo_root: str) -> list[str]:
    text = _normalize_text(f"{task_prompt}\n{subtask_prompt}\n{repo_root}")
    selected = list(BASE_PACKS)

    overrides = _parse_pack_overrides(task_prompt, subtask_prompt)
    if overrides:
        return overrides

    for pack_name, pack in PACK_DEFINITIONS.items():
        if pack_name in selected:
            continue
        if pack_name == "project-i2v" and not SKILLS_INCLUDE_PROJECT_SPECIFIC:
            continue
        if pack_name == "project-i2v":
            if "i2v" in text or "\\i2v" in text or "/i2v" in text:
                selected.append(pack_name)
            continue
        for keyword in pack.get("keywords", []):
            if keyword in text:
                selected.append(pack_name)
                break
    return selected


def _score_skill(
    skill: dict[str, Any],
    combined_text: str,
) -> int:
    score = 0
    skill_id = str(skill.get("id", ""))
    if skill_id and skill_id in combined_text:
        score += 4
    for tag in skill.get("tags", []):
        if tag and tag in combined_text:
            score += 1
    desc = str(skill.get("description", "")).lower()
    for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", desc):
        if token in combined_text:
            score += 1
    return score


def _select_skills(
    index: dict[str, Any],
    selected_packs: list[str],
    task_prompt: str,
    subtask_prompt: str,
) -> list[dict[str, Any]]:
    skills = index.get("skills", [])
    combined_text = _normalize_text(f"{task_prompt}\n{subtask_prompt}")
    filtered = [
        s
        for s in skills
        if str(s.get("pack")) in selected_packs
    ]
    filtered.sort(
        key=lambda s: (
            _score_skill(s, combined_text),
            -len(str(s.get("description", ""))),
            str(s.get("id", "")),
        ),
        reverse=True,
    )
    return filtered[:SKILLS_MAX_SKILLS]


def _render_skill_guidance(selected_skills: list[dict[str, Any]], max_chars: int) -> str:
    if not selected_skills:
        return ""
    lines: list[str] = []
    size = 0
    for skill in selected_skills:
        chunk = [
            f"- [{skill.get('id')}] {skill.get('description', '').strip()}",
        ]
        rules = [r for r in skill.get("rules", []) if r][:3]
        for rule in rules:
            chunk.append(f"  rule: {rule}")
        text = "\n".join(chunk)
        projected = size + len(text) + 1
        if projected > max_chars:
            break
        lines.append(text)
        size = projected
    if not lines:
        return ""
    return "\n".join(lines)


def build_worker_subtask_prompt(
    task_prompt: str,
    subtask_prompt: str,
    repo_root: str,
) -> tuple[str, dict[str, Any]]:
    """Inject selected capability-pack guidance into a worker subtask prompt."""
    if not SKILLS_ENABLED:
        return subtask_prompt, {
            "skills_enabled": False,
            "profile": SKILLS_PROFILE,
            "selected_packs": [],
            "selected_skills": [],
            "index_path": str(INDEX_PATH),
        }

    index = _load_index()
    packs = _select_packs(task_prompt, subtask_prompt, repo_root)
    selected_skills = _select_skills(index, packs, task_prompt, subtask_prompt)
    skill_text = _render_skill_guidance(selected_skills, SKILLS_MAX_CHARS)

    prompt = (
        "You are a Supergod worker in a homogeneous worker fleet.\n"
        "Any worker can execute any task. Use the provided capability guidance when relevant.\n\n"
        "Global task objective:\n"
        f"{task_prompt.strip()}\n\n"
        "Assigned subtask:\n"
        f"{subtask_prompt.strip()}\n\n"
        f"Active capability packs: {', '.join(packs) if packs else 'none'}\n"
    )
    if skill_text:
        prompt += "\nSkill guidance:\n" + skill_text + "\n"

    prompt += (
        "\nExecution requirements:\n"
        "- Follow existing project patterns before introducing new structure.\n"
        "- Keep edits scoped to this subtask and avoid unrelated refactors.\n"
        "- Run relevant verification commands for touched files.\n"
        "- If blocked, report exact failure reason and the minimum user action needed.\n"
    )

    metadata = {
        "skills_enabled": True,
        "profile": SKILLS_PROFILE,
        "selected_packs": packs,
        "selected_skills": [s.get("id") for s in selected_skills],
        "index_path": str(INDEX_PATH),
        "index_skill_count": int(index.get("stats", {}).get("total_skills", 0)),
    }
    return prompt, metadata
