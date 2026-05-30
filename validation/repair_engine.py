"""
repair_engine.py

Surgical per-layer schema repair engine.

FUNDAMENTAL DESIGN PRINCIPLE:
  Only the broken schema layer is regenerated — never the full pipeline.

  If UI/API consistency check fails → regenerate ONLY ui_schema
  If API/DB consistency check fails → regenerate ONLY api_schema
  If Auth consistency fails (undefined role) → add the role to auth_schema
    (this is often a pure-Python fix, no Gemini needed)

  This is significantly cheaper and faster than full retry.
  It also preserves the good work done by other layers.

REPAIR CYCLE LIMIT:
  MAX_REPAIR_ATTEMPTS from config (default: 3).
  Each attempt reruns ALL validation checks after repair.
  If still failing after MAX_REPAIR_ATTEMPTS: surface warnings in schema,
  do not raise an exception (the output is still usable, just imperfect).
"""

import json
import re
import time
import random
from google import genai
from google.genai import types
from pydantic import ValidationError

from models.app_schema_model import (
    AppSchema, UIPage, APIEndpoint, DBTable, AuthRole, BusinessRule
)
from models.intent_model import IntentModel
from validation.consistency_checker import ValidationReport, ConsistencyError
from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL, GEMINI_TEMPERATURE_REPAIR, GEMINI_MAX_OUTPUT_TOKENS,
    MAX_REPAIR_ATTEMPTS
)


class MaxRepairAttemptsError(Exception):
    """
    Raised when the repair engine has exhausted all attempts.
    Callers should catch this and add a warning to the schema
    rather than crashing the pipeline.
    """
    def __init__(self, attempts: int, remaining_errors: list[ConsistencyError]):
        self.attempts = attempts
        self.remaining_errors = remaining_errors
        descriptions = [e.description[:80] for e in remaining_errors[:3]]
        super().__init__(
            f"Repair failed after {attempts} attempt(s). "
            f"Remaining errors: {descriptions}"
        )


class RepairEngine:
    """
    Surgical repair engine for AppSchema consistency errors.

    Call repair() with the AppSchema and ValidationReport.
    It identifies which layers have errors, regenerates only those
    layers, and returns an updated AppSchema.

    Does NOT call the full pipeline orchestrator.
    Does NOT regenerate stages 1-4.
    """

    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    # ─────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────

    def repair(
        self,
        app_schema: AppSchema,
        validation_report: ValidationReport,
        original_intent: IntentModel,
        attempt: int = 1,
    ) -> tuple[AppSchema, int]:
        """
        Repair a schema that failed consistency validation.

        Args:
            app_schema         : The AppSchema with consistency errors.
            validation_report  : The ValidationReport from ConsistencyChecker.
            original_intent    : IntentModel (provides context for Gemini).
            attempt            : Current repair attempt number (1-based).

        Returns:
            tuple of (repaired_AppSchema, total_tokens_used)

        Raises:
            MaxRepairAttemptsError: If attempt > MAX_REPAIR_ATTEMPTS.
        """
        if attempt > MAX_REPAIR_ATTEMPTS:
            raise MaxRepairAttemptsError(attempt - 1, validation_report.errors)

        print(
            f"\n[Repair Engine] Attempt {attempt}/{MAX_REPAIR_ATTEMPTS}: "
            f"{len(validation_report.errors)} error(s) to fix."
        )

        total_tokens = 0

        # Group errors by their layer string
        errors_by_layer: dict[str, list[ConsistencyError]] = {}
        for error in validation_report.errors:
            layer = error.layer
            if layer not in errors_by_layer:
                errors_by_layer[layer] = []
            errors_by_layer[layer].append(error)

        print(f"  Layers with errors: {list(errors_by_layer.keys())}")

        # Handle each layer independently
        for layer, layer_errors in errors_by_layer.items():
            app_schema, tokens = self._repair_layer(
                app_schema, layer, layer_errors, original_intent
            )
            total_tokens += tokens

        return app_schema, total_tokens

    # ─────────────────────────────────────────────────────────────────
    # LAYER DISPATCH
    # ─────────────────────────────────────────────────────────────────

    def _repair_layer(
        self,
        app_schema: AppSchema,
        layer: str,
        errors: list[ConsistencyError],
        intent: IntentModel,
    ) -> tuple[AppSchema, int]:
        """Route to the correct repair method based on layer string."""

        if "API/DB" in layer:
            return self._repair_api_schema(app_schema, errors, intent)
        elif "UI/API" in layer:
            return self._repair_ui_schema(app_schema, errors, intent)
        elif "UI/Auth" in layer or "API/Auth" in layer:
            # Try pure-Python auth repair first (cheaper)
            return self._repair_auth_schema(app_schema, errors, intent)
        else:
            print(f"  [Repair] ⚠️ Unknown layer '{layer}', skipping.")
            return app_schema, 0

    # ─────────────────────────────────────────────────────────────────
    # LAYER-SPECIFIC REPAIR METHODS
    # ─────────────────────────────────────────────────────────────────

    def _repair_api_schema(
        self,
        app_schema: AppSchema,
        errors: list[ConsistencyError],
        intent: IntentModel,
    ) -> tuple[AppSchema, int]:
        """
        Repair API/DB consistency errors.

        Common cause: POST endpoint has a field not in DB.
        Fix: Update request_body to use the correct DB column names.
        """
        print(f"  [Repair] Fixing API schema ({len(errors)} error(s))...")

        error_text = self._format_errors(errors)
        current_api_json = json.dumps(
            [ep.model_dump() for ep in app_schema.api_schema], indent=2
        )
        current_db_summary = self._summarise_db(app_schema)

        system_prompt = (
            "You are an API schema repair engineer. "
            "Fix ONLY the listed errors in the API schema. "
            "Keep all existing endpoints. Only change the specific "
            "request_body fields that are causing errors. "
            "Use the DB column names as the authoritative source. "
            "Return ONLY a valid JSON array of APIEndpoint objects. "
            "NO explanation. NO markdown. Start with [ end with ]."
        )

        user_content = (
            f"Fix these errors in the API schema:\n{error_text}\n\n"
            f"DB Column Names Available (use these exact names):\n{current_db_summary}\n\n"
            f"Current API Schema to fix:\n{current_api_json}\n\n"
            f"App context: {intent.app_name} ({intent.app_type})"
        )

        raw, tokens, _ = self._call_gemini_raw(system_prompt, user_content)
        parsed = self._extract_list(raw)

        if parsed is not None:
            try:
                app_schema.api_schema = [APIEndpoint(**ep) for ep in parsed]
                print(f"  [Repair] ✅ API schema repaired ({tokens} tokens)")
            except (ValidationError, TypeError) as e:
                print(f"  [Repair] ⚠️ API repair validation failed: {e}. Keeping original.")
        else:
            print("  [Repair] ⚠️ API repair returned non-JSON. Keeping original.")

        return app_schema, tokens

    def _repair_ui_schema(
        self,
        app_schema: AppSchema,
        errors: list[ConsistencyError],
        intent: IntentModel,
    ) -> tuple[AppSchema, int]:
        """
        Repair UI/API consistency errors.

        Common cause: UIComponent.data_source references non-existent API path.
        Fix: Update data_source to reference an existing API endpoint.
        """
        print(f"  [Repair] Fixing UI schema ({len(errors)} error(s))...")

        error_text = self._format_errors(errors)
        current_ui_json = json.dumps(
            [p.model_dump() for p in app_schema.ui_schema], indent=2
        )
        available_paths = [ep.path for ep in app_schema.api_schema]

        system_prompt = (
            "You are a UI schema repair engineer. "
            "Fix ONLY the listed data_source errors in the UI schema. "
            "Update component data_source fields to reference existing API paths. "
            "Do not change any other component properties. "
            "Do not add or remove any pages or components. "
            "Return ONLY a valid JSON array of UIPage objects. "
            "NO explanation. NO markdown. Start with [ end with ]."
        )

        user_content = (
            f"Fix these UI schema errors:\n{error_text}\n\n"
            f"Available API paths (use ONLY these):\n"
            f"{json.dumps(available_paths, indent=2)}\n\n"
            f"Current UI schema to fix:\n{current_ui_json}"
        )

        raw, tokens, _ = self._call_gemini_raw(system_prompt, user_content)
        parsed = self._extract_list(raw)

        if parsed is not None:
            try:
                app_schema.ui_schema = [UIPage(**p) for p in parsed]
                print(f"  [Repair] ✅ UI schema repaired ({tokens} tokens)")
            except (ValidationError, TypeError) as e:
                print(f"  [Repair] ⚠️ UI repair validation failed: {e}. Keeping original.")
        else:
            print("  [Repair] ⚠️ UI repair returned non-JSON. Keeping original.")

        return app_schema, tokens

    def _repair_auth_schema(
        self,
        app_schema: AppSchema,
        errors: list[ConsistencyError],
        intent: IntentModel,
    ) -> tuple[AppSchema, int]:
        """
        Repair Auth consistency errors.

        Strategy A (pure Python, no Claude):
          If error is "undefined role" — extract the missing role name
          from the error's affected_fields and add a default role.
          This handles the common case where a role name typo or
          casing difference causes the error.

        Strategy B (Claude call):
          If Strategy A doesn't resolve all errors — call Claude to
          regenerate only the auth_schema.
        """
        print(f"  [Repair] Fixing Auth schema ({len(errors)} error(s))...")

        # Strategy A: Pure Python — add missing roles
        python_fixed = 0
        for error in errors:
            if error.check_type in ("ui_uses_undefined_role", "api_uses_undefined_role"):
                for affected in error.affected_fields:
                    # The first affected field is the role name
                    if not any(r.name == affected for r in app_schema.auth_schema):
                        # Add a default role with read-only permissions
                        new_role = AuthRole(
                            name=affected,
                            permissions=[f"{entity.lower()}s:read"
                                         for entity in intent.core_entities[:3]],
                            inherits_from=None
                        )
                        app_schema.auth_schema.append(new_role)
                        print(f"  [Repair] Added missing role '{affected}' (Python fix)")
                        python_fixed += 1
                        break  # Only take first affected_field as role name

        if python_fixed == len(errors):
            print(f"  [Repair] ✅ Auth repaired via Python (0 tokens)")
            return app_schema, 0

        # Strategy B: Gemini call for remaining errors
        error_text = self._format_errors(errors)
        current_auth_json = json.dumps(
            [r.model_dump() for r in app_schema.auth_schema], indent=2
        )

        system_prompt = (
            "You are an auth schema repair engineer. "
            "Fix the listed role definition errors. "
            "Add any missing roles. Keep all existing roles unchanged. "
            "Return ONLY a valid JSON array of AuthRole objects. "
            "Each role must have: name, permissions (list of strings), "
            "inherits_from (string or null). "
            "NO explanation. NO markdown. Start with [ end with ]."
        )

        user_content = (
            f"Fix these auth errors:\n{error_text}\n\n"
            f"App roles needed: {intent.user_roles}\n\n"
            f"Current auth schema:\n{current_auth_json}"
        )

        raw, tokens, _ = self._call_gemini_raw(system_prompt, user_content)
        parsed = self._extract_list(raw)

        if parsed is not None:
            try:
                app_schema.auth_schema = [AuthRole(**r) for r in parsed]
                print(f"  [Repair] ✅ Auth schema repaired via Gemini ({tokens} tokens)")
            except (ValidationError, TypeError) as e:
                print(f"  [Repair] ⚠️ Auth repair validation failed: {e}. Keeping original.")
        else:
            print("  [Repair] ⚠️ Auth repair returned non-JSON. Keeping original.")

        return app_schema, tokens

    # ─────────────────────────────────────────────────────────────────
    # PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────────────

    def _call_gemini_raw(
        self, system_prompt: str, user_content: str
    ) -> tuple[str, int, float]:
        """
        Make a Gemini API call and return raw text + usage stats.
        Uses GEMINI_TEMPERATURE_REPAIR (0.1) for deterministic fixes.
        """
        t_start = time.perf_counter()

        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                response = self.client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=user_content,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=GEMINI_TEMPERATURE_REPAIR,
                        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                    )
                )
                break
            except Exception as e:
                err_str = str(e).upper()
                if ("503" in err_str or "UNAVAILABLE" in err_str) and attempt < max_attempts - 1:
                    sleep_time = (2 ** attempt) + random.uniform(0, 1)
                    print(f"  [Repair] [Warning] Gemini is busy. Retrying in {sleep_time:.2f} seconds...")
                    time.sleep(sleep_time)
                else:
                    raise

        latency_ms = (time.perf_counter() - t_start) * 1000
        tokens = response.usage_metadata.prompt_token_count + response.usage_metadata.candidates_token_count
        raw = response.text.strip()
        return raw, tokens, latency_ms

    def _extract_list(self, text: str) -> list | None:
        """Extract a JSON array from raw text using 3 strategies."""
        # Strategy 1: Direct
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 2: Strip markdown
        clean = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
        try:
            result = json.loads(clean)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find brackets
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                result = json.loads(text[start:end + 1])
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass

        return None

    def _format_errors(self, errors: list[ConsistencyError]) -> str:
        """Format a list of ConsistencyErrors as numbered text."""
        lines = []
        for i, error in enumerate(errors, 1):
            lines.append(
                f"{i}. [{error.check_type}] {error.description} "
                f"| Fix: {error.suggested_fix}"
            )
        return "\n".join(lines)

    def _summarise_db(self, app_schema: AppSchema) -> str:
        """Summarise DB schema as a compact table→columns mapping."""
        summary = {}
        for table in app_schema.db_schema:
            summary[table.name] = [col.name for col in table.columns]
        return json.dumps(summary, indent=2)
