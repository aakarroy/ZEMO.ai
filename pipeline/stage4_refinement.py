"""
stage4_refinement.py

Stage 4 of the pipeline: Refinement.

Detects naming inconsistencies across the 4 separately-generated schemas
(UI, API, DB, Auth) and normalises them.

TWO-STEP APPROACH:
  Step 1: Python-only analysis — fast, zero cost, zero tokens.
          Looks for common naming patterns that diverge across layers.
  Step 2: Claude normalisation — ONLY called if Step 1 found issues.
          Sends only the inconsistencies + schemas to Claude, not the
          full pipeline context. Targeted and efficient.

This is NOT a full regeneration. It is a surgical consistency fix.
"""

import json
import re
import time
import anthropic
from pipeline import PipelineStage, StageValidationError
from models.intent_model import IntentModel
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_TEMPERATURE_REPAIR


# Known naming variant groups — fields that often get named differently
# across separately-generated schemas.
NAMING_VARIANT_GROUPS = [
    {"phone_number", "phone", "contact_phone", "phone_num", "tel"},
    {"user_id", "userId", "userid", "owner_id", "author_id"},
    {"created_at", "createdAt", "created_date", "creation_date", "date_created"},
    {"updated_at", "updatedAt", "modified_at", "last_updated", "update_time"},
    {"first_name", "firstName", "fname", "given_name"},
    {"last_name", "lastName", "lname", "family_name", "surname"},
    {"is_active", "isActive", "active", "enabled", "status"},
    {"password_hash", "hashed_password", "password_encrypted", "pwd_hash"},
    {"stripe_customer_id", "stripeCustomerId", "stripe_id", "payment_customer_id"},
    {"profile_picture", "avatar", "profile_image", "photo_url", "picture_url"},
]

REFINEMENT_SYSTEM_PROMPT = """
You are a schema consistency expert performing a targeted normalisation pass.

You will receive:
1. A list of naming inconsistencies found across 4 schemas
2. The 4 schemas (ui_schema, api_schema, db_schema, auth_schema, business_rules)

Your job:
- Fix ONLY the listed naming inconsistencies
- Use the DB schema column names as the authoritative source of truth
  (the database defines the canonical field names)
- Update UI component props, API request_body fields, and any other
  references to use the canonical DB column name
- Keep ALL other data completely unchanged
- Do not add or remove any pages, endpoints, tables, roles, or rules

OUTPUT: A JSON object with EXACTLY these top-level keys:
{
  "ui_schema": [...],
  "api_schema": [...],
  "db_schema": [...],
  "auth_schema": [...],
  "business_rules": [...]
}

Return all 5 schemas even if only one had issues.
Preserve all existing data exactly, changing only the field names listed.
"""


class RefinementLayer(PipelineStage):
    """
    Stage 4: Cross-layer schema refinement.

    Detects naming inconsistencies via Python analysis,
    then calls Claude ONLY if issues are found.
    """

    stage_name = "refinement"

    def _collect_api_fields(self, schemas: dict) -> set[str]:
        """Extract all field names from API request bodies."""
        fields = set()
        for endpoint in schemas.get("api_schema", []):
            if hasattr(endpoint, "request_body") and endpoint.request_body:
                fields.update(endpoint.request_body.keys())
        return fields

    def _collect_db_columns(self, schemas: dict) -> set[str]:
        """Extract all column names from DB tables."""
        columns = set()
        for table in schemas.get("db_schema", []):
            for col in table.columns:
                columns.add(col.name)
        return columns

    def _collect_ui_prop_keys(self, schemas: dict) -> set[str]:
        """Extract field names referenced in UI component props."""
        fields = set()
        for page in schemas.get("ui_schema", []):
            for comp in page.components:
                if comp.props:
                    # Look for 'columns', 'fields' keys in props
                    for prop_key in ("columns", "fields"):
                        if prop_key in comp.props:
                            val = comp.props[prop_key]
                            if isinstance(val, list):
                                fields.update(str(v) for v in val)
        return fields

    def find_inconsistencies(self, schemas: dict) -> list[str]:
        """
        Pure Python consistency check.
        Returns a list of human-readable inconsistency descriptions.
        Zero Claude calls. Zero cost.
        """
        inconsistencies = []

        api_fields = self._collect_api_fields(schemas)
        db_columns = self._collect_db_columns(schemas)
        ui_fields = self._collect_ui_prop_keys(schemas)

        all_field_names = api_fields | db_columns | ui_fields

        for variant_group in NAMING_VARIANT_GROUPS:
            # Find which variants from this group appear in our schemas
            found_variants = variant_group & all_field_names
            if len(found_variants) > 1:
                # Multiple variants of the same concept found
                in_api = variant_group & api_fields
                in_db = variant_group & db_columns
                in_ui = variant_group & ui_fields

                # Only report if different names are used across layers
                cross_layer_variants = set()
                if in_api:
                    cross_layer_variants.update(in_api)
                if in_db:
                    cross_layer_variants.update(in_db)
                if in_ui:
                    cross_layer_variants.update(in_ui)

                if len(cross_layer_variants) > 1:
                    inconsistencies.append(
                        f"Naming variant detected: {cross_layer_variants}. "
                        f"API uses {in_api or 'none'}, "
                        f"DB uses {in_db or 'none'}, "
                        f"UI uses {in_ui or 'none'}. "
                        f"Normalise to the DB column name (authoritative)."
                    )

        return inconsistencies

    def _schemas_to_json(self, schemas: dict) -> dict:
        """
        Convert schemas dict (containing Pydantic model lists) to
        plain dicts for JSON serialisation.
        """
        serialisable = {}
        for key, value in schemas.items():
            if key.startswith("_"):
                continue
            if isinstance(value, list):
                serialisable[key] = [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in value
                ]
            else:
                serialisable[key] = value
        return serialisable

    def refine(
        self, schemas: dict, intent: IntentModel
    ) -> tuple[dict, int, float]:
        """
        Main refinement entry point.

        Step 1: Python analysis (free, fast)
        Step 2: Claude normalisation (only if issues found)

        Args:
            schemas: Dict from Stage 3's generate_all() result.
                     Keys: ui_schema, api_schema, db_schema, auth_schema,
                            business_rules, _tokens, _latency
            intent:  IntentModel (for context in logs)

        Returns:
            tuple of (refined_schemas_dict, tokens_used, latency_ms)
            refined_schemas_dict has same keys as input (minus _tokens/_latency)

        Note:
            If no inconsistencies are found, returns original schemas unchanged
            with tokens=0, latency=0.0 (no Claude call made).
        """
        print(f"\n[Stage 4] Running consistency analysis for '{intent.app_name}'...")

        inconsistencies = self.find_inconsistencies(schemas)

        if not inconsistencies:
            print("[Stage 4] ✅ No inconsistencies found. Schemas are consistent.")
            return schemas, 0, 0.0

        print(
            f"[Stage 4] Found {len(inconsistencies)} inconsistency(ies). "
            "Calling Claude for normalisation..."
        )
        for issue in inconsistencies:
            print(f"  → {issue}")

        # Build schemas as plain JSON for Claude
        schemas_json = self._schemas_to_json(schemas)
        schemas_str = json.dumps(schemas_json, indent=2)

        user_content = (
            f"Fix these {len(inconsistencies)} naming inconsistencies:\n\n"
            + "\n".join(f"{i+1}. {issue}" for i, issue in enumerate(inconsistencies))
            + f"\n\nSCHEMAS TO FIX:\n{schemas_str}\n\n"
            "Return the corrected JSON object with all 5 schema keys."
        )

        # Use CLAUDE_TEMPERATURE_REPAIR (0.1) for deterministic fixes
        t_start = time.perf_counter()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            temperature=CLAUDE_TEMPERATURE_REPAIR,
            system=REFINEMENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        latency_ms = (time.perf_counter() - t_start) * 1000
        tokens_used = message.usage.input_tokens + message.usage.output_tokens
        raw_text = message.content[0].text.strip()

        parsed = self._extract_json_object(raw_text)
        if parsed is None:
            print(
                "[Stage 4] ⚠️ Claude refinement returned non-JSON. "
                "Using original schemas unchanged."
            )
            return schemas, tokens_used, latency_ms

        # Validate that all 5 expected keys are present in response
        expected_keys = {"ui_schema", "api_schema", "db_schema", "auth_schema", "business_rules"}
        if not expected_keys.issubset(parsed.keys()):
            missing = expected_keys - parsed.keys()
            print(
                f"[Stage 4] ⚠️ Refined schema missing keys: {missing}. "
                "Using original schemas unchanged."
            )
            return schemas, tokens_used, latency_ms

        print(
            f"[Stage 4] ✅ Refinement complete: "
            f"{tokens_used} tokens, {latency_ms:.0f}ms"
        )

        # Merge refined data back, preserving _tokens/_latency from Stage 3
        refined = dict(schemas)  # copy
        refined.update(parsed)   # overwrite with normalised versions

        return refined, tokens_used, latency_ms
