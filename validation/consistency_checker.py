"""
consistency_checker.py

Cross-layer schema validation for the generated AppSchema.

Runs 3 independent consistency checks:
  1. API ↔ DB consistency:
     POST/PUT/PATCH request body fields must exist as DB columns.
  2. UI ↔ API consistency:
     UIComponent data_source paths must map to existing API endpoints.
  3. Auth consistency:
     Role names in UIPage.access_roles and APIEndpoint.allowed_roles
     must be defined in AppSchema.auth_schema.

Returns a ValidationReport with structured error objects.
Does NOT modify the schemas — only reports problems.
"""

from dataclasses import dataclass, field
from models.app_schema_model import AppSchema


@dataclass
class ConsistencyError:
    """
    A single detected inconsistency between schema layers.

    Attributes:
        check_type    : Machine-readable error category
        severity      : "error" (blocks output) or "warning" (noted but allowed)
        layer         : Which schema layer pair has the issue (e.g. "UI/API")
        description   : Human-readable explanation of the problem
        affected_fields: Specific field names, paths, or IDs involved
        suggested_fix : Actionable recommendation for the repair engine
    """
    check_type: str
    severity: str          # "error" | "warning"
    layer: str             # "API/DB" | "UI/API" | "UI/Auth" | "API/Auth"
    description: str
    affected_fields: list[str] = field(default_factory=list)
    suggested_fix: str = ""


@dataclass
class ValidationReport:
    """
    Aggregated result of all consistency checks.

    Attributes:
        is_valid      : True only if zero "error"-severity ConsistencyErrors.
                        Warnings do NOT make is_valid=False.
        errors        : List of severity="error" ConsistencyErrors.
        warnings      : List of severity="warning" ConsistencyErrors.
        passed_checks : Count of checks that found zero errors.
        failed_checks : Count of checks that found at least one error.
    """
    is_valid: bool
    errors: list[ConsistencyError] = field(default_factory=list)
    warnings: list[ConsistencyError] = field(default_factory=list)
    passed_checks: int = 0
    failed_checks: int = 0


# Field names that are special-cased and do NOT need to exist in DB.
# These are auth/session fields that only live in request bodies.
_EXEMPT_REQUEST_FIELDS = frozenset({
    "password", "token", "confirm_password", "current_password",
    "new_password", "access_token", "refresh_token", "otp", "code",
    "captcha", "remember_me", "two_factor_code",
})

# Path suffixes that are virtual (not real DB-backed endpoints)
_VIRTUAL_PATH_SUFFIXES = ("/count", "/stats", "/summary", "/export",
                           "/health", "/me", "/logout", "/login",
                           "/register", "/search", "/bulk")


class ConsistencyChecker:
    """
    Validates cross-layer consistency of a generated AppSchema.

    Usage:
        checker = ConsistencyChecker()
        report = checker.run_all_checks(app_schema)
        if not report.is_valid:
            # pass to RepairEngine
    """

    def check_api_db_consistency(self, app_schema: AppSchema) -> list[ConsistencyError]:
        """
        Check: All POST/PUT/PATCH request body fields must exist in DB.

        Logic:
          - Collect every column name across ALL DB tables.
          - For each POST/PUT/PATCH endpoint with a request_body:
              - For each field name in request_body:
                  - Skip if field is in _EXEMPT_REQUEST_FIELDS.
                  - Error if field not found in any DB table's columns.

        Why cross ALL tables (not just the "matching" table):
          We don't do entity-to-table matching here because the naming
          might be inconsistent (that's Stage 4's job). We just need
          the field to exist SOMEWHERE in the DB schema.
        """
        errors = []

        # Build set of all column names across all tables
        all_db_columns: set[str] = set()
        for table in app_schema.db_schema:
            for col in (table.columns or []):
                all_db_columns.add(col.name)

        # Check each write endpoint
        for endpoint in app_schema.api_schema:
            if endpoint.method not in ("POST", "PUT", "PATCH"):
                continue
            if not endpoint.request_body:
                continue

            for field_name in endpoint.request_body.keys():
                if field_name in _EXEMPT_REQUEST_FIELDS:
                    continue
                if field_name not in all_db_columns:
                    errors.append(ConsistencyError(
                        check_type="api_field_not_in_db",
                        severity="error",
                        layer="API/DB",
                        description=(
                            f"Endpoint {endpoint.method} {endpoint.path} "
                            f"has request body field '{field_name}' "
                            f"which does not exist as a column in any DB table."
                        ),
                        affected_fields=[field_name, endpoint.path],
                        suggested_fix=(
                            f"Either add column '{field_name}' to the relevant "
                            f"DB table, or remove '{field_name}' from the "
                            f"request body of {endpoint.path}."
                        )
                    ))

        return errors

    def check_ui_api_consistency(self, app_schema: AppSchema) -> list[ConsistencyError]:
        """
        Check: UI components referencing API paths must have those paths exist.

        Logic:
          - Collect all API paths (exact match and base paths).
          - For each UIComponent with data_source starting with "api:":
              - Extract the path (strip "api:" prefix, strip query params).
              - Strip known virtual suffixes (/count, /stats, etc.).
              - Check if the base path exists in api_schema.
              - If not found: error.

        Why strip virtual suffixes:
          Generators often use "api:/contacts/count" which maps to
          the base "/contacts" endpoint with a count parameter.
          We don't penalise this — it's expected behaviour.
        """
        errors = []

        # Build set of all defined API paths
        api_paths: set[str] = {ep.path for ep in app_schema.api_schema}

        for page in app_schema.ui_schema:
            for component in (page.components or []):
                if not component.data_source:
                    continue
                if not component.data_source.startswith("api:"):
                    continue

                # Extract raw path
                raw_path = component.data_source[4:]  # Strip "api:"
                # Remove query string
                base_path = raw_path.split("?")[0]
                # Try to match progressively shorter paths
                # (strip known virtual suffixes)
                candidate_paths = [base_path]
                for suffix in _VIRTUAL_PATH_SUFFIXES:
                    if base_path.endswith(suffix):
                        candidate_paths.append(base_path[: -len(suffix)])

                # Also try the parent path (e.g., /contacts/count → /contacts)
                parts = base_path.rstrip("/").rsplit("/", 1)
                if len(parts) == 2 and parts[0]:
                    candidate_paths.append(parts[0])

                found = any(cp in api_paths for cp in candidate_paths)

                if not found:
                    errors.append(ConsistencyError(
                        check_type="ui_references_missing_api_path",
                        severity="error",
                        layer="UI/API",
                        description=(
                            f"Page '{page.id}' component '{component.id}' "
                            f"has data_source='{component.data_source}' "
                            f"but no API endpoint with path '{base_path}' "
                            f"(or parent path) exists in the API schema."
                        ),
                        affected_fields=[component.id, base_path],
                        suggested_fix=(
                            f"Add a GET {base_path} endpoint to the API schema, "
                            f"or update component '{component.id}' data_source "
                            f"to reference an existing API path."
                        )
                    ))

        return errors

    def check_auth_consistency(self, app_schema: AppSchema) -> list[ConsistencyError]:
        """
        Check: All role names used in pages and endpoints must be defined in auth.

        Logic:
          - Build set of defined role names from auth_schema.
          - Add "*" as a valid special role.
          - For each UIPage.access_roles: check each role is defined.
          - For each APIEndpoint.allowed_roles: check each role is defined.
        """
        errors = []

        defined_roles: set[str] = {role.name for role in app_schema.auth_schema}
        defined_roles.add("*")  # "*" is a valid wildcard role

        # Check UI pages
        for page in app_schema.ui_schema:
            for role in (page.access_roles or []):
                if role not in defined_roles:
                    errors.append(ConsistencyError(
                        check_type="ui_uses_undefined_role",
                        severity="error",
                        layer="UI/Auth",
                        description=(
                            f"Page '{page.id}' (route: {page.route}) "
                            f"requires role '{role}' in access_roles, "
                            f"but '{role}' is not defined in auth_schema. "
                            f"Defined roles: {sorted(defined_roles - {'*'})}."
                        ),
                        affected_fields=[role, page.id],
                        suggested_fix=(
                            f"Add role '{role}' to auth_schema with appropriate "
                            f"permissions, or change page '{page.id}' access_roles "
                            f"to use an existing role."
                        )
                    ))

        # Check API endpoints
        for endpoint in app_schema.api_schema:
            for role in (endpoint.allowed_roles or []):
                if role not in defined_roles:
                    errors.append(ConsistencyError(
                        check_type="api_uses_undefined_role",
                        severity="error",
                        layer="API/Auth",
                        description=(
                            f"Endpoint {endpoint.method} {endpoint.path} "
                            f"has allowed_role '{role}' which is not defined "
                            f"in auth_schema. "
                            f"Defined roles: {sorted(defined_roles - {'*'})}."
                        ),
                        affected_fields=[role, endpoint.path],
                        suggested_fix=(
                            f"Add role '{role}' to auth_schema, or update "
                            f"endpoint {endpoint.path} allowed_roles to use "
                            f"an existing role name."
                        )
                    ))

        return errors

    def run_all_checks(self, app_schema: AppSchema) -> ValidationReport:
        """
        Run all 3 consistency checks and return a unified ValidationReport.

        Args:
            app_schema: The complete AppSchema to validate.

        Returns:
            ValidationReport with is_valid, errors, warnings, and check counts.
        """
        all_found_errors: list[ConsistencyError] = []
        passed = 0
        failed = 0

        checks = [
            ("API-DB Consistency",  self.check_api_db_consistency),
            ("UI-API Consistency",  self.check_ui_api_consistency),
            ("Auth Consistency",    self.check_auth_consistency),
        ]

        for check_name, check_fn in checks:
            try:
                check_errors = check_fn(app_schema)
                if check_errors:
                    failed += 1
                    print(
                        f"  [Validator] ❌ {check_name}: "
                        f"{len(check_errors)} issue(s)"
                    )
                    all_found_errors.extend(check_errors)
                else:
                    passed += 1
                    print(f"  [Validator] ✅ {check_name}: OK")
            except Exception as e:
                # If a check itself crashes, record as an error
                failed += 1
                print(f"  [Validator] ❌ {check_name} crashed: {e}")
                all_found_errors.append(ConsistencyError(
                    check_type="check_crashed",
                    severity="error",
                    layer="Internal",
                    description=f"Check '{check_name}' raised an exception: {e}",
                    suggested_fix="Investigate the validator code for this check."
                ))

        hard_errors = [e for e in all_found_errors if e.severity == "error"]
        warnings = [e for e in all_found_errors if e.severity == "warning"]

        return ValidationReport(
            is_valid=len(hard_errors) == 0,
            errors=hard_errors,
            warnings=warnings,
            passed_checks=passed,
            failed_checks=failed,
        )
