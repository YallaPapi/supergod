"""Orchestrator brain — uses its own Codex instance for thinking.

Handles task decomposition, evaluation, and conflict resolution.
"""

import json
import logging
import re
from dataclasses import dataclass

from supergod.shared.protocol import new_id
from supergod.worker.codex_runner import run_codex_collect

log = logging.getLogger(__name__)

# Max retry attempts when Codex returns unparseable output
_MAX_PARSE_RETRIES = 1


@dataclass
class Subtask:
    id: str
    description: str
    depends_on: list[str]


def validate_subtask_dependencies(subtasks: list[Subtask]) -> tuple[bool, str]:
    """Validate dependency graph for duplicate IDs, missing deps, and cycles."""
    if not subtasks:
        return False, "no subtasks generated"

    nodes: dict[str, Subtask] = {}
    duplicates: set[str] = set()
    for subtask in subtasks:
        sid = str(subtask.id)
        if sid in nodes:
            duplicates.add(sid)
            continue
        nodes[sid] = subtask

    if duplicates:
        ordered = ", ".join(sorted(duplicates))
        return False, f"duplicate subtask id(s): {ordered}"

    missing: set[str] = set()
    for subtask in subtasks:
        for dep in subtask.depends_on:
            if dep not in nodes:
                missing.add(dep)
    if missing:
        ordered = ", ".join(sorted(missing))
        return False, f"unknown dependency id(s): {ordered}"

    # DFS cycle detection over dependency edges: subtask -> depends_on.
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def _visit(node_id: str) -> list[str] | None:
        if node_id in visited:
            return None
        if node_id in visiting:
            try:
                start = stack.index(node_id)
            except ValueError:
                start = 0
            return stack[start:] + [node_id]

        visiting.add(node_id)
        stack.append(node_id)
        for dep in nodes[node_id].depends_on:
            cycle = _visit(dep)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)
        return None

    for sid in nodes:
        cycle = _visit(sid)
        if cycle:
            return False, f"circular dependency detected: {' -> '.join(cycle)}"

    return True, ""


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
