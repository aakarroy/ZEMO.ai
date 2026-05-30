"""
stage2_design.py

Stage 2 of the pipeline: System Design.

Takes the structured IntentModel from Stage 1 and returns a DesignModel
that defines the full architecture: entities, relationships, auth strategy,
data flows, and third-party services.

This stage makes architectural decisions — it does NOT generate API routes
or UI components. That is Stage 3's job.
"""

from pipeline import PipelineStage
from models.intent_model import IntentModel
from models.design_model import DesignModel


DESIGN_SYSTEM_PROMPT = """
You are a Senior Software Architect designing the system architecture for a web application.

Your input is a structured intent specification. Your output is an architecture design.

DO NOT generate:
- API routes or endpoints (Stage 3 handles this)
- UI pages or components (Stage 3 handles this)
- Specific SQL CREATE TABLE statements

DO generate:
- Every data entity with its fields and relationships
- Authentication strategy (be specific about token types and expiry)
- Role hierarchy
- Data flows (how entities interact)
- Third-party services needed

ENTITY FIELD RULES:
- Every entity MUST start with: {"name": "id", "type": "UUID", "nullable": false}
- Every entity MUST end with:
    {"name": "created_at", "type": "TIMESTAMP", "nullable": false}
    {"name": "updated_at", "type": "TIMESTAMP", "nullable": false}
- Foreign keys: add field like {"name": "user_id", "type": "UUID", "nullable": false}
  for every belongs_to relationship
- Allowed types ONLY: UUID, VARCHAR(255), VARCHAR(512), TEXT, INTEGER,
  BIGINT, BOOLEAN, DECIMAL(10,2), FLOAT, TIMESTAMP, DATE, JSON

RELATION FORMAT: "has_many: EntityName", "belongs_to: EntityName",
"has_one: EntityName", "many_to_many: EntityName"

OUTPUT SCHEMA:
{
  "entities": [
    {
      "entity_name": "PascalCase (must include ALL from core_entities)",
      "fields": [
        {"name": "id", "type": "UUID", "nullable": false},
        {"name": "example_field", "type": "VARCHAR(255)", "nullable": false},
        {"name": "created_at", "type": "TIMESTAMP", "nullable": false},
        {"name": "updated_at", "type": "TIMESTAMP", "nullable": false}
      ],
      "relations": ["has_many: Contact", "belongs_to: Organization"]
    }
  ],
  "auth_strategy": "Describe the full auth mechanism with token types and expiry",
  "role_hierarchy": ["admin > manager > user (most to least privileged)"],
  "data_flows": ["Actor verb Object — minimum 6 flows describing key interactions"],
  "third_party_services": ["Service for purpose — empty array if none needed"]
}

CRITICAL: Include every entity from the provided core_entities list.
You may ADD junction tables (e.g., UserRole, PostTag) that were implied
but not listed. Never remove entities from the core list.
"""


class SystemDesigner(PipelineStage):
    """
    Stage 2: System Design.

    Takes IntentModel, returns DesignModel with full architecture.
    """

    stage_name = "system_design"

    def design(self, intent: IntentModel) -> tuple[DesignModel, int, float]:
        """
        Design the system architecture from a structured intent.

        Args:
            intent: Validated IntentModel from Stage 1.

        Returns:
            tuple of (DesignModel, tokens_used, latency_ms)

        Raises:
            StageValidationError: If Gemini's response cannot be validated.
        """
        print(
            f"\n[Stage 2] Designing architecture for '{intent.app_name}' "
            f"({len(intent.core_entities)} entities)..."
        )

        # Serialize IntentModel to pretty JSON for Gemini's context
        user_content = (
            "Design the system architecture for this app specification.\n\n"
            f"APP SPECIFICATION:\n{intent.model_dump_json(indent=2)}\n\n"
            "Remember: Define all entities with their fields. "
            "Output ONLY the JSON object."
        )

        result, tokens, latency = self.call_gemini(
            system_prompt=DESIGN_SYSTEM_PROMPT,
            user_content=user_content,
            response_model=DesignModel,
        )

        print(
            f"[Stage 2] ✅ Complete: {len(result.entities)} entities defined, "
            f"auth='{result.auth_strategy[:40]}...'"
        )

        return result, tokens, latency
