"""intent_model — Pydantic model for Stage 1 (Intent Extraction) output."""
from pydantic import BaseModel, Field


class IntentModel(BaseModel):
    """
    Output contract for Stage 1: Intent Extraction.

    Claude must return a JSON object matching this schema exactly.
    This model is the input to Stage 2 (SystemDesigner).
    """

    app_name: str = Field(
        ...,
        description=(
            "A short, clean name for the app. 2-4 words maximum. "
            "Examples: 'CRM Platform', 'Task Manager', 'E-Commerce Store'. "
            "Do NOT include words like 'App' or 'System' unless natural."
        )
    )

    app_type: str = Field(
        ...,
        description=(
            "The broad category this app falls into. "
            "Examples: 'CRM', 'Project Management', 'E-Commerce', "
            "'Healthcare', 'SaaS Invoicing', 'Social Platform', "
            "'Learning Management System', 'Restaurant Management'."
        )
    )

    core_entities: list[str] = Field(
        ...,
        description=(
            "The primary data objects (nouns) in the system. "
            "These become database tables. Use PascalCase singular. "
            "Examples: ['User', 'Contact', 'Deal', 'Invoice', 'Product']. "
            "Include ALL entities implied by the features, even if not "
            "explicitly named in the prompt."
        )
    )

    user_roles: list[str] = Field(
        ...,
        description=(
            "All distinct user role types in the system. "
            "Use lowercase_with_underscores. "
            "Examples: ['admin', 'manager', 'basic_user', 'guest', "
            "'premium_user', 'support_agent']. "
            "Always include at least 'admin' and one non-admin role."
        )
    )

    key_features: list[str] = Field(
        ...,
        description=(
            "Concrete, actionable features the app must have. "
            "Write as user-facing capabilities, not technical specs. "
            "Examples: ['User registration and login with JWT', "
            "'CRUD operations for contacts', "
            "'Role-based dashboard with analytics', "
            "'Stripe payment integration for premium plans']. "
            "Minimum 5 features. Be specific."
        )
    )

    ambiguities: list[str] = Field(
        default_factory=list,
        description=(
            "Things the user's prompt did NOT specify that would affect "
            "the system design. List as questions or statements. "
            "Examples: ['Payment provider not specified', "
            "'Dashboard metrics not defined', "
            "'Email notification system not mentioned', "
            "'Multi-tenant vs single-tenant architecture unclear']. "
            "Empty list if prompt is fully specified."
        )
    )

    assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "Decisions made to fill in the ambiguities above. "
            "Must correspond 1:1 with ambiguities list where possible. "
            "Examples: ['Assuming Stripe for payment processing', "
            "'Dashboard will show: total users, revenue, "
            "active sessions, recent activity', "
            "'Using SendGrid for transactional emails']. "
            "Each assumption must be actionable and specific."
        )
    )

    complexity_score: int = Field(
        ...,
        ge=1,
        le=10,
        description=(
            "Integer 1-10 estimating build complexity. "
            "1-3: Simple CRUD app (todo list, basic blog). "
            "4-6: Medium SaaS (CRM, invoicing, project management). "
            "7-9: Complex platform (marketplace, healthcare, multi-tenant). "
            "10: Enterprise-grade (ERP, banking, complex compliance)."
        )
    )
