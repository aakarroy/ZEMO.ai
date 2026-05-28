"""
json_validator.py

Multi-strategy JSON extraction from raw text strings.

Used as a standalone utility by both the pipeline stages and the repair engine.
Provides a richer result type than a simple bool — includes the parsed data
and a descriptive error message for debugging.
"""

import json
import re
from dataclasses import dataclass


@dataclass
class JSONValidationResult:
    """
    Result of attempting to extract and parse JSON from raw text.

    Attributes:
        is_valid : True if JSON was successfully extracted and parsed.
        data     : The parsed Python object (dict or list). None if is_valid=False.
        error    : Human-readable failure reason. None if is_valid=True.
        strategy : Which extraction strategy succeeded (1-4). 0 if failed.
    """
    is_valid: bool
    data: dict | list | None
    error: str | None
    strategy: int = 0


def validate_and_extract_json(text: str) -> JSONValidationResult:
    """
    Attempt to extract valid JSON from raw text using 4 strategies.

    This function is the single source of truth for JSON extraction.
    Both PipelineStage and RepairEngine use this (via their own
    private _extract_json_object/_extract_json_array wrappers).

    Strategies attempted in order:
      1. Direct json.loads() — handles perfectly-formed responses
      2. Strip markdown code fences (``` or ```json) — handles LLM markdown
      3. Find first { and last } or [ and ] — handles text wrapping JSON
      4. Unwrap common envelope keys — handles {"result": {...}} patterns

    Args:
        text: Raw string, possibly containing JSON mixed with other text.

    Returns:
        JSONValidationResult with is_valid, data, error, strategy.
    """
    if not text or not text.strip():
        return JSONValidationResult(
            is_valid=False, data=None,
            error="Input text is empty or whitespace only.", strategy=0
        )

    text = text.strip()

    # ── Strategy 1: Direct parse ────────────────────────────────────
    try:
        data = json.loads(text)
        return JSONValidationResult(is_valid=True, data=data, error=None, strategy=1)
    except json.JSONDecodeError:
        pass

    # ── Strategy 2: Strip markdown fences ──────────────────────────
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"\s*```", "", cleaned).strip()
    if cleaned != text:  # Only retry if stripping changed something
        try:
            data = json.loads(cleaned)
            return JSONValidationResult(
                is_valid=True, data=data, error=None, strategy=2
            )
        except json.JSONDecodeError:
            pass

    # Find first occurrences
    brace_start = text.find("{")
    bracket_start = text.find("[")
    
    # Determine which comes first (ignoring -1)
    try_array_first = False
    if bracket_start != -1:
        if brace_start == -1 or bracket_start < brace_start:
            try_array_first = True

    def try_object():
        b_start = text.find("{")
        b_end = text.rfind("}")
        if b_start != -1 and b_end > b_start:
            try:
                data = json.loads(text[b_start : b_end + 1])
                return JSONValidationResult(is_valid=True, data=data, error=None, strategy=3)
            except json.JSONDecodeError:
                pass
        return None

    def try_array():
        b_start = text.find("[")
        b_end = text.rfind("]")
        if b_start != -1 and b_end > b_start:
            try:
                data = json.loads(text[b_start : b_end + 1])
                return JSONValidationResult(is_valid=True, data=data, error=None, strategy=3)
            except json.JSONDecodeError:
                pass
        return None

    # ── Strategy 3: Find outermost object or array ──────────────────────
    if try_array_first:
        res = try_array()
        if res: return res
        res = try_object()
        if res: return res
    else:
        res = try_object()
        if res: return res
        res = try_array()
        if res: return res

    # ── Strategy 4: Unwrap common envelope keys ─────────────────────
    envelope_keys = (
        "result", "schema", "output", "data", "response",
        "items", "list", "records", "pages", "endpoints",
        "tables", "roles", "rules", "content"
    )
    try:
        # Try to parse the outer structure first
        outer = json.loads(cleaned if cleaned else text)
        if isinstance(outer, dict):
            for key in envelope_keys:
                if key in outer:
                    inner = outer[key]
                    if isinstance(inner, (dict, list)):
                        return JSONValidationResult(
                            is_valid=True, data=inner, error=None, strategy=4
                        )
    except json.JSONDecodeError:
        pass

    # ── All strategies failed ───────────────────────────────────────
    preview = text[:100].replace("\n", " ")
    return JSONValidationResult(
        is_valid=False,
        data=None,
        error=(
            f"All 4 JSON extraction strategies failed. "
            f"Text preview: '{preview}...'"
        ),
        strategy=0
    )
