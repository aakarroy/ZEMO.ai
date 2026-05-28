"""
stage1_intent.py

Stage 1 of the pipeline: Intent Extraction.

Takes a raw natural language app description and returns a structured
IntentModel by calling Claude with a focused system prompt.

The system prompt includes the full IntentModel field descriptions
so Claude knows exactly what each field expects.
"""

from pipeline import PipelineStage
from models.intent_model import IntentModel


# ─────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# Embeds the IntentModel schema so Claude always knows the contract.
# Keep this prompt focused: intent extraction ONLY, no design decisions.
# ─────────────────────────────────────────────────────────────────────

INTENT_SYSTEM_PROMPT = """
You are an expert product analyst who converts app descriptions into
structured specifications.

Your job is ONLY to extract intent — not to design the system.
Do not generate API routes, database tables, or UI components.
Those come in later stages.

You must identify:
1. What the app is called and what category it belongs to
2. What data entities (nouns) exist in the system
3. What roles different types of users have
4. What the key features are (be specific and complete)
5. What the user did NOT specify (ambiguities)
6. What reasonable assumptions you will make to fill gaps
7. How complex this app is on a scale of 1-10

OUTPUT SCHEMA — return a JSON object with EXACTLY these fields:

{
  "app_name": "Short clean name, 2-4 words, no 'App' or 'System' suffix unless natural",
  "app_type": "Category: CRM | E-Commerce | Project Management | Healthcare | SaaS | Blog | Social Platform | LMS | Restaurant | Recruitment | Fitness | Other",
  "core_entities": ["PascalCase singular nouns that become database tables. Min 3, max 12."],
  "user_roles": ["lowercase_snake_case role names. Always include 'admin'. Min 2 roles."],
  "key_features": [
    "Specific, actionable features. Write as user-facing capabilities.",
    "Minimum 5 features. Be comprehensive based on the app type.",
    "Include implied features even if not explicitly stated.",
    "Example: 'JWT authentication with email/password login and logout'"
  ],
  "ambiguities": ["Things NOT specified that affect system design. Empty list if prompt is clear."],
  "assumptions": ["Concrete decisions made to resolve each ambiguity. Be specific."],
  "complexity_score": 5
}

EDGE CASE HANDLING:
- If prompt is extremely vague (e.g., "build an app"): make reasonable assumptions
  for a generic business app and list all assumptions. Do not ask for clarification.
- If prompt is contradictory (e.g., "no login but separate user data"):
  document the contradiction in ambiguities and make the most logical resolution
  in assumptions.
- If prompt references an existing product (e.g., "build Salesforce"):
  extract the core features of that product type for a mid-complexity version.
- ALWAYS produce valid output. Never return an error or ask a question.
"""


# ─────────────────────────────────────────────────────────────────────
# STAGE CLASS
# ─────────────────────────────────────────────────────────────────────

class IntentExtractor(PipelineStage):
    """
    Stage 1: Intent Extraction.

    Converts a raw user prompt into a structured IntentModel.
    Uses a single Claude call with low temperature for consistency.
    """

    stage_name = "intent_extraction"

    def extract(self, prompt: str) -> tuple[IntentModel, int, float]:
        """
        Extract structured intent from a natural language app description.

        Args:
            prompt: The raw user-entered text describing the app.

        Returns:
            tuple of (IntentModel, tokens_used, latency_ms)

        Raises:
            StageValidationError: If Claude's response cannot be validated.
        """
        print(f"\n[Stage 1] Extracting intent from prompt ({len(prompt)} chars)...")

        user_content = (
            "Extract the structured intent from this app description.\n\n"
            f"APP DESCRIPTION:\n{prompt}\n\n"
            "Remember: Output ONLY the JSON object. No preamble."
        )

        result, tokens, latency = self.call_claude(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_content=user_content,
            response_model=IntentModel,
        )

        print(
            f"[Stage 1] ✅ Complete: app='{result.app_name}', "
            f"entities={result.core_entities}, "
            f"complexity={result.complexity_score}/10"
        )

        return result, tokens, latency
