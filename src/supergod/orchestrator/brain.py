"""Orchestrator brain — uses its own Codex instance for thinking.

Handles task decomposition, evaluation, and conflict resolution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from supergod.shared.protocol import new_id
from supergod.worker.codex_runner import run_codex_collect

log = logging.getLogger(__name__)


@dataclass
class Subtask:
    id: str
    description: str
    depends_on: list[str]


DECOMPOSE_PROMPT = """You are a software architect. Break the following task into independent subtasks that can be worked on in parallel by separate developers. Each developer has their own copy of the codebase.

CRITICAL RULES:
- Make subtasks as independent as possible to avoid merge conflicts
- Each subtask should work on different files/directories when possible
- Include clear file paths and module names in each subtask description
- If subtasks have dependencies (B needs A to finish first), specify them

Output ONLY valid JSON — no markdown, no explanation. Format:
[
  {{"id": "1", "description": "Implement X in src/x/", "depends_on": []}},
  {{"id": "2", "description": "Implement Y in src/y/", "depends_on": []}},
  {{"id": "3", "description": "Wire up X and Y in src/main.py", "depends_on": ["1", "2"]}}
]

TASK: {prompt}"""


async def decompose_task(
    prompt: str, workdir: str = "."
) -> list[Subtask]:
    """Use Codex to break a high-level task into subtasks."""
    full_prompt = DECOMPOSE_PROMPT.format(prompt=prompt)

    result = await run_codex_collect(
        prompt=full_prompt,
        workdir=workdir,
    )

    if result.return_code != 0:
        raise RuntimeError(f"Decomposition failed: {result.final_message}")

    # Parse the JSON from the final message
    text = result.final_message.strip()

    # Try to extract JSON array from the response
    try:
        # Handle case where codex wraps in markdown code blocks
        if "```" in text:
            start = text.index("[")
            end = text.rindex("]") + 1
            text = text[start:end]
        items = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Failed to parse decomposition output: %s", text)
        raise RuntimeError(f"Failed to parse subtasks: {e}") from e

    subtasks = []
    for item in items:
        subtasks.append(
            Subtask(
                id=item.get("id", new_id()),
                description=item["description"],
                depends_on=item.get("depends_on", []),
            )
        )

    log.info("Decomposed into %d subtasks", len(subtasks))
    return subtasks


EVALUATE_PROMPT = """You are reviewing the results of a software development task.

Original task: {original_prompt}

Test output:
{test_output}

If tests passed, respond with ONLY: {{"status": "success", "summary": "brief summary"}}
If tests failed, respond with ONLY: {{"status": "failure", "summary": "what went wrong", "fix_tasks": [{{"description": "what to fix"}}]}}

Output ONLY valid JSON — no markdown, no explanation."""


async def evaluate_results(
    original_prompt: str, test_output: str, workdir: str = "."
) -> dict:
    """Use Codex to evaluate test results and determine next steps."""
    full_prompt = EVALUATE_PROMPT.format(
        original_prompt=original_prompt, test_output=test_output
    )

    result = await run_codex_collect(prompt=full_prompt, workdir=workdir)

    text = result.final_message.strip()
    try:
        if "```" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            text = text[start:end]
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.error("Failed to parse evaluation: %s", text)
        return {"status": "unknown", "summary": text}
