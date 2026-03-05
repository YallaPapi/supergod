import pytest
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
