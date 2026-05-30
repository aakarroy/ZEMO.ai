"""app_schema_model — Pydantic models for final AppSchema and pipeline metadata."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field, model_validator
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════
# NULL COERCION HELPER
# ═══════════════════════════════════════════════════════════════════

def _coerce_nulls(data: dict[str, Any], list_fields: set[str], dict_fields: set[str]) -> dict[str, Any]:
    """Convert explicit null/None values to [] or {} before Pydantic validates types.

    LLMs frequently output "field": null even when the schema demands a list or dict.
    Without this coercion, Pydantic raises dict_type / list_type errors.
    """
    for key in list_fields:
        if key in data and data[key] is None:
            data[key] = []
    for key in dict_fields:
        if key in data and data[key] is None:
            data[key] = {}
    return data


# ═══════════════════════════════════════════════════════════════════
# UI SCHEMA MODELS
# ═══════════════════════════════════════════════════════════════════

class UIComponent(BaseModel):
    """A single UI component on a page (widget, table, form, chart, etc.)."""

    @model_validator(mode='before')
    @classmethod
    def coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_nulls(data, set(), {'props'})
        return data

    id: str = Field(
        ...,
        description=(
            "Unique identifier for this component within the page. "
            "Use snake_case. Examples: 'contacts_table', 'add_contact_btn', "
            "'revenue_chart', 'user_profile_form'."
        )
    )

    type: str = Field(
        ...,
        description=(
            "Component type. Must be one of: "
            "DataTable, Form, Button, Modal, Chart, StatsCard, "
            "Sidebar, Header, SearchBar, FilterPanel, DetailView, "
            "FileUpload, RichTextEditor, Calendar, KanbanBoard, "
            "NotificationBanner, Pagination, Tabs, Breadcrumb."
        )
    )

    props: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Component-specific configuration properties. "
            "For DataTable: {'columns': ['name', 'email', 'status'], "
            "'sortable': true, 'searchable': true}. "
            "For StatsCard: {'label': 'Total Contacts', 'color': 'blue', "
            "'icon': 'users'}. "
            "For Form: {'fields': ['name', 'email', 'phone'], "
            "'submit_label': 'Save Contact'}. "
            "For Chart: {'chart_type': 'bar', 'x_axis': 'month', "
            "'y_axis': 'revenue'}."
        )
    )

    data_source: str | None = Field(
        None,
        description=(
            "API endpoint this component fetches data from. "
            "Format: 'api:/path/to/endpoint'. "
            "Examples: 'api:/contacts', 'api:/analytics/revenue', "
            "'api:/users/me'. "
            "Null for static components like Buttons or Headers."
        )
    )

    action: str | None = Field(
        None,
        description=(
            "What happens when user interacts with this component. "
            "Format: 'modal:modal_id', 'navigate:/route', "
            "'api_call:POST:/contacts', 'download:csv'. "
            "Null for display-only components like DataTable or Chart."
        )
    )


class UIPage(BaseModel):
    """A complete page/screen in the application."""

    @model_validator(mode='before')
    @classmethod
    def coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_nulls(data, {'components', 'access_roles'}, set())
        return data

    id: str = Field(
        ...,
        description=(
            "Unique page identifier in snake_case. "
            "Examples: 'dashboard', 'contacts_list', 'contact_detail', "
            "'settings', 'admin_analytics', 'login', 'register'."
        )
    )

    title: str = Field(
        ...,
        description="Human-readable page title shown in browser tab and page header."
    )

    route: str = Field(
        ...,
        description=(
            "URL route for this page. Must start with /. "
            "Examples: '/', '/dashboard', '/contacts', '/contacts/:id', "
            "'/admin/analytics', '/settings/billing'."
        )
    )

    layout: str = Field(
        "sidebar_main",
        description=(
            "Page layout template. Must be one of: "
            "'sidebar_main' (nav sidebar + main content area), "
            "'full_width' (no sidebar, full width), "
            "'centered' (centered card, used for login/register), "
            "'split' (two equal columns)."
        )
    )

    components: list[UIComponent] = Field(
        default_factory=list,
        description=(
            "All UI components on this page, in render order (top to bottom). "
            "Every page must have at least one component. "
            "Login/register pages typically have one Form component. "
            "Dashboard pages typically have StatsCards + Charts + DataTable."
        )
    )

    access_roles: list[str] = Field(
        default_factory=list,
        description=(
            "Role names that can access this page. "
            "Must match role names in AuthRole.name. "
            "Use ['*'] for public pages (login, register, landing). "
            "Examples: ['admin'], ['admin', 'manager'], ['admin', 'user']."
        )
    )


# ═══════════════════════════════════════════════════════════════════
# API SCHEMA MODELS
# ═══════════════════════════════════════════════════════════════════

class APIEndpoint(BaseModel):
    """A single REST API endpoint."""

    @model_validator(mode='before')
    @classmethod
    def coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_nulls(data, {'allowed_roles', 'validation_rules'}, set())
        return data

    id: str = Field(
        ...,
        description=(
            "Unique identifier for this endpoint in snake_case. "
            "Convention: {method}_{resource}. "
            "Examples: 'list_contacts', 'create_contact', "
            "'get_contact_by_id', 'update_contact', 'delete_contact', "
            "'get_analytics_summary'."
        )
    )

    path: str = Field(
        ...,
        description=(
            "URL path. Must start with /. Use :param for path params. "
            "Examples: '/contacts', '/contacts/:id', "
            "'/analytics/revenue', '/auth/login', '/users/me'."
        )
    )

    method: str = Field(
        ...,
        description=(
            "HTTP method. Must be EXACTLY one of: "
            "GET, POST, PUT, DELETE, PATCH. "
            "GET = read, POST = create, PUT = full update, "
            "PATCH = partial update, DELETE = remove."
        )
    )

    auth_required: bool = Field(
        True,
        description=(
            "Whether a valid JWT token is required to call this endpoint. "
            "False only for: POST /auth/login, POST /auth/register, "
            "GET /health, and explicitly public endpoints."
        )
    )

    allowed_roles: list[str] = Field(
        default_factory=list,
        description=(
            "Which roles can call this endpoint. "
            "Must match role names defined in AuthRole.name. "
            "Use ['*'] for public endpoints (auth_required=false). "
            "Examples: ['admin'], ['admin', 'manager', 'user']."
        )
    )

    request_body: dict[str, Any] | None = Field(
        None,
        description=(
            "Request body schema for POST/PUT/PATCH endpoints. "
            "Format: {'field_name': 'type:required|optional'}. "
            "Examples: {'name': 'string:required', "
            "'email': 'email:required', 'phone': 'string:optional', "
            "'role': 'string:required'}. "
            "Null for GET and DELETE endpoints."
        )
    )

    response_schema: dict[str, Any] | str = Field(
        ...,
        description=(
            "What this endpoint returns. "
            "Examples: {'type': 'array', 'items': 'Contact'}, "
            "{'type': 'object', 'model': 'Contact'}, "
            "{'type': 'object', 'fields': {'token': 'string', "
            "'user': 'User'}}, "
            "{'type': 'object', 'fields': {'deleted': 'boolean'}}."
        )
    )

    validation_rules: list[str] = Field(
        default_factory=list,
        description=(
            "Business validation rules enforced by this endpoint. "
            "Examples: ['Email must be unique across all users', "
            "'Phone must match E.164 format', "
            "'User can only update their own contacts', "
            "'Admin can update any contact']. "
            "Empty list for simple CRUD with no special rules."
        )
    )


# ═══════════════════════════════════════════════════════════════════
# DATABASE SCHEMA MODELS
# ═══════════════════════════════════════════════════════════════════

class DBColumn(BaseModel):
    """A single column in a database table."""

    name: str = Field(
        ...,
        description=(
            "Column name in snake_case. "
            "Examples: 'id', 'email', 'created_at', 'user_id', "
            "'stripe_customer_id', 'is_active', 'plan_type'."
        )
    )

    type: str = Field(
        ...,
        description=(
            "SQL data type. Must be one of: "
            "UUID, VARCHAR(255), VARCHAR(512), TEXT, INTEGER, BIGINT, "
            "BOOLEAN, DECIMAL(10,2), FLOAT, TIMESTAMP, DATE, JSON."
        )
    )

    nullable: bool = Field(
        True,
        description="False = NOT NULL constraint. Primary keys must be False."
    )

    primary_key: bool = Field(
        False,
        description=(
            "True only for the 'id' column of each table. "
            "Every table must have exactly one primary key named 'id' of type UUID."
        )
    )

    foreign_key: str | None = Field(
        None,
        description=(
            "Reference to another table's column. "
            "Format: 'table_name.column_name'. "
            "Examples: 'users.id', 'organizations.id', 'plans.id'. "
            "Null if this column is not a foreign key."
        )
    )

    unique: bool = Field(
        False,
        description=(
            "True if this column has a UNIQUE constraint. "
            "Examples: email in users table, slug in posts table."
        )
    )


class DBTable(BaseModel):
    """A single database table."""

    @model_validator(mode='before')
    @classmethod
    def coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_nulls(data, {'columns', 'indexes'}, set())
        return data

    name: str = Field(
        ...,
        description=(
            "Table name in snake_case plural. "
            "Examples: 'users', 'contacts', 'invoices', "
            "'subscription_plans', 'user_roles' (for junction tables)."
        )
    )

    columns: list[DBColumn] = Field(
        default_factory=list,
        description=(
            "All columns in this table. MANDATORY first column: "
            "{'name': 'id', 'type': 'UUID', 'nullable': false, "
            "'primary_key': true, 'foreign_key': null, 'unique': false}. "
            "MANDATORY last two columns: created_at (TIMESTAMP, not nullable) "
            "and updated_at (TIMESTAMP, not nullable)."
        )
    )

    indexes: list[str] = Field(
        default_factory=list,
        description=(
            "Index names for performance-critical columns. "
            "Convention: idx_{table}_{column}. "
            "Always index foreign key columns and frequently-queried fields. "
            "Examples: ['idx_contacts_user_id', 'idx_contacts_email', "
            "'idx_invoices_status', 'idx_invoices_created_at']."
        )
    )


# ═══════════════════════════════════════════════════════════════════
# AUTH SCHEMA MODELS
# ═══════════════════════════════════════════════════════════════════

class AuthRole(BaseModel):
    """A single role in the role-based access control system."""

    @model_validator(mode='before')
    @classmethod
    def coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_nulls(data, {'permissions'}, set())
        return data

    name: str = Field(
        ...,
        description=(
            "Role identifier in lowercase_with_underscores. "
            "Must match role names used in UIPage.access_roles "
            "and APIEndpoint.allowed_roles. "
            "Examples: 'admin', 'manager', 'basic_user', 'premium_user', 'guest'."
        )
    )

    permissions: list[str] = Field(
        default_factory=list,
        description=(
            "List of permission strings this role has. "
            "Format: 'resource:action'. "
            "Use ['*'] for admin (full access). "
            "Resource = entity name (lowercase plural). "
            "Action = read | write | delete | admin. "
            "Examples: ['contacts:read', 'contacts:write', "
            "'invoices:read', 'analytics:read']. "
            "Each role must have at least one permission."
        )
    )

    inherits_from: str | None = Field(
        None,
        description=(
            "Name of a role this role inherits all permissions from. "
            "Example: 'manager' inherits_from 'basic_user' means manager "
            "has all basic_user permissions PLUS its own. "
            "Null if this role does not inherit from any other role."
        )
    )


# ═══════════════════════════════════════════════════════════════════
# BUSINESS RULES MODEL
# ═══════════════════════════════════════════════════════════════════

class BusinessRule(BaseModel):
    """A single business logic rule that governs app behaviour."""

    @model_validator(mode='before')
    @classmethod
    def coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_nulls(data, {'affected_components'}, set())
        return data

    id: str = Field(
        ...,
        description=(
            "Unique rule identifier in snake_case. "
            "Examples: 'premium_gate_analytics', "
            "'admin_only_delete', 'contact_limit_basic_plan', "
            "'auto_invoice_on_subscription'."
        )
    )

    name: str = Field(
        ...,
        description="Human-readable rule name. 3-8 words."
    )

    condition: str = Field(
        ...,
        description=(
            "When this rule triggers. Write as a logical condition. "
            "Examples: \"user.plan == 'basic' AND page == 'analytics'\", "
            "\"user.role != 'admin' AND action == 'delete'\", "
            "\"contacts.count >= 100 AND user.plan == 'free'\"."
        )
    )

    action: str = Field(
        ...,
        description=(
            "What happens when condition is true. "
            "Examples: 'redirect_to_upgrade_page', "
            "'show_paywall_modal', 'return_403_forbidden', "
            "'send_limit_reached_email', 'auto_charge_stripe'."
        )
    )

    affected_components: list[str] = Field(
        default_factory=list,
        description=(
            "UIComponent.id or UIPage.id values this rule affects. "
            "Examples: ['analytics_page', 'revenue_chart', 'export_btn']. "
            "Empty if rule is purely backend (API/DB level)."
        )
    )


# ═══════════════════════════════════════════════════════════════════
# TOP-LEVEL SCHEMA MODEL
# ═══════════════════════════════════════════════════════════════════

class AppSchema(BaseModel):
    """
    The complete generated application schema.
    This is the final output of the entire pipeline.
    Contains all sub-schemas plus metadata.
    """

    @model_validator(mode='before')
    @classmethod
    def coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_nulls(
                data,
                {'ui_schema', 'api_schema', 'db_schema', 'auth_schema',
                 'business_rules', 'assumptions', 'warnings'},
                set(),
            )
        return data

    meta: dict[str, Any] = Field(
        ...,
        description=(
            "Generation metadata. Required keys: "
            "app_name (str), app_type (str), complexity_score (int), "
            "generated_at (ISO datetime string), "
            "prompt_hash (MD5 hex of original prompt), "
            "run_id (UUID string)."
        )
    )

    ui_schema: list[UIPage] = Field(
        default_factory=list,
        description="All pages in the application. Minimum 3 pages for any real app."
    )

    api_schema: list[APIEndpoint] = Field(
        default_factory=list,
        description=(
            "All REST API endpoints. "
            "Minimum: CRUD for each core entity + auth endpoints."
        )
    )

    db_schema: list[DBTable] = Field(
        default_factory=list,
        description=(
            "All database tables. "
            "Minimum: one table per core entity + users table always present."
        )
    )

    auth_schema: list[AuthRole] = Field(
        default_factory=list,
        description=(
            "All RBAC roles. "
            "Minimum 2 roles: admin and at least one non-admin role."
        )
    )

    business_rules: list[BusinessRule] = Field(
        default_factory=list,
        description=(
            "Business logic rules. "
            "Empty list acceptable for simple apps with no premium/gating logic."
        )
    )

    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions carried forward from IntentModel.assumptions."
    )

    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal issues detected during generation or repair. "
            "Examples: ['Max repair attempts reached, 2 issues remain', "
            "'Refinement skipped: no inconsistencies detected']."
        )
    )


# ═══════════════════════════════════════════════════════════════════
# PIPELINE METADATA MODELS
# ═══════════════════════════════════════════════════════════════════

class StageResult(BaseModel):
    """Metadata and output snapshot for a single pipeline stage."""

    stage_name: str = Field(
        ...,
        description=(
            "One of: 'intent_extraction', 'system_design', "
            "'schema_generation', 'refinement', 'validation'."
        )
    )

    status: str = Field(
        "pending",
        description="One of: 'pending', 'running', 'complete', 'failed', 'warning'."
    )

    output: Any | None = Field(
        None,
        description="Serialisable snapshot of this stage's output. Stored as JSON in DB."
    )

    latency_ms: float = Field(
        0.0,
        description="Wall-clock time for this stage in milliseconds."
    )

    tokens_used: int = Field(
        0,
        description="Total tokens (input + output) consumed by Claude in this stage."
    )

    error: str | None = Field(
        None,
        description="Error message if status=='failed'. Null otherwise."
    )


class GenerationResult(BaseModel):
    """
    Complete result of one full pipeline run.
    This is what gets saved to SQLite and returned to the Streamlit UI.
    """

    run_id: str = Field(
        ...,
        description="UUID4 string uniquely identifying this generation run."
    )

    prompt: str = Field(
        ...,
        description="The original user prompt exactly as entered."
    )

    stages: dict[str, StageResult] = Field(
        default_factory=dict,
        description=(
            "Results for each stage, keyed by stage_name. "
            "Keys: 'intent_extraction', 'system_design', "
            "'schema_generation', 'refinement', 'validation'."
        )
    )

    final_schema: AppSchema | None = Field(
        None,
        description=(
            "The validated AppSchema. "
            "Null if the pipeline failed before producing a complete schema."
        )
    )

    total_latency_ms: float = Field(
        0.0,
        description="Total wall-clock time for the entire pipeline run."
    )

    repair_attempts: int = Field(
        0,
        description="Number of repair cycles performed by RepairEngine."
    )

    validation_passed: bool = Field(
        False,
        description="True if ConsistencyChecker found zero errors in the final schema."
    )

    total_tokens_used: int = Field(
        0,
        description="Sum of all tokens across all Claude API calls in this run."
    )

    estimated_cost_usd: float = Field(
        0.0,
        description=(
            "Estimated API cost in USD. "
            "Calculated as: (total_tokens / 1_000_000) * 3.0 "
            "(Claude Sonnet pricing approximation)."
        )
    )

    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO 8601 UTC timestamp of when this run was initiated."
    )
