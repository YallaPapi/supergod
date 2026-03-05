from polyedge.scheduler import run_poller, run_api_research, run_supergod_research, run_forever


def test_scheduler_functions_exist():
    """Smoke test that all scheduler functions are importable."""
    assert callable(run_poller)
    assert callable(run_api_research)
    assert callable(run_supergod_research)
    assert callable(run_forever)
