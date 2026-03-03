from supergod.orchestrator.brain import Subtask, validate_subtask_dependencies


def test_validate_subtask_dependencies_accepts_dag():
    subtasks = [
        Subtask(id="1", description="Build API", depends_on=[]),
        Subtask(id="2", description="Build UI", depends_on=[]),
        Subtask(id="3", description="Integrate", depends_on=["1", "2"]),
    ]
    ok, err = validate_subtask_dependencies(subtasks)
    assert ok is True
    assert err == ""


def test_validate_subtask_dependencies_rejects_unknown_dependency():
    subtasks = [
        Subtask(id="1", description="Build API", depends_on=["does-not-exist"]),
    ]
    ok, err = validate_subtask_dependencies(subtasks)
    assert ok is False
    assert "unknown subtask" in err


def test_validate_subtask_dependencies_rejects_cycle():
    subtasks = [
        Subtask(id="1", description="Step 1", depends_on=["2"]),
        Subtask(id="2", description="Step 2", depends_on=["1"]),
    ]
    ok, err = validate_subtask_dependencies(subtasks)
    assert ok is False
    assert "Circular dependency detected" in err
