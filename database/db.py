"""
db.py

SQLAlchemy engine, session factory, table initialisation, and CRUD helpers.

All operations are SYNCHRONOUS. Use SessionLocal() as a context manager
or call session.close() explicitly.

Public API:
  init_db()               → Create all tables if they don't exist
  save_run(result)        → str (run_id)
  get_recent_runs(limit)  → list[dict]
  get_run_schema(run_id)  → dict | None
  get_run_full(run_id)    → dict | None
  get_total_runs()        → int
  get_stats()             → dict (aggregate stats)
"""

import json
from datetime import datetime
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL
from database.run_model import Base, GenerationRun


# ─────────────────────────────────────────────────────────────────────
# ENGINE & SESSION FACTORY
# ─────────────────────────────────────────────────────────────────────

# connect_args: check_same_thread=False is required for SQLite when
# multiple threads might use the same connection (e.g., Streamlit reruns).
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,         # Set True for SQL query debugging
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ─────────────────────────────────────────────────────────────────────
# INITIALISATION
# ─────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """
    Create all database tables if they don't already exist.
    Safe to call multiple times — SQLAlchemy uses CREATE TABLE IF NOT EXISTS.
    Call this at application startup (top of app.py).
    """
    Base.metadata.create_all(bind=engine)
    print("[DB] Database initialised at:", DATABASE_URL)


# ─────────────────────────────────────────────────────────────────────
# WRITE OPERATIONS
# ─────────────────────────────────────────────────────────────────────

def save_run(result) -> str:
    """
    Persist a GenerationResult to the generation_runs table.

    Args:
        result: A GenerationResult Pydantic model instance.

    Returns:
        The run_id string (same as result.run_id).

    Notes:
        - final_schema is serialised via model_dump_json()
        - stages_json is serialised as dict of model_dump() dicts
        - If serialisation of any field fails, stores null for that field
          rather than raising (graceful degradation)
    """
    session = SessionLocal()
    try:
        # Serialise final_schema
        final_schema_str = None
        if result.final_schema is not None:
            try:
                final_schema_str = result.final_schema.model_dump_json()
            except Exception as e:
                print(f"[DB] ⚠️ Could not serialise final_schema: {e}")

        # Serialise stages
        stages_str = None
        try:
            stages_str = json.dumps(
                {k: v.model_dump() for k, v in result.stages.items()}
            )
        except Exception as e:
            print(f"[DB] ⚠️ Could not serialise stages: {e}")

        # Serialise intent (Stage 1 output for quick access)
        intent_str = None
        intent_stage = result.stages.get("intent_extraction")
        if intent_stage and intent_stage.output:
            try:
                intent_str = json.dumps(intent_stage.output)
            except Exception:
                pass

        # Build prompt_preview
        prompt_preview = result.prompt[:100]
        if len(result.prompt) > 100:
            prompt_preview = result.prompt[:97] + "..."

        # Parse created_at string to datetime
        try:
            created_at = datetime.fromisoformat(result.created_at)
        except (ValueError, AttributeError):
            created_at = datetime.utcnow()

        run = GenerationRun(
            id=result.run_id,
            prompt=result.prompt,
            prompt_preview=prompt_preview,
            final_schema=final_schema_str,
            stages_json=stages_str,
            intent_json=intent_str,
            is_valid=result.validation_passed,
            repair_attempts=result.repair_attempts,
            total_latency_ms=result.total_latency_ms,
            total_tokens=result.total_tokens_used,
            estimated_cost_usd=result.estimated_cost_usd,
            created_at=created_at,
        )

        session.add(run)
        session.commit()
        print(f"[DB] Saved run {result.run_id[:8]} to database")
        return result.run_id

    except Exception as e:
        session.rollback()
        print(f"[DB] ❌ Failed to save run: {e}")
        raise
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────
# READ OPERATIONS
# ─────────────────────────────────────────────────────────────────────

def get_recent_runs(limit: int = 20) -> list[dict]:
    """
    Retrieve the most recent generation runs, ordered newest first.

    Args:
        limit: Maximum number of runs to return. Default 20.

    Returns:
        List of dicts with keys:
          id, prompt_preview, is_valid, repair_attempts,
          latency_ms, cost_usd, tokens, created_at
    """
    session = SessionLocal()
    try:
        runs = (
            session.query(GenerationRun)
            .order_by(GenerationRun.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id":              run.id,
                "prompt_preview":  run.prompt_preview or "(no preview)",
                "is_valid":        run.is_valid,
                "repair_attempts": run.repair_attempts,
                "latency_ms":      run.total_latency_ms,
                "cost_usd":        run.estimated_cost_usd,
                "tokens":          run.total_tokens,
                "created_at":      str(run.created_at),
            }
            for run in runs
        ]
    finally:
        session.close()


def get_run_schema(run_id: str) -> dict | None:
    """
    Retrieve the final AppSchema for a specific run.

    Args:
        run_id: UUID string matching GenerationRun.id

    Returns:
        Parsed dict of the AppSchema, or None if run not found
        or final_schema was null (pipeline failed early).
    """
    session = SessionLocal()
    try:
        run = (
            session.query(GenerationRun)
            .filter(GenerationRun.id == run_id)
            .first()
        )
        if run is None:
            return None
        if run.final_schema is None:
            return None
        return json.loads(run.final_schema)
    except json.JSONDecodeError as e:
        print(f"[DB] ⚠️ Could not parse final_schema JSON for run {run_id}: {e}")
        return None
    finally:
        session.close()


def get_run_full(run_id: str) -> dict | None:
    """
    Retrieve full metadata for a specific run including all stages.

    Args:
        run_id: UUID string

    Returns:
        Dict with all GenerationRun columns plus parsed stages_json,
        or None if run not found.
    """
    session = SessionLocal()
    try:
        run = (
            session.query(GenerationRun)
            .filter(GenerationRun.id == run_id)
            .first()
        )
        if run is None:
            return None

        stages = {}
        if run.stages_json:
            try:
                stages = json.loads(run.stages_json)
            except json.JSONDecodeError:
                pass

        intent = {}
        if run.intent_json:
            try:
                intent = json.loads(run.intent_json)
            except json.JSONDecodeError:
                pass

        return {
            "id":               run.id,
            "prompt":           run.prompt,
            "prompt_preview":   run.prompt_preview,
            "is_valid":         run.is_valid,
            "repair_attempts":  run.repair_attempts,
            "total_latency_ms": run.total_latency_ms,
            "total_tokens":     run.total_tokens,
            "estimated_cost_usd": run.estimated_cost_usd,
            "created_at":       str(run.created_at),
            "stages":           stages,
            "intent":           intent,
        }
    finally:
        session.close()


def get_total_runs() -> int:
    """Return total count of generation runs in the database."""
    session = SessionLocal()
    try:
        return session.query(func.count(GenerationRun.id)).scalar() or 0
    finally:
        session.close()


def get_stats() -> dict:
    """
    Return aggregate statistics across all runs.
    Used by the Streamlit sidebar and evaluation display.

    Returns dict with:
      total_runs, successful_runs, success_rate,
      avg_latency_ms, avg_tokens, avg_cost_usd,
      total_cost_usd
    """
    session = SessionLocal()
    try:
        total = session.query(func.count(GenerationRun.id)).scalar() or 0
        if total == 0:
            return {
                "total_runs": 0,
                "successful_runs": 0,
                "success_rate": 0.0,
                "avg_latency_ms": 0.0,
                "avg_tokens": 0.0,
                "avg_cost_usd": 0.0,
                "total_cost_usd": 0.0,
            }

        successful = (
            session.query(func.count(GenerationRun.id))
            .filter(GenerationRun.is_valid == True)
            .scalar() or 0
        )

        avg_latency = (
            session.query(func.avg(GenerationRun.total_latency_ms)).scalar() or 0.0
        )
        avg_tokens = (
            session.query(func.avg(GenerationRun.total_tokens)).scalar() or 0.0
        )
        avg_cost = (
            session.query(func.avg(GenerationRun.estimated_cost_usd)).scalar() or 0.0
        )
        total_cost = (
            session.query(func.sum(GenerationRun.estimated_cost_usd)).scalar() or 0.0
        )

        return {
            "total_runs":     total,
            "successful_runs": successful,
            "success_rate":   round(successful / total, 3),
            "avg_latency_ms": round(avg_latency, 1),
            "avg_tokens":     round(avg_tokens, 0),
            "avg_cost_usd":   round(avg_cost, 5),
            "total_cost_usd": round(total_cost, 4),
        }
    finally:
        session.close()
