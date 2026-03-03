"""Tests for orchestrator brain decomposition safeguards."""

from supergod.orchestrator import brain
from supergod.worker.codex_runner import CodexResult


def _mock_result(message: str) -> CodexResult:
    return CodexResult(events=[], final_message=message, return_code=0)


async def test_decompose_task_falls_back_on_circular_dependencies(monkeypatch):
    async def fake_run_codex_collect(prompt: str, workdir: str = "."):
        return _mock_result(
            """
            [
              {"id": "1", "description": "Step 1", "depends_on": ["2"]},
              {"id": "2", "description": "Step 2", "depends_on": ["1"]}
            ]
            """
        )

    monkeypatch.setattr(brain, "run_codex_collect", fake_run_codex_collect)

    result = await brain.decompose_task("Build feature", workdir=".")
    assert len(result) == 1
    assert result[0].description == "Build feature"
    assert result[0].depends_on == []


async def test_decompose_task_falls_back_on_duplicate_ids(monkeypatch):
    async def fake_run_codex_collect(prompt: str, workdir: str = "."):
        return _mock_result(
            """
            [
              {"id": "1", "description": "Step 1", "depends_on": []},
              {"id": "1", "description": "Step 2", "depends_on": []}
            ]
            """
        )

    monkeypatch.setattr(brain, "run_codex_collect", fake_run_codex_collect)

    result = await brain.decompose_task("Build feature", workdir=".")
    assert len(result) == 1
    assert result[0].description == "Build feature"
    assert result[0].depends_on == []


async def test_decompose_task_normalizes_invalid_dependencies(monkeypatch):
    async def fake_run_codex_collect(prompt: str, workdir: str = "."):
        return _mock_result(
            """
            [
              {"id": "1", "description": "Step 1", "depends_on": ["1", "2", "missing", "2"]},
              {"id": "2", "description": "Step 2", "depends_on": "not-a-list"}
            ]
            """
        )

    monkeypatch.setattr(brain, "run_codex_collect", fake_run_codex_collect)

    result = await brain.decompose_task("Build feature", workdir=".")
    assert len(result) == 2
    by_id = {s.id: s for s in result}
    assert by_id["1"].depends_on == ["2"]
    assert by_id["2"].depends_on == []
