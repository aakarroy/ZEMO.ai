"""
run_model.py

SQLAlchemy ORM model for the generation_runs table.
One row per pipeline run. Stores metadata and the final schema as JSON text.
"""

from sqlalchemy import (
    Column, String, Text, Float, Integer, Boolean, DateTime
)
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime


class Base(DeclarativeBase):
    """Base class for all ORM models in this project."""
    pass


class GenerationRun(Base):
    """
    ORM mapping for the generation_runs table.

    Columns:
        id              : UUID string from GenerationResult.run_id
        prompt          : Full original prompt text
        prompt_preview  : First 100 chars (for sidebar display)
        final_schema    : JSON string of AppSchema.model_dump_json()
                          Null if pipeline failed before producing a schema.
        stages_json     : JSON string of all StageResult dicts
        intent_json     : JSON string of Stage 1 output (for quick display)
        is_valid        : Whether ConsistencyChecker passed with 0 errors
        repair_attempts : How many repair cycles were needed
        total_latency_ms: Wall-clock ms for the full pipeline
        total_tokens    : Sum of all Claude API tokens used
        estimated_cost_usd: Estimated USD cost
        created_at      : UTC datetime when the run was initiated
    """
    __tablename__ = "generation_runs"

    id = Column(String(36), primary_key=True, nullable=False)
    prompt = Column(Text, nullable=False)
    prompt_preview = Column(String(103), nullable=True)
    final_schema = Column(Text, nullable=True)
    stages_json = Column(Text, nullable=True)
    intent_json = Column(Text, nullable=True)
    is_valid = Column(Boolean, default=False, nullable=False)
    repair_attempts = Column(Integer, default=0, nullable=False)
    total_latency_ms = Column(Float, default=0.0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    estimated_cost_usd = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return (
            f"<GenerationRun id={self.id[:8]} "
            f"valid={self.is_valid} "
            f"tokens={self.total_tokens}>"
        )
