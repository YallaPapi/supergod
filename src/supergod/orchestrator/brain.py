"""Orchestrator brain — uses its own Codex instance for thinking.

Handles task decomposition, evaluation, and conflict resolution.
"""

import json
import logging
import re
from dataclasses import dataclass

from supergod.shared.config import BRAIN_PARSE_RETRIES
from supergod.shared.protocol import new_id
from supergod.worker.codex_runner import run_codex_collect

log = logging.getLogger(__name__)

# Max retry attempts when Codex returns unparseable output
_MAX_PARSE_RETRIES = max(0, BRAIN_PARSE_RETRIES)


@dataclass
class Subtask:
    id: str
    description: str
    depends_on: list[str]


def _extract_json_array(text: str) -> list:
    """Extract a JSON array from text that may contain markdown or other noise.

    Tries multiple strategies:
    1. Direct parse of full text
    2. Regex match for outermost [...] brackets
    3. Strip markdown code fences then retry
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: regex for outermost square brackets
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON array found", text, 0)


def _extract_json_object(text: str) -> dict:
    """Extract a JSON object from text that may contain markdown or other noise.

    Tries multiple strategies:
    1. Direct parse of full text
    2. Regex match for outermost {...} brackets
    3. Strip markdown code fences then retry
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: regex for outermost curly brackets
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON object found", text, 0)


DECOMPOSE_PROMPT = """Output ONLY a raw JSON array, nothing else. Each item has id, description, depends_on fields. Make subtasks work on different files to avoid conflicts. Decompose this task into independent subtasks: {prompt}"""


DECOMPOSE_RETRY_PROMPT = """Output ONLY a raw JSON array. No markdown, no explanation. Each item: {{"id": "1", "description": "...", "depends_on": []}}. Task: {prompt}"""


async def decompose_task(
    prompt: str, workdir: str = "."
) -> list[Subtask]:
    """Use Codex to break a high-level task into subtasks.

    Falls back to treating the entire prompt as a single subtask if
    decomposition fails (e.g. simple tasks, Codex returns non-JSON).
    """
    full_prompt = DECOMPOSE_PROMPT.format(prompt=prompt)

    for attempt in range(_MAX_PARSE_RETRIES + 1):
        try:
            result = await run_codex_collect(
                prompt=full_prompt,
                workdir=workdir,
            )
        except Exception as e:
            log.warning("Codex failed during decomposition: %s. Falling back to single subtask.", e)
            return [Subtask(id=new_id(), description=prompt, depends_on=[])]

        if result.return_code != 0:
            log.warning("Decomposition failed (rc=%d): %s. Falling back to single subtask.",
                        result.return_code, result.final_message)
            return [Subtask(id=new_id(), description=prompt, depends_on=[])]

        text = result.final_message.strip()
        if not text:
            log.warning("Decomposition returned empty output (%d events). Falling back to single subtask.",
                        len(result.events))
            return [Subtask(id=new_id(), description=prompt, depends_on=[])]

        try:
            items = _extract_json_array(text)
            break
        except json.JSONDecodeError:
            if attempt < _MAX_PARSE_RETRIES:
                log.warning(
                    "Failed to parse decomposition (attempt %d), retrying with simpler prompt. "
                    "Raw output: %s",
                    attempt + 1,
                    text[:500],
                )
                full_prompt = DECOMPOSE_RETRY_PROMPT.format(prompt=prompt)
                continue
            log.warning("Failed to parse decomposition after retries. Falling back to single subtask. "
                        "Raw output: %s", text[:500])
            return [Subtask(id=new_id(), description=prompt, depends_on=[])]

    subtasks = []
    for item in items:
        if not isinstance(item, dict):
            log.warning("Skipping non-dict subtask item: %s", item)
            continue
        desc = item.get("description")
        if not desc:
            log.warning("Skipping subtask with no description: %s", item)
            continue
        subtasks.append(
            Subtask(
                id=item.get("id", new_id()),
                description=desc,
                depends_on=item.get("depends_on", []),
            )
        )

    if not subtasks:
        log.warning("Decomposition produced 0 valid subtasks from %d items. Falling back.", len(items))
        return [Subtask(id=new_id(), description=prompt, depends_on=[])]

    log.info("Decomposed into %d subtasks", len(subtasks))
    return subtasks


EVALUATE_PROMPT = """You are reviewing the results of a software development task.

Original task: {original_prompt}

Test output:
{test_output}

If tests passed, respond with ONLY: {{"status": "success", "summary": "brief summary"}}
If tests failed, respond with ONLY: {{"status": "failure", "summary": "what went wrong", "fix_tasks": [{{"description": "what to fix"}}]}}

Output ONLY valid JSON — no markdown, no explanation."""


EVALUATE_RETRY_PROMPT = """Your previous response was not valid JSON. Respond with ONLY a JSON object, nothing else.

If tests passed: {{"status": "success", "summary": "brief summary"}}
If tests failed: {{"status": "failure", "summary": "what went wrong", "fix_tasks": [{{"description": "what to fix"}}]}}"""


async def evaluate_results(
    original_prompt: str, test_output: str, workdir: str = "."
) -> dict:
    """Use Codex to evaluate test results and determine next steps."""
    full_prompt = EVALUATE_PROMPT.format(
        original_prompt=original_prompt, test_output=test_output
    )

    for attempt in range(_MAX_PARSE_RETRIES + 1):
        result = await run_codex_collect(prompt=full_prompt, workdir=workdir)

        text = result.final_message.strip()
        if not text:
            log.warning("Evaluation returned empty output")
            return {"status": "unknown", "summary": "Empty response from evaluator"}

        try:
            return _extract_json_object(text)
        except json.JSONDecodeError:
            if attempt < _MAX_PARSE_RETRIES:
                log.warning(
                    "Failed to parse evaluation (attempt %d), retrying. Raw: %s",
                    attempt + 1,
                    text[:500],
                )
                full_prompt = EVALUATE_RETRY_PROMPT
                continue

            log.error("Failed to parse evaluation after retries. Raw: %s", text[:1000])
            return {
                "status": "unknown",
                "summary": f"Failed to parse evaluator output: {text[:300]}",
            }


REPLAN_PROMPT = """You are replanning an in-flight software task.

Original task:
{original_prompt}

Completed subtasks:
{completed_summary}

Remaining subtasks:
{remaining_summary}

Return ONLY valid JSON object with this schema:
{{
  "action": "continue" | "cancel_remaining" | "add_subtasks" | "replace_remaining",
  "reason": "brief reason",
  "subtasks": [
    {{
      "id": "optional short id",
      "description": "required when adding",
      "depends_on": ["optional short/full ids"]
    }}
  ]
}}

Rules:
- Prefer "continue" unless there is a clear reason to change plan.
- Use add/replace only when new work is required.
- Keep subtasks independent when possible.
- Output raw JSON only.
"""

REPLAN_RETRY_PROMPT = """Respond with ONLY a valid JSON object.
Required keys: "action", "reason".
Valid actions: continue, cancel_remaining, add_subtasks, replace_remaining.
If adding work, include "subtasks" list with description and optional depends_on.
"""


async def replan_check(
    original_prompt: str,
    completed_subtasks: list[dict],
    remaining_subtasks: list[dict],
    workdir: str = ".",
) -> dict:
    """Ask brain whether the remaining plan should change."""
    completed_summary = "\n".join(
        f"- {s.get('subtask_id', s.get('id', '?'))}: {s.get('prompt', s.get('description', ''))}"
        for s in completed_subtasks
    ) or "- none"
    remaining_summary = "\n".join(
        f"- {s.get('subtask_id', s.get('id', '?'))}: {s.get('prompt', s.get('description', ''))}"
        for s in remaining_subtasks
    ) or "- none"

    full_prompt = REPLAN_PROMPT.format(
        original_prompt=original_prompt,
        completed_summary=completed_summary,
        remaining_summary=remaining_summary,
    )

    for attempt in range(_MAX_PARSE_RETRIES + 1):
        result = await run_codex_collect(prompt=full_prompt, workdir=workdir)
        text = result.final_message.strip()
        if not text:
            return {"action": "continue", "reason": "Empty response from replanner"}
        try:
            plan = _extract_json_object(text)
            action = plan.get("action")
            if action not in {
                "continue",
                "cancel_remaining",
                "add_subtasks",
                "replace_remaining",
            }:
                return {
                    "action": "continue",
                    "reason": f"Invalid replanner action: {action!r}",
                }
            if "reason" not in plan or not str(plan.get("reason", "")).strip():
                plan["reason"] = "No reason provided"
            if "subtasks" in plan and not isinstance(plan["subtasks"], list):
                plan["subtasks"] = []
            return plan
        except json.JSONDecodeError:
            if attempt < _MAX_PARSE_RETRIES:
                full_prompt = REPLAN_RETRY_PROMPT
                continue
            return {"action": "continue", "reason": "Failed to parse replanner output"}
