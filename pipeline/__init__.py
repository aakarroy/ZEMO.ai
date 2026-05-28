"""
pipeline/__init__.py

Base infrastructure for the multi-stage LLM pipeline.

Provides:
  - StageValidationError: raised when Claude returns data that cannot
    be validated against a Pydantic model.
  - PipelineStage: base class inherited by all 4 pipeline stage classes.
    Provides call_claude() and call_claude_list() methods.

All methods are synchronous. No async/await anywhere in this codebase.
"""

import json
import re
import time
from typing import Any, TypeVar
import anthropic
from pydantic import BaseModel, ValidationError

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    CLAUDE_TEMPERATURE,
    CLAUDE_MAX_TOKENS,
)

# TypeVar for generic return type in call_claude methods
T = TypeVar("T", bound=BaseModel)


# ═══════════════════════════════════════════════════════════════════
# CUSTOM EXCEPTION
# ═══════════════════════════════════════════════════════════════════

class StageValidationError(Exception):
    """
    Raised when a pipeline stage's Claude response cannot be
    parsed as JSON or validated against its Pydantic model.

    Attributes:
        stage_name      : Which stage failed (e.g. "intent_extraction")
        raw_response    : The exact text Claude returned (for debugging)
        validation_errors: Pydantic error JSON string or parse error message
        attempted_model : The class name of the Pydantic model we tried
    """

    def __init__(
        self,
        stage_name: str,
        raw_response: str,
        validation_errors: str,
        attempted_model: str = "unknown",
    ):
        self.stage_name = stage_name
        self.raw_response = raw_response
        self.validation_errors = validation_errors
        self.attempted_model = attempted_model
        super().__init__(
            f"[{stage_name}] Failed to validate against {attempted_model}. "
            f"Error: {validation_errors[:200]}. "
            f"Raw response (first 300 chars): {raw_response[:300]}"
        )


# ═══════════════════════════════════════════════════════════════════
# BASE PIPELINE STAGE
# ═══════════════════════════════════════════════════════════════════

class PipelineStage:
    """
    Base class for all pipeline stages.

    Subclasses must set class attribute `stage_name` to a string
    identifying the stage (used in error messages and logging).

    Usage:
        class IntentExtractor(PipelineStage):
            stage_name = "intent_extraction"

            def extract(self, prompt: str) -> tuple[IntentModel, int, float]:
                return self.call_claude(
                    system_prompt=INTENT_SYSTEM_PROMPT,
                    user_content=prompt,
                    response_model=IntentModel
                )
    """

    stage_name: str = "base_stage"

    def __init__(self):
        """Initialise Anthropic client once per stage instance."""
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ───────────────────────────────────────────────────────────────
    # PUBLIC API
    # ───────────────────────────────────────────────────────────────

    def call_claude(
        self,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[T, int, float]:
        """
        Make a single Claude API call and validate the response against
        a Pydantic BaseModel.

        Args:
            system_prompt  : The system instruction (role + JSON schema).
            user_content   : The user message (data to process).
            response_model : A Pydantic BaseModel subclass (e.g. IntentModel).
            temperature    : Override config default (default: CLAUDE_TEMPERATURE=0.2).
            max_tokens     : Override config default (default: CLAUDE_MAX_TOKENS=4000).

        Returns:
            tuple of (validated_model_instance, tokens_used, latency_ms)

        Raises:
            StageValidationError: If JSON cannot be extracted or Pydantic validation fails.
            anthropic.APIError:   If the Anthropic API call itself fails (network, auth, etc.)
        """
        _temperature = temperature if temperature is not None else CLAUDE_TEMPERATURE
        _max_tokens = max_tokens if max_tokens is not None else CLAUDE_MAX_TOKENS

        # Append the critical JSON-only instruction to EVERY system prompt.
        # This is non-negotiable — Claude must never return markdown here.
        enforced_system = (
            system_prompt.rstrip()
            + "\n\n"
            + "═" * 60
            + "\n"
            + "CRITICAL OUTPUT REQUIREMENT:\n"
            + "Your response must be ONLY a raw JSON object.\n"
            + "• Start your response with the character: {\n"
            + "• End your response with the character: }\n"
            + "• NO markdown code fences (no ``` or ```json)\n"
            + "• NO explanation text before or after the JSON\n"
            + "• NO comments inside the JSON\n"
            + "• The JSON must be valid and parseable by Python's json.loads()\n"
            + "═" * 60
        )

        t_start = time.perf_counter()

        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=_max_tokens,
            temperature=_temperature,
            system=enforced_system,
            messages=[{"role": "user", "content": user_content}],
        )

        latency_ms = (time.perf_counter() - t_start) * 1000
        tokens_used = message.usage.input_tokens + message.usage.output_tokens
        raw_text = message.content[0].text.strip()

        print(
            f"  [{self.stage_name}] Claude responded: "
            f"{tokens_used} tokens, {latency_ms:.0f}ms, "
            f"{len(raw_text)} chars"
        )

        # Extract JSON dict from raw text
        parsed = self._extract_json_object(raw_text)
        if parsed is None:
            raise StageValidationError(
                stage_name=self.stage_name,
                raw_response=raw_text,
                validation_errors="All 4 JSON extraction strategies failed. "
                                  "Claude returned non-JSON content.",
                attempted_model=response_model.__name__,
            )

        # Validate against Pydantic model
        try:
            validated = response_model(**parsed)
        except ValidationError as e:
            raise StageValidationError(
                stage_name=self.stage_name,
                raw_response=raw_text,
                validation_errors=e.json(),
                attempted_model=response_model.__name__,
            )

        return validated, tokens_used, latency_ms

    def call_claude_list(
        self,
        system_prompt: str,
        user_content: str,
        item_model: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[list[T], int, float]:
        """
        Make a single Claude API call and validate the response as a
        JSON array where each element validates against item_model.

        Args:
            system_prompt : The system instruction.
            user_content  : The user message.
            item_model    : A Pydantic BaseModel subclass for each list item.
            temperature   : Override default.
            max_tokens    : Override default.

        Returns:
            tuple of (list_of_validated_items, tokens_used, latency_ms)

        Raises:
            StageValidationError: If JSON array cannot be extracted or items fail validation.
        """
        _temperature = temperature if temperature is not None else CLAUDE_TEMPERATURE
        _max_tokens = max_tokens if max_tokens is not None else CLAUDE_MAX_TOKENS

        enforced_system = (
            system_prompt.rstrip()
            + "\n\n"
            + "═" * 60
            + "\n"
            + "CRITICAL OUTPUT REQUIREMENT:\n"
            + "Your response must be ONLY a raw JSON array.\n"
            + "• Start your response with the character: [\n"
            + "• End your response with the character: ]\n"
            + "• NO markdown code fences (no ``` or ```json)\n"
            + "• NO explanation text before or after the JSON\n"
            + "• NO comments inside the JSON\n"
            + "• Each element in the array must match the schema provided\n"
            + "• The JSON must be valid and parseable by Python's json.loads()\n"
            + "═" * 60
        )

        t_start = time.perf_counter()

        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=_max_tokens,
            temperature=_temperature,
            system=enforced_system,
            messages=[{"role": "user", "content": user_content}],
        )

        latency_ms = (time.perf_counter() - t_start) * 1000
        tokens_used = message.usage.input_tokens + message.usage.output_tokens
        raw_text = message.content[0].text.strip()

        print(
            f"  [{self.stage_name}] Claude responded: "
            f"{tokens_used} tokens, {latency_ms:.0f}ms, "
            f"{len(raw_text)} chars"
        )

        # Extract JSON array from raw text
        parsed = self._extract_json_array(raw_text)
        if parsed is None:
            raise StageValidationError(
                stage_name=self.stage_name,
                raw_response=raw_text,
                validation_errors="All JSON array extraction strategies failed.",
                attempted_model=f"list[{item_model.__name__}]",
            )

        # Validate each item
        validated_items: list[T] = []
        for i, item in enumerate(parsed):
            try:
                validated_items.append(item_model(**item))
            except ValidationError as e:
                raise StageValidationError(
                    stage_name=self.stage_name,
                    raw_response=raw_text,
                    validation_errors=f"Item [{i}] failed: {e.json()}",
                    attempted_model=item_model.__name__,
                )

        return validated_items, tokens_used, latency_ms

    # ───────────────────────────────────────────────────────────────
    # PRIVATE JSON EXTRACTION HELPERS
    # ───────────────────────────────────────────────────────────────

    def _extract_json_object(self, text: str) -> dict | None:
        """
        4-strategy JSON object extraction from raw LLM text.

        Strategy 1: Direct json.loads() — fastest, works if Claude behaved
        Strategy 2: Strip markdown code fences and retry
        Strategy 3: Find first { and last } and extract substring
        Strategy 4: Unwrap known wrapper keys Claude sometimes adds
                    (e.g. {"result": {...}, "schema": {...}})
        """
        # Strategy 1: Direct parse
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 2: Strip markdown fences
        clean = re.sub(r"```(?:json)?\s*", "", text)
        clean = re.sub(r"\s*```", "", clean).strip()
        try:
            result = json.loads(clean)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find outermost { ... }
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                result = json.loads(text[brace_start : brace_end + 1])
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # Strategy 4: Unwrap common wrapper keys
        for wrapper_key in ("result", "schema", "output", "data", "response"):
            try:
                candidate = json.loads(text)
                if isinstance(candidate, dict) and wrapper_key in candidate:
                    inner = candidate[wrapper_key]
                    if isinstance(inner, dict):
                        return inner
            except json.JSONDecodeError:
                pass

        return None

    def _extract_json_array(self, text: str) -> list | None:
        """
        4-strategy JSON array extraction from raw LLM text.

        Strategy 1: Direct json.loads()
        Strategy 2: Strip markdown fences
        Strategy 3: Find first [ and last ]
        Strategy 4: Unwrap wrapper dicts that contain arrays
                    (e.g. {"items": [...], "pages": [...]})
        """
        # Strategy 1: Direct parse
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 2: Strip markdown fences
        clean = re.sub(r"```(?:json)?\s*", "", text)
        clean = re.sub(r"\s*```", "", clean).strip()
        try:
            result = json.loads(clean)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find outermost [ ... ]
        bracket_start = text.find("[")
        bracket_end = text.rfind("]")
        if bracket_start != -1 and bracket_end > bracket_start:
            try:
                result = json.loads(text[bracket_start : bracket_end + 1])
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass

        # Strategy 4: Unwrap wrapper dicts containing an array
        for wrapper_key in ("items", "data", "results", "list", "pages",
                             "endpoints", "tables", "roles", "rules"):
            try:
                candidate = json.loads(text)
                if isinstance(candidate, dict) and wrapper_key in candidate:
                    inner = candidate[wrapper_key]
                    if isinstance(inner, list):
                        return inner
            except json.JSONDecodeError:
                pass

        return None
