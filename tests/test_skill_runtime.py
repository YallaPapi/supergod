"""Tests for runtime skill-pack prompt injection."""

import json

from supergod.skills import runtime


def test_build_worker_subtask_prompt_injects_guidance(tmp_path, monkeypatch):
    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "stats": {"total_skills": 1},
                "skills": [
                    {
                        "id": "feature-implementer",
                        "pack": "core-dev",
                        "description": "Implements features with tests.",
                        "rules": ["ALWAYS run tests"],
                        "tags": ["feature", "test"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "INDEX_PATH", index_path)
    monkeypatch.setattr(runtime, "SKILLS_ENABLED", True)
    monkeypatch.setattr(runtime, "SKILLS_MAX_SKILLS", 4)
    monkeypatch.setattr(runtime, "SKILLS_MAX_CHARS", 2000)

    prompt, meta = runtime.build_worker_subtask_prompt(
        task_prompt="Implement auth API with tests",
        subtask_prompt="Build login endpoint",
        repo_root="/workspace/repo",
    )

    assert "Active capability packs: core-dev" in prompt
    assert "feature-implementer" in prompt
    assert "ALWAYS run tests" in prompt
    assert "core-dev" in meta["selected_packs"]
    assert "feature-implementer" in meta["selected_skills"]


def test_pack_override_is_respected(tmp_path, monkeypatch):
    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "stats": {"total_skills": 1},
                "skills": [
                    {
                        "id": "docker-container-admin",
                        "pack": "infra-ops",
                        "description": "Docker operations.",
                        "rules": [],
                        "tags": ["docker"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "INDEX_PATH", index_path)
    monkeypatch.setattr(runtime, "SKILLS_ENABLED", True)

    prompt, meta = runtime.build_worker_subtask_prompt(
        task_prompt="packs: infra-ops\nDeploy this service",
        subtask_prompt="Add docker compose",
        repo_root="/workspace/repo",
    )

    assert "Active capability packs: infra-ops" in prompt
    assert meta["selected_packs"] == ["infra-ops"]


def test_active_capability_packs_override_is_respected(tmp_path, monkeypatch):
    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "stats": {"total_skills": 1},
                "skills": [
                    {
                        "id": "dependency-resolver",
                        "pack": "core-dev",
                        "description": "Dependency analysis.",
                        "rules": [],
                        "tags": ["dependency"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "INDEX_PATH", index_path)
    monkeypatch.setattr(runtime, "SKILLS_ENABLED", True)

    prompt, meta = runtime.build_worker_subtask_prompt(
        task_prompt="Parallel execution context",
        subtask_prompt="Active capability packs: core-dev, project-i2v",
        repo_root="/workspace/repo",
    )

    assert "Active capability packs: core-dev, project-i2v" in prompt
    assert meta["selected_packs"] == ["core-dev", "project-i2v"]


def test_skills_disabled_returns_original_prompt(monkeypatch):
    monkeypatch.setattr(runtime, "SKILLS_ENABLED", False)
    subtask = "Implement health check endpoint"
    prompt, meta = runtime.build_worker_subtask_prompt(
        task_prompt="Build API",
        subtask_prompt=subtask,
        repo_root="/workspace/repo",
    )
    assert prompt == subtask
    assert meta["skills_enabled"] is False
