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


def _normalize_depends_on(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    deps: list[str] = []
    for dep in raw:
        if isinstance(dep, str) and dep:
            deps.append(dep)
    return deps


def _normalize_subtasks(subtasks: list[Subtask]) -> list[Subtask]:
    """Normalize dependency lists and drop invalid dependency references."""
    known_ids = {s.id for s in subtasks}
    normalized: list[Subtask] = []

    for st in subtasks:
        seen: set[str] = set()
        cleaned_deps: list[str] = []
        for dep in st.depends_on:
            if dep == st.id:
                continue
            if dep not in known_ids:
                continue
            if dep in seen:
                continue
            seen.add(dep)
            cleaned_deps.append(dep)
        normalized.append(
            Subtask(id=st.id, description=st.description, depends_on=cleaned_deps)
        )
    return normalized


def _has_duplicate_subtask_ids(subtasks: list[Subtask]) -> bool:
    ids = [s.id for s in subtasks]
    return len(ids) != len(set(ids))


def _find_dependency_cycle(subtasks: list[Subtask]) -> list[str] | None:
    """Return a cycle path if one exists, otherwise None."""
    deps_by_id = {s.id: s.depends_on for s in subtasks}
    state: dict[str, int] = {}  # 0/absent=unvisited, 1=visiting, 2=done
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        state[node] = 1
        path.append(node)
        for dep in deps_by_id.get(node, []):
            dep_state = state.get(dep, 0)
            if dep_state == 0:
                cycle = dfs(dep)
                if cycle:
                    return cycle
            elif dep_state == 1:
                start = path.index(dep)
                return path[start:] + [dep]
        path.pop()
        state[node] = 2
        return None

    for subtask_id in deps_by_id:
        if state.get(subtask_id, 0) == 0:
            cycle = dfs(subtask_id)
            if cycle:
                return cycle
    return None


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
                depends_on=_normalize_depends_on(item.get("depends_on", [])),
            )
        )

    if not subtasks:
        log.warning("Decomposition produced 0 valid subtasks from %d items. Falling back.", len(items))
        return [Subtask(id=new_id(), description=prompt, depends_on=[])]

    if _has_duplicate_subtask_ids(subtasks):
        log.warning(
            "Decomposition produced duplicate subtask IDs. Falling back to single subtask."
        )
        return [Subtask(id=new_id(), description=prompt, depends_on=[])]

    subtasks = _normalize_subtasks(subtasks)
    cycle = _find_dependency_cycle(subtasks)
    if cycle:
        log.warning(
            "Decomposition produced circular dependencies (%s). Falling back to single subtask.",
            " -> ".join(cycle),
        )
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
