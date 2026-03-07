import pytest
from unittest.mock import patch
from polyedge.research.supergod import build_research_prompt, PROMPT_TEMPLATES


def test_build_research_prompt_market():
    prompt = build_research_prompt(
        category="historical_precedent",
        market_question="Will Bitcoin hit $100k by July?",
        yes_price=0.65,
    )
    assert "Will Bitcoin hit $100k by July?" in prompt
    assert "65.0%" in prompt
    assert "JSON" in prompt


def test_build_research_prompt_global_sweep():
    prompt = build_research_prompt(category="global_sweep", today="2026-03-05")
    assert "weather" in prompt.lower()
    assert "JSON" in prompt
    assert "2026-03-05" in prompt


def test_build_research_prompt_contrarian():
    prompt = build_research_prompt(
        category="contrarian_analysis",
        market_question="Will X happen?",
        yes_price=0.8,
    )
    assert "YES" in prompt  # consensus should be YES at 80%
    assert "80.0%" in prompt


def test_build_research_prompt_unknown_category():
    with pytest.raises(ValueError, match="Unknown prompt category"):
        build_research_prompt(category="nonexistent")


def test_all_templates_have_json_instruction():
    for name, template in PROMPT_TEMPLATES.items():
        assert "JSON" in template, f"Template {name} missing JSON instruction"


@pytest.mark.asyncio
async def test_submit_to_supergod_normalizes_ws_client_suffix():
    class _DummyWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send(self, _msg):
            return None

        async def recv(self):
            return '{"type":"task_accepted","task_id":"t1"}'

    connect_calls = []

    def _connect(url, close_timeout=10):  # noqa: ARG001
        connect_calls.append(url)
        return _DummyWS()

    with patch("websockets.connect", new=_connect):
        from polyedge.research.supergod import submit_to_supergod
        task_id = await submit_to_supergod("x", orchestrator_url="ws://89.167.99.187:8080")

    assert task_id == "t1"
    assert connect_calls == ["ws://89.167.99.187:8080/ws/client"]
