"""design_model — Pydantic model for Stage 2 (System Design) output."""
from pydantic import BaseModel, Field


class EntityField(BaseModel):
    """
    Represents a single field/column within an entity.
    Used by EntityRelation to define the entity's data structure.
    """

    name: str = Field(
        ...,
        description=(
            "Column name in snake_case. "
            "Examples: 'id', 'email', 'created_at', 'user_id', 'stripe_customer_id'."
        )
    )

    type: str = Field(
        ...,
        description=(
            "SQL data type string. "
            "Must be one of: UUID, VARCHAR(255), TEXT, INTEGER, "
            "BOOLEAN, DECIMAL(10,2), TIMESTAMP, DATE, FLOAT, BIGINT."
        )
    )

    nullable: bool = Field(
        True,
        description=(
            "Whether this field can be NULL in the database. "
            "Primary keys and required fields should be False."
        )
    )


class EntityRelation(BaseModel):
    """
    Represents a single domain entity (maps to one database table).
    """

    entity_name: str = Field(
        ...,
        description=(
            "PascalCase entity name. Same as the names in IntentModel.core_entities. "
            "Examples: 'User', 'Contact', 'Invoice', 'Product'."
        )
    )

    fields: list[EntityField] = Field(
        ...,
        description=(
            "All fields for this entity. MUST include: "
            "{'name': 'id', 'type': 'UUID', 'nullable': false} as the first field. "
            "Add created_at (TIMESTAMP) and updated_at (TIMESTAMP) to every entity. "
            "Add foreign key fields (e.g. user_id UUID) for all relationships."
        )
    )

    relations: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable relationship descriptions. "
            "Format: 'has_many: Contact', 'belongs_to: User', "
            "'has_one: Profile', 'many_to_many: Tag'. "
            "List ALL relationships this entity has with other entities."
        )
    )


class DesignModel(BaseModel):
    """
    Output contract for Stage 2: System Design.

    This is the input to Stage 3 (SchemaGenerator) along with IntentModel.
    It defines the full architecture before any schema code is generated.
    """

    entities: list[EntityRelation] = Field(
        ...,
        description=(
            "Every entity in the system, fully defined with fields and relations. "
            "Must include ALL entities listed in IntentModel.core_entities. "
            "May include junction/pivot tables not in core_entities "
            "(e.g. UserRole, TagPost for many-to-many)."
        )
    )

    auth_strategy: str = Field(
        ...,
        description=(
            "Authentication mechanism description. "
            "Examples: 'JWT access tokens (15min expiry) with refresh tokens "
            "(7 day expiry), stored in HTTP-only cookies', "
            "'Session-based auth with bcrypt password hashing', "
            "'OAuth2 via Google with JWT session tokens'."
        )
    )

    role_hierarchy: list[str] = Field(
        ...,
        description=(
            "Role hierarchy from most to least privileged. "
            "Format each entry as comparison string. "
            "Examples: ['admin > manager > user', 'admin > premium_user > basic_user']. "
            "Must include ALL roles from IntentModel.user_roles."
        )
    )

    data_flows: list[str] = Field(
        ...,
        description=(
            "Key data flows describing how entities interact. "
            "Write as actor-action-object sentences. "
            "Examples: ['User creates Contact with name, email, phone', "
            "'Admin views aggregated analytics across all Users', "
            "'Payment triggers Plan upgrade for User account', "
            "'Contact belongs to exactly one User (owner)']. "
            "Minimum 5 flows for any non-trivial app."
        )
    )

    third_party_services: list[str] = Field(
        default_factory=list,
        description=(
            "External services the app integrates with. "
            "Examples: ['Stripe for subscription billing and payment processing', "
            "'SendGrid for transactional email (signup, password reset)', "
            "'AWS S3 for file upload storage', "
            "'Twilio for SMS notifications']. "
            "Empty list if app has no external integrations."
        )
    )
