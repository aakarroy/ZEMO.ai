"""
orchestrator.py

Coordinates the complete 5-stage pipeline end-to-end.

Stage 1: IntentExtractor    → IntentModel
Stage 2: SystemDesigner     → DesignModel
Stage 3: SchemaGenerator    → dict of 5 schema lists
Stage 4: RefinementLayer    → refined dict
Stage 5: ConsistencyChecker + RepairEngine (repair loop)

Returns a GenerationResult containing:
  - All stage outputs and metadata
  - The final AppSchema (or None if pipeline failed early)
  - Total tokens, cost, latency, repair_attempts
"""

import hashlib
import json
import time
import uuid
from datetime import datetime

from pipeline.stage1_intent import IntentExtractor
from pipeline.stage2_design import SystemDesigner
from pipeline.stage3_schema import SchemaGenerator
from pipeline.stage4_refinement import RefinementLayer
from validation.consistency_checker import ConsistencyChecker
from validation.repair_engine import RepairEngine, MaxRepairAttemptsError
from models.app_schema_model import (
    AppSchema, UIPage, APIEndpoint, DBTable, AuthRole, BusinessRule,
    StageResult, GenerationResult,
)
from models.intent_model import IntentModel
from config import MAX_REPAIR_ATTEMPTS


# Gemini 2.5 Flash blended pricing estimate ($/million tokens)
_COST_PER_MILLION_TOKENS = 5.0


def calculate_cost(total_tokens: int) -> float:
    """Estimate API cost in USD using blended Gemini 2.5 Flash pricing."""
    return round((total_tokens / 1_000_000) * _COST_PER_MILLION_TOKENS, 6)


def _safe_list(schemas: dict, key: str, model_class) -> list:
    """
    Extract a list from schemas dict, handling two cases:
      Case A: Value is already list[PydanticModel] (from Stage 3 direct)
      Case B: Value is list[dict] (from Stage 4 Gemini normalisation)

    Args:
        schemas    : The schemas dict from generate_all() or refine()
        key        : One of: ui_schema, api_schema, db_schema,
                     auth_schema, business_rules
        model_class: The Pydantic model class to validate against

    Returns:
        list[model_class instances]
    """
    raw = schemas.get(key, [])
    if not raw:
        return []

    result = []
    for item in raw:
        if isinstance(item, dict):
            # Case B: plain dict from JSON — validate through Pydantic
            try:
                result.append(model_class(**item))
            except Exception as e:
                print(f"  [Orchestrator] ⚠️ Could not validate {key} item: {e}")
        elif hasattr(item, "model_dump"):
            # Case A: already a Pydantic model instance
            result.append(item)
        else:
            print(f"  [Orchestrator] ⚠️ Unexpected item type in {key}: {type(item)}")

    return result


class PipelineOrchestrator:
    """
    Coordinates the complete 5-stage pipeline.

    Usage:
        orchestrator = PipelineOrchestrator()
        result = orchestrator.generate(prompt, progress_callback=callback)
    """

    def generate(
        self,
        prompt: str,
        progress_callback=None,
    ) -> GenerationResult:
        """
        Run the complete pipeline for a given prompt.

        Args:
            prompt           : Raw user-entered text describing the app.
            progress_callback: Optional callable(stage_name, status, detail="").
                               Called at start and end of each stage.
                               Also called by SchemaGenerator for sub-schemas.

        Returns:
            GenerationResult with all stage data and final AppSchema.
            If a stage fails, returns partial result with that stage's error.
        """
        total_start = time.perf_counter()
        run_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()

        result = GenerationResult(
            run_id=run_id,
            prompt=prompt,
            created_at=created_at,
        )

        total_tokens = 0

        def notify(stage: str, status: str, detail: str = ""):
            """Emit a progress update. Safe to call even if no callback."""
            if progress_callback:
                progress_callback(stage, status, detail)

        # ══════════════════════════════════════════════════════════════
        # STAGE 1: INTENT EXTRACTION
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"Pipeline Run: {run_id[:8]}")
        print(f"Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
        print(f"{'='*60}")

        notify("intent_extraction", "running")
        stage1_start = time.perf_counter()
        try:
            intent, s1_tokens, s1_latency = IntentExtractor().extract(prompt)
            total_tokens += s1_tokens
            result.stages["intent_extraction"] = StageResult(
                stage_name="intent_extraction",
                status="complete",
                output=intent.model_dump(),
                latency_ms=s1_latency,
                tokens_used=s1_tokens,
            )
            notify(
                "intent_extraction", "complete",
                f"{s1_latency:.0f}ms | {s1_tokens} tokens | "
                f"'{intent.app_name}' ({intent.complexity_score}/10)"
            )

        except Exception as e:
            result.stages["intent_extraction"] = StageResult(
                stage_name="intent_extraction",
                status="failed",
                latency_ms=(time.perf_counter() - stage1_start) * 1000,
                error=str(e),
            )
            notify("intent_extraction", "failed", str(e)[:100])
            result.total_latency_ms = (time.perf_counter() - total_start) * 1000
            print(f"[Orchestrator] ❌ Stage 1 failed: {e}")
            return result

        # ══════════════════════════════════════════════════════════════
        # STAGE 2: SYSTEM DESIGN
        # ══════════════════════════════════════════════════════════════
        notify("system_design", "running")
        stage2_start = time.perf_counter()
        try:
            design, s2_tokens, s2_latency = SystemDesigner().design(intent)
            total_tokens += s2_tokens
            result.stages["system_design"] = StageResult(
                stage_name="system_design",
                status="complete",
                output=design.model_dump(),
                latency_ms=s2_latency,
                tokens_used=s2_tokens,
            )
            notify(
                "system_design", "complete",
                f"{s2_latency:.0f}ms | {s2_tokens} tokens | "
                f"{len(design.entities)} entities"
            )

        except Exception as e:
            result.stages["system_design"] = StageResult(
                stage_name="system_design",
                status="failed",
                latency_ms=(time.perf_counter() - stage2_start) * 1000,
                error=str(e),
            )
            notify("system_design", "failed", str(e)[:100])
            result.total_latency_ms = (time.perf_counter() - total_start) * 1000
            print(f"[Orchestrator] ❌ Stage 2 failed: {e}")
            return result

        # ══════════════════════════════════════════════════════════════
        # STAGE 3: SCHEMA GENERATION (5 sequential sub-calls)
        # ══════════════════════════════════════════════════════════════
        notify("schema_generation", "running", "Starting 5 schema generators...")

        def schema_sub_progress(schema_key: str):
            """Called by SchemaGenerator after each sub-schema completes."""
            label_map = {
                "ui_schema":      "UI Schema",
                "api_schema":     "API Schema",
                "db_schema":      "DB Schema",
                "auth_schema":    "Auth Schema",
                "business_rules": "Business Rules",
            }
            notify(
                "schema_generation", "running",
                f"✓ {label_map.get(schema_key, schema_key)} generated"
            )

        stage3_start = time.perf_counter()
        try:
            schemas = SchemaGenerator().generate_all(
                intent, design, progress_callback=schema_sub_progress
            )
            s3_tokens = schemas.pop("_tokens", 0)
            s3_latency = schemas.pop("_latency", 0.0)
            total_tokens += s3_tokens

            result.stages["schema_generation"] = StageResult(
                stage_name="schema_generation",
                status="complete",
                output={
                    "ui_pages":      len(schemas.get("ui_schema", [])),
                    "api_endpoints": len(schemas.get("api_schema", [])),
                    "db_tables":     len(schemas.get("db_schema", [])),
                    "auth_roles":    len(schemas.get("auth_schema", [])),
                    "business_rules": len(schemas.get("business_rules", [])),
                },
                latency_ms=s3_latency,
                tokens_used=s3_tokens,
            )
            notify(
                "schema_generation", "complete",
                f"{s3_latency:.0f}ms | {s3_tokens} tokens | "
                f"Pages={len(schemas.get('ui_schema', []))}, "
                f"Endpoints={len(schemas.get('api_schema', []))}, "
                f"Tables={len(schemas.get('db_schema', []))}"
            )

        except Exception as e:
            result.stages["schema_generation"] = StageResult(
                stage_name="schema_generation",
                status="failed",
                latency_ms=(time.perf_counter() - stage3_start) * 1000,
                error=str(e),
            )
            notify("schema_generation", "failed", str(e)[:100])
            result.total_latency_ms = (time.perf_counter() - total_start) * 1000
            print(f"[Orchestrator] ❌ Stage 3 failed: {e}")
            return result

        # ══════════════════════════════════════════════════════════════
        # STAGE 4: REFINEMENT
        # ══════════════════════════════════════════════════════════════
        notify("refinement", "running")
        stage4_start = time.perf_counter()
        try:
            refined_schemas, s4_tokens, s4_latency = RefinementLayer().refine(
                schemas, intent
            )
            total_tokens += s4_tokens
            refinement_applied = s4_tokens > 0

            result.stages["refinement"] = StageResult(
                stage_name="refinement",
                status="complete",
                output={
                    "inconsistencies_fixed": refinement_applied,
                    "tokens_used": s4_tokens,
                },
                latency_ms=s4_latency,
                tokens_used=s4_tokens,
            )
            notify(
                "refinement", "complete",
                "Fixed naming inconsistencies" if refinement_applied
                else "No inconsistencies detected"
            )

        except Exception as e:
            # Refinement failure is non-fatal — use original schemas
            print(f"[Orchestrator] ⚠️ Stage 4 failed (non-fatal): {e}")
            result.stages["refinement"] = StageResult(
                stage_name="refinement",
                status="warning",
                latency_ms=(time.perf_counter() - stage4_start) * 1000,
                error=f"Non-fatal: {str(e)}",
            )
            notify("refinement", "warning", f"Skipped: {str(e)[:80]}")
            refined_schemas = schemas  # Use unrefined schemas

        # ══════════════════════════════════════════════════════════════
        # ASSEMBLE AppSchema
        # ══════════════════════════════════════════════════════════════
        app_schema = AppSchema(
            meta={
                "app_name":        intent.app_name,
                "app_type":        intent.app_type,
                "complexity_score": intent.complexity_score,
                "generated_at":    created_at,
                "prompt_hash":     hashlib.md5(prompt.encode()).hexdigest(),
                "run_id":          run_id,
            },
            ui_schema=_safe_list(refined_schemas, "ui_schema", UIPage),
            api_schema=_safe_list(refined_schemas, "api_schema", APIEndpoint),
            db_schema=_safe_list(refined_schemas, "db_schema", DBTable),
            auth_schema=_safe_list(refined_schemas, "auth_schema", AuthRole),
            business_rules=_safe_list(refined_schemas, "business_rules", BusinessRule),
            assumptions=intent.assumptions,
            warnings=[],
        )

        # ══════════════════════════════════════════════════════════════
        # STAGE 5: VALIDATION + REPAIR LOOP
        # ══════════════════════════════════════════════════════════════
        notify("validation", "running", "Running consistency checks...")

        checker = ConsistencyChecker()
        validation_report = checker.run_all_checks(app_schema)
        repair_attempts = 0

        while not validation_report.is_valid and repair_attempts < MAX_REPAIR_ATTEMPTS:
            repair_attempts += 1
            notify(
                "validation", "running",
                f"Repair attempt {repair_attempts}/{MAX_REPAIR_ATTEMPTS}: "
                f"{len(validation_report.errors)} error(s)"
            )
            try:
                engine = RepairEngine()
                app_schema, repair_tokens = engine.repair(
                    app_schema,
                    validation_report,
                    intent,
                    attempt=repair_attempts,
                )
                total_tokens += repair_tokens
                # Re-validate after repair
                validation_report = checker.run_all_checks(app_schema)

            except MaxRepairAttemptsError as e:
                warning_msg = (
                    f"Max repair attempts ({e.attempts}) reached. "
                    f"{len(e.remaining_errors)} issue(s) remain: "
                    f"{[err.description[:60] for err in e.remaining_errors[:2]]}"
                )
                app_schema.warnings.append(warning_msg)
                print(f"[Orchestrator] ⚠️ {warning_msg}")
                break

        # Final validation status
        final_valid = validation_report.is_valid
        validation_detail = (
            f"{validation_report.passed_checks} checks passed, "
            f"{validation_report.failed_checks} failed, "
            f"{len(validation_report.errors)} error(s), "
            f"{repair_attempts} repair(s)"
        )

        result.stages["validation"] = StageResult(
            stage_name="validation",
            status="complete" if final_valid else "warning",
            output={
                "passed_checks":   validation_report.passed_checks,
                "failed_checks":   validation_report.failed_checks,
                "error_count":     len(validation_report.errors),
                "warning_count":   len(validation_report.warnings),
                "errors":          [e.description for e in validation_report.errors],
                "warnings":        [w.description for w in validation_report.warnings],
                "repair_attempts": repair_attempts,
            },
            latency_ms=0.0,
            tokens_used=0,
        )
        notify(
            "validation",
            "complete" if final_valid else "warning",
            validation_detail
        )

        # ══════════════════════════════════════════════════════════════
        # FINALISE RESULT
        # ══════════════════════════════════════════════════════════════
        result.final_schema = app_schema
        result.total_latency_ms = (time.perf_counter() - total_start) * 1000
        result.repair_attempts = repair_attempts
        result.validation_passed = final_valid
        result.total_tokens_used = total_tokens
        result.estimated_cost_usd = calculate_cost(total_tokens)

        print(
            f"\n[Orchestrator] ✅ Pipeline complete: "
            f"valid={final_valid}, "
            f"repairs={repair_attempts}, "
            f"tokens={total_tokens}, "
            f"cost=${result.estimated_cost_usd:.4f}, "
            f"latency={result.total_latency_ms:.0f}ms"
        )

        return result
