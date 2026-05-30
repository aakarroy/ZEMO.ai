"""
stage3_schema.py

Stage 3 of the pipeline: Schema Generation.

Makes FIVE separate Gemini calls, one per schema type:
  1. UI Schema   → list[UIPage]
  2. API Schema  → list[APIEndpoint]
  3. DB Schema   → list[DBTable]
  4. Auth Schema → list[AuthRole]
  5. Business Rules → list[BusinessRule]

WHY FIVE CALLS instead of one:
  Each schema type has different structure, different constraints,
  and different quality bar. Combining them into one prompt produces
  worse output, harder-to-repair errors, and exceeds token limits
  for complex apps. Separate calls = targeted prompts = better output.

All calls run SEQUENTIALLY (not parallel) to avoid Gemini rate limits
and to keep the code simple and debuggable in Streamlit.
"""

import json
from pipeline import PipelineStage
from models.intent_model import IntentModel
from models.design_model import DesignModel
from models.app_schema_model import (
    UIPage, APIEndpoint, DBTable, AuthRole, BusinessRule
)


# ─────────────────────────────────────────────────────────────────────
# SYSTEM PROMPTS — one per schema type
# ─────────────────────────────────────────────────────────────────────

UI_SYSTEM_PROMPT = """
You are a UI/UX Architect generating a complete page schema for a web application.

Generate ALL pages the app needs. For every page, define every component.

ALLOWED COMPONENT TYPES (use ONLY these):
DataTable, Form, Button, Modal, Chart, StatsCard, Sidebar, Header,
SearchBar, FilterPanel, DetailView, FileUpload, RichTextEditor,
Calendar, KanbanBoard, NotificationBanner, Pagination, Tabs, Breadcrumb

COMPONENT RULES:
- data_source format: "api:/path" (must match a real API path you expect to exist)
- action format: "modal:modal_id" | "navigate:/route" | "api_call:METHOD:/path" | "download:format"
- props: always provide relevant configuration. Keep component props concise to avoid exceeding output token limits.
- Every page needs at least 1 component. Login pages need 1 Form. Dashboards need 3+.
CRITICAL RULES FOR JSON:
- NEVER output null for arrays/lists. If a field like access_roles or components is empty, output an empty array [].
- NEVER output null for objects/dicts. If a field like props is empty, output an empty object {}.
- For nullable string fields (data_source, action), use JSON null, NOT the string "null".

BRANDING REQUIREMENT:
The app_name provided in the schema is the official brand name (e.g., "CartNova", "NexusCRM").
Use it consistently in page titles, headers, and any title-like fields throughout the UI schema.
Do NOT use generic names like "Dashboard" in isolation — prefer branded variants where it fits
(e.g., "CartNova Dashboard", "NexusCRM Contacts"). The login page title should include the brand name.

LAYOUT OPTIONS: "sidebar_main" | "full_width" | "centered" | "split"
  - Use "centered" for login and register pages
  - Use "sidebar_main" for all authenticated app pages
  - Use "full_width" for landing pages

ACCESS ROLES: must use role names from the app's role list.
  Use ["*"] for public pages (login, register).

MANDATORY PAGES for any authenticated app:
  - Login page (route: /login, layout: centered, access: ["*"])
  - Dashboard/Home (route: /, layout: sidebar_main)
  - At least one page per core entity (list view)
  - Settings page (route: /settings)

OUTPUT: A JSON array of UIPage objects. No explanation text.
[
  {
    "id": "snake_case_unique_id",
    "title": "Human Readable Title",
    "route": "/route-path",
    "layout": "sidebar_main",
    "components": [
      {
        "id": "component_snake_case_id",
        "type": "ComponentType",
        "props": {"key": "value"},
        "data_source": "api:/endpoint",
        "action": null
      }
    ],
    "access_roles": ["role_name"]
  }
]
"""

API_SYSTEM_PROMPT = """
You are a Backend API Architect generating a complete REST API schema.

Generate ALL endpoints the app needs. Follow REST conventions strictly.

HTTP METHOD RULES:
- GET: retrieve data (no body)
- POST: create new resource (has body)
- PUT: full update of resource (has body, uses :id)
- PATCH: partial update (has body, uses :id)
- DELETE: remove resource (no body, uses :id)

MANDATORY ENDPOINTS for every entity named X:
  GET    /x-plural           → list all (paginated)
  POST   /x-plural           → create one
  GET    /x-plural/:id       → get one by ID
  PUT    /x-plural/:id       → update one
  DELETE /x-plural/:id       → delete one

MANDATORY AUTH ENDPOINTS:
  POST /auth/login    → {email, password} → {token, user}  auth_required: false
  POST /auth/register → {email, password, name} → {token, user}  auth_required: false
  POST /auth/logout   → {} → {success: boolean}  auth_required: true
  GET  /users/me      → {} → User object  auth_required: true

REQUEST BODY format: {"field_name": "type:required|optional"}
  Types: string, email, password, integer, boolean, uuid, decimal, date, json
  Note: Do NOT include 'id', 'created_at', 'updated_at' in POST request bodies.

VALIDATION RULES: At minimum add email format rules, required field rules.
  Add role-specific rules (e.g. "Users can only update their own records").

RESPONSE SCHEMA RULES:
- For response_schema, ALWAYS prefer a structured dictionary like {"type": "array", "items": "EntityName"} or {"type": "object", "model": "EntityName"}.
- If referencing a core entity type by name alone, you may use a string identifier like "User" or a dictionary like {"type": "User"}.

CRITICAL RULES FOR JSON:
- NEVER output null for arrays/lists. If a field like allowed_roles or validation_rules is empty, output an empty array [].
- NEVER output null for objects/dicts. If a field like request_body is not needed, use JSON null (not the string "null").

OUTPUT: A JSON array. No explanation text.
[
  {
    "id": "verb_resource_qualifier",
    "path": "/resource-plural",
    "method": "GET",
    "auth_required": true,
    "allowed_roles": ["role_name"],
    "request_body": null,
    "response_schema": {"type": "array", "items": "EntityName"},
    "validation_rules": ["rule description"]
  }
]
"""

DB_SYSTEM_PROMPT = """
You are a Database Architect generating a normalized SQL database schema.

Generate ALL tables the app needs.

MANDATORY RULES:
1. Every table's FIRST column: {"name":"id","type":"UUID","nullable":false,"primary_key":true,"foreign_key":null,"unique":false}
2. Every table's LAST two columns:
   {"name":"created_at","type":"TIMESTAMP","nullable":false,"primary_key":false,"foreign_key":null,"unique":false}
   {"name":"updated_at","type":"TIMESTAMP","nullable":false,"primary_key":false,"foreign_key":null,"unique":false}
3. Foreign keys format: "referenced_table.id" (always reference 'id' column)
4. UNIQUE constraint: email in users table, slug in posts, etc.
5. Table names: snake_case plural (users, contacts, invoice_items)

ALLOWED COLUMN TYPES ONLY:
UUID, VARCHAR(255), VARCHAR(512), TEXT, INTEGER, BIGINT,
BOOLEAN, DECIMAL(10,2), FLOAT, TIMESTAMP, DATE, JSON

INDEX NAMING: idx_{table_name}_{column_name}
Always index: all foreign key columns, email columns, status/type columns,
created_at on high-write tables.

MANDATORY TABLE: "users" must always be present with these columns minimum:
  id (UUID, PK), email (VARCHAR(255), UNIQUE, NOT NULL),
  password_hash (VARCHAR(255), NOT NULL), role (VARCHAR(50), NOT NULL),
  is_active (BOOLEAN, NOT NULL), created_at, updated_at

CRITICAL RULES FOR JSON:
- NEVER output null for arrays/lists. If a field like columns or indexes is empty, output an empty array [].
- NEVER output null for objects/dicts.

OUTPUT: A JSON array of table objects.
[
  {
    "name": "table_snake_case_plural",
    "columns": [
      {
        "name": "column_name",
        "type": "TYPE",
        "nullable": true,
        "primary_key": false,
        "foreign_key": "other_table.id or null",
        "unique": false
      }
    ],
    "indexes": ["idx_table_column"]
  }
]
"""

AUTH_SYSTEM_PROMPT = """
You are a Security Architect generating a Role-Based Access Control schema.

Generate ALL roles the app needs.

PERMISSION FORMAT: "resource:action"
  resource = lowercase plural entity name (contacts, invoices, users)
  action   = read | write | delete | admin
  
Special permissions:
  "*" = full access to everything (admin role only)
  "analytics:read" = can view analytics/reports
  "billing:admin" = can manage billing/payments

MANDATORY ROLE: "admin" must always be present with permissions: ["*"]

ROLE HIERARCHY: If role B inherits_from role A, then B has all of A's
permissions plus its own. Use inherits_from for roles that extend
a base role (e.g. premium_user inherits_from basic_user).

COVERAGE RULE: Every entity in the app must have at least one non-admin
role with at minimum "entity:read" permission.

CRITICAL RULES FOR JSON:
- NEVER output null for arrays/lists. If a field like permissions is empty, output an empty array [].
- NEVER output null for objects/dicts.

OUTPUT: A JSON array of role objects.
[
  {
    "name": "role_lowercase_snake",
    "permissions": ["resource:action"],
    "inherits_from": "parent_role_name or null"
  }
]
"""

BUSINESS_RULES_PROMPT = """
You are a Product Manager defining the business logic rules for a web application.

Generate business rules that govern app behaviour beyond simple CRUD.

RULE TYPES TO CONSIDER:
- Access gating (premium features, plan limits)
- Data ownership (users can only edit their own records)
- Capacity limits (max 100 contacts on free plan)
- Automated actions (send email on event, charge on subscription renewal)
- Approval workflows (manager must approve before publish)
- Role restrictions (only admin can delete permanently)

CONDITION FORMAT: Logical expression using entity attributes.
  Examples:
  "user.plan == 'free' AND contacts.count >= 100"
  "user.role != 'admin' AND record.owner_id != user.id"
  "invoice.status == 'sent' AND days_since_sent > 30"

ACTION FORMAT: System action verb.
  Examples: "show_upgrade_modal", "return_403", "redirect:/upgrade",
  "send_email:payment_reminder", "auto_archive", "block_creation"

AFFECTED COMPONENTS: List UIComponent.id or UIPage.id values that
enforce or display this rule. Empty list for pure backend rules.

CRITICAL RULES FOR JSON:
- NEVER output null for arrays/lists. If a field like affected_components is empty, output an empty array [].
- NEVER output null for objects/dicts.

OUTPUT: A JSON array. If the app is simple with no gating logic, return [].
[
  {
    "id": "snake_case_rule_id",
    "name": "Human Readable Rule Name",
    "condition": "logical condition expression",
    "action": "system_action",
    "affected_components": ["component_or_page_id"]
  }
]
"""


# ─────────────────────────────────────────────────────────────────────
# STAGE CLASS
# ─────────────────────────────────────────────────────────────────────

class SchemaGenerator(PipelineStage):
    """
    Stage 3: Schema Generation.

    Makes 5 separate Gemini calls to generate:
      - UI Schema (list[UIPage])
      - API Schema (list[APIEndpoint])
      - DB Schema (list[DBTable])
      - Auth Schema (list[AuthRole])
      - Business Rules (list[BusinessRule])

    Results are returned as a dict with standardised keys.
    The _tokens and _latency keys carry aggregate stats.
    """

    stage_name = "schema_generation"

    def _build_context(self, intent: IntentModel, design: DesignModel) -> str:
        """
        Build a shared context string used in all sub-prompts.
        Gives Gemini full awareness of what we're building.
        """
        return (
            "APP SPECIFICATION:\n"
            f"{intent.model_dump_json(indent=2)}\n\n"
            "SYSTEM DESIGN:\n"
            f"{design.model_dump_json(indent=2)}"
        )

    def generate_ui(
        self, intent: IntentModel, design: DesignModel
    ) -> tuple[list[UIPage], int, float]:
        """Generate UI page/component schema."""
        print("  [Stage 3] Generating UI schema...")
        context = self._build_context(intent, design)
        user_content = (
            f"Generate the complete UI schema for this app.\n\n{context}\n\n"
            "Create ALL pages needed. Use the role names and entity names from the spec."
        )
        items, tokens, latency = self.call_gemini_list(
            system_prompt=UI_SYSTEM_PROMPT,
            user_content=user_content,
            item_model=UIPage,
        )
        print(f"  [Stage 3] UI: {len(items)} pages generated")
        return items, tokens, latency

    def generate_api(
        self, intent: IntentModel, design: DesignModel
    ) -> tuple[list[APIEndpoint], int, float]:
        """Generate REST API endpoint schema."""
        print("  [Stage 3] Generating API schema...")
        context = self._build_context(intent, design)
        user_content = (
            f"Generate the complete REST API schema for this app.\n\n{context}\n\n"
            "Include CRUD endpoints for every entity AND auth endpoints."
        )
        items, tokens, latency = self.call_gemini_list(
            system_prompt=API_SYSTEM_PROMPT,
            user_content=user_content,
            item_model=APIEndpoint,
        )
        print(f"  [Stage 3] API: {len(items)} endpoints generated")
        return items, tokens, latency

    def generate_db(
        self, intent: IntentModel, design: DesignModel
    ) -> tuple[list[DBTable], int, float]:
        """Generate database table schema."""
        print("  [Stage 3] Generating DB schema...")
        context = self._build_context(intent, design)
        user_content = (
            f"Generate the complete database schema for this app.\n\n{context}\n\n"
            "Include a 'users' table. All entities from the design must have a table."
        )
        items, tokens, latency = self.call_gemini_list(
            system_prompt=DB_SYSTEM_PROMPT,
            user_content=user_content,
            item_model=DBTable,
        )
        print(f"  [Stage 3] DB: {len(items)} tables generated")
        return items, tokens, latency

    def generate_auth(
        self, intent: IntentModel, design: DesignModel
    ) -> tuple[list[AuthRole], int, float]:
        """Generate RBAC role/permission schema."""
        print("  [Stage 3] Generating Auth schema...")
        context = self._build_context(intent, design)
        user_content = (
            f"Generate the complete RBAC auth schema for this app.\n\n{context}\n\n"
            f"Roles needed: {intent.user_roles}. "
            "Every role must have specific permissions."
        )
        items, tokens, latency = self.call_gemini_list(
            system_prompt=AUTH_SYSTEM_PROMPT,
            user_content=user_content,
            item_model=AuthRole,
        )
        print(f"  [Stage 3] Auth: {len(items)} roles generated")
        return items, tokens, latency

    def generate_business_rules(
        self, intent: IntentModel, design: DesignModel
    ) -> tuple[list[BusinessRule], int, float]:
        """Generate business logic rules."""
        print("  [Stage 3] Generating business rules...")
        context = self._build_context(intent, design)
        user_content = (
            f"Generate business rules for this app.\n\n{context}\n\n"
            "If this is a simple app with no gating logic, return an empty array []."
        )
        items, tokens, latency = self.call_gemini_list(
            system_prompt=BUSINESS_RULES_PROMPT,
            user_content=user_content,
            item_model=BusinessRule,
        )
        print(f"  [Stage 3] Rules: {len(items)} business rules generated")
        return items, tokens, latency

    def generate_all(
        self,
        intent: IntentModel,
        design: DesignModel,
        progress_callback=None,
    ) -> dict:
        """
        Run all 5 schema generators sequentially.

        Args:
            intent: IntentModel from Stage 1
            design: DesignModel from Stage 2
            progress_callback: Optional callable(schema_key: str) called after
                               each sub-generator completes. Used by Streamlit
                               to update the progress indicator.

        Returns:
            dict with keys:
              "ui_schema"      → list[UIPage]
              "api_schema"     → list[APIEndpoint]
              "db_schema"      → list[DBTable]
              "auth_schema"    → list[AuthRole]
              "business_rules" → list[BusinessRule]
              "_tokens"        → int (total tokens across all 5 calls)
              "_latency"       → float (total latency in ms across all 5 calls)

        Raises:
            StageValidationError: if any sub-generator fails.
        """
        print(f"\n[Stage 3] Generating all schemas for '{intent.app_name}'...")

        total_tokens = 0
        total_latency = 0.0
        results = {}

        generators = [
            ("ui_schema",      self.generate_ui),
            ("api_schema",     self.generate_api),
            ("db_schema",      self.generate_db),
            ("auth_schema",    self.generate_auth),
            ("business_rules", self.generate_business_rules),
        ]

        for key, generator_fn in generators:
            items, tokens, latency = generator_fn(intent, design)
            results[key] = items
            total_tokens += tokens
            total_latency += latency
            if progress_callback:
                progress_callback(key)

        results["_tokens"] = total_tokens
        results["_latency"] = total_latency

        print(
            f"[Stage 3] ✅ Complete: "
            f"UI={len(results['ui_schema'])} pages, "
            f"API={len(results['api_schema'])} endpoints, "
            f"DB={len(results['db_schema'])} tables, "
            f"Auth={len(results['auth_schema'])} roles, "
            f"Rules={len(results['business_rules'])}, "
            f"Total tokens={total_tokens}"
        )

        return results
