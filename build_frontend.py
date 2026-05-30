"""
build_frontend.py

Stage 5 of the ZEMO.ai pipeline: Full-Stack Code Generator.

Standalone Streamlit app. Run with: streamlit run build_frontend.py

Reads the most recent successful schema from compiler.db,
sends it to Gemini to generate full-stack source code,
parses the response with self-healing logic, and delivers
a downloadable ZIP + in-browser IDE view.
"""

import io
import json
import re
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
import os

# ── Environment ────────────────────────────────────────────────────
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_PATH = "compiler.db"



# ── Custom CSS ─────────────────────────────────────────────────────
st.markdown("""
<style>
    .ide-header {
        background: #1e293b;
        padding: 8px 16px;
        border-radius: 6px 6px 0 0;
        border-bottom: 1px solid #334155;
        font-family: monospace;
        font-size: 0.85rem;
        color: #94a3b8;
    }
    .file-badge {
        background: #0f172a;
        border: 1px solid #334155;
        padding: 2px 8px;
        border-radius: 4px;
        font-family: monospace;
        font-size: 0.8rem;
        color: #6366f1;
    }
    .log-line {
        font-family: monospace;
        font-size: 0.82rem;
    }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# DATABASE HELPERS — sqlite3 only, no SQLAlchemy
# ═══════════════════════════════════════════════════════════════════

def _get_db_connection() -> sqlite3.Connection:
    """
    Open a synchronous sqlite3 connection to compiler.db.
    Returns connection with row_factory=sqlite3.Row for dict-like access.
    Raises FileNotFoundError if compiler.db does not exist.
    """
    if not Path(DATABASE_PATH).exists():
        raise FileNotFoundError(
            f"compiler.db not found at {Path(DATABASE_PATH).resolve()}. "
            "Run the main pipeline (streamlit run app.py) and generate "
            "at least one schema before using the Full-Stack Studio."
        )
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_latest_schema() -> tuple[dict | None, str | None]:
    """
    Query generation_runs for the most recent row where:
      - is_valid = 1 (validation passed)
      - final_schema IS NOT NULL

    Returns:
        (schema_dict, run_id) if found
        (None, None)          if no valid run exists

    Falls back to most recent ANY run if no valid runs exist,
    so the studio still works during development when is_valid=0.
    """
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()

        # Primary: most recent valid run
        cursor.execute("""
            SELECT id, final_schema
            FROM generation_runs
            WHERE is_valid = 1
              AND final_schema IS NOT NULL
              AND final_schema != ''
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()

        # Fallback: any run with a schema
        if row is None:
            cursor.execute("""
                SELECT id, final_schema
                FROM generation_runs
                WHERE final_schema IS NOT NULL
                  AND final_schema != ''
                ORDER BY created_at DESC
                LIMIT 1
            """)
            row = cursor.fetchone()

        conn.close()

        if row is None:
            return None, None

        schema_dict = json.loads(row["final_schema"])
        return schema_dict, row["id"]

    except FileNotFoundError as e:
        st.error(str(e))
        return None, None
    except json.JSONDecodeError as e:
        st.error(
            f"The stored schema JSON is corrupted and cannot be parsed: {e}. "
            "Re-generate a schema from the main pipeline."
        )
        return None, None
    except sqlite3.Error as e:
        st.error(f"Database error: {e}")
        return None, None


def fetch_all_runs_summary() -> list[dict]:
    """
    Return a lightweight list of all runs for the sidebar selector.
    Each dict: {id, prompt_preview, is_valid, created_at}
    """
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, prompt_preview, is_valid, created_at
            FROM generation_runs
            WHERE final_schema IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 30
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def fetch_schema_by_run_id(run_id: str) -> dict | None:
    """Fetch and parse final_schema for a specific run_id."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT final_schema FROM generation_runs WHERE id = ?",
            (run_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row and row["final_schema"]:
            return json.loads(row["final_schema"])
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
# GEMINI CLIENT
# ═══════════════════════════════════════════════════════════════════

def _get_gemini_client():
    """
    Initialise and return a google.genai client.
    Raises EnvironmentError if GEMINI_API_KEY is not set.
    """
    if not GEMINI_API_KEY:
        raise EnvironmentError(
            "GEMINI_API_KEY not found. Add it to your .env file: "
            "GEMINI_API_KEY=your_key_here"
        )
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    return client


# ═══════════════════════════════════════════════════════════════════
# SCHEMA → PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════

def build_gemini_prompt(schema: dict) -> str:
    """
    Construct the full prompt sent to Gemini.

    Extracts the most actionable parts of the schema to keep
    the prompt focused and within token budget:
      - App metadata (name, type, complexity)
      - All API endpoints (method, path, auth, roles, request_body)
      - All DB tables (name + columns)
      - All auth roles + permissions
      - All UI pages (id, route, access_roles)
      - Business rules

    Uses the app_name from the schema as the official brand name
    across all generated source code (title tags, headers, etc.).
    The system instruction enforces strict <file name="...">...</file>
    XML wrapping so our parser can extract files reliably.
    """
    meta = schema.get("meta", {})
    app_name = meta.get("app_name", "Generated App")
    app_type = meta.get("app_type", "Web Application")
    complexity = meta.get("complexity_score", 5)

    # Compact but complete schema summary
    schema_summary = json.dumps({
        "app_name": app_name,
        "app_type": app_type,
        "complexity_score": complexity,
        "api_endpoints": [
            {
                "method": ep.get("method"),
                "path": ep.get("path"),
                "auth_required": ep.get("auth_required"),
                "allowed_roles": ep.get("allowed_roles", []),
                "request_body": ep.get("request_body"),
                "response_schema": ep.get("response_schema"),
            }
            for ep in schema.get("api_schema", [])
        ],
        "database_tables": [
            {
                "table": t.get("name"),
                "columns": [
                    {"name": c.get("name"), "type": c.get("type"),
                     "nullable": c.get("nullable"), "primary_key": c.get("primary_key"),
                     "foreign_key": c.get("foreign_key")}
                    for c in t.get("columns", [])
                ],
            }
            for t in schema.get("db_schema", [])
        ],
        "auth_roles": [
            {"name": r.get("name"), "permissions": r.get("permissions")}
            for r in schema.get("auth_schema", [])
        ],
        "ui_pages": [
            {"id": p.get("id"), "route": p.get("route"),
             "access_roles": p.get("access_roles")}
            for p in schema.get("ui_schema", [])
        ],
        "business_rules": schema.get("business_rules", []),
        "assumptions": schema.get("assumptions", []),
    }, indent=2)

    prompt = f"""You are an elite full-stack web compiler.

Convert the following structural JSON application schema into complete,
production-ready source code for a {app_type} called "{app_name}".

BRAND NAME REQUIREMENT — THIS IS MANDATORY:
The official brand name of this application is "{app_name}".
Use the app_name provided in the schema as the official brand name across
ALL generated source code. Apply it universally:
  - HTML <title> tags and <h1> page headers: "{app_name}"
  - Browser tab title, meta og:title, meta description
  - package.json "name" field (lowercase slug of the brand name)
  - FastAPI app title in main.py: app = FastAPI(title="{app_name}")
  - Server startup log: e.g., "Starting {app_name} API server..."
  - README.md H1 heading and introduction paragraph
  - docker-compose.yml service names and labels
  - Login/register page branding and welcome message
Do NOT invent a different name or use generic labels like "My App",
"Web Application", or "Generated App" anywhere in the output.

SCHEMA:
{schema_summary}

GENERATION REQUIREMENTS:
1. Generate a COMPLETE frontend layer:
   - Single-page HTML file using Tailwind CSS CDN (no build step)
   - Vanilla JavaScript for all interactivity, API calls, auth
   - Separate CSS file for custom styles beyond Tailwind
   - One JS module per major feature area

2. Generate a COMPLETE backend layer:
   - Python/FastAPI application
   - One route file per entity (matching the api_endpoints above)
   - SQLAlchemy models matching every db_table above
   - JWT auth middleware matching the auth_roles
   - requirements.txt with all backend dependencies

3. Generate supporting files:
   - README.md with setup instructions
   - .env.example with all required environment variables
   - docker-compose.yml for local development

FILE NAMING CONVENTIONS:
  frontend/index.html
  frontend/styles.css
  frontend/js/auth.js
  frontend/js/api.js
  frontend/js/[feature].js  (one per UI page/entity)
  backend/main.py
  backend/routes/[entity].py  (one per db table)
  backend/models/[entity].py  (one per db table)
  backend/auth.py
  backend/database.py
  backend/requirements.txt
  README.md
  .env.example
  docker-compose.yml

DOCUMENTATION REQUIREMENT: You MUST also generate a README.md file. Wrap it in <file name="README.md"> [RAW MARKDOWN HERE] </file> just like the source code. The README must be highly professional and include the following sections:

Project Title: Use the official app_name.

Description: Write a compelling, 2-paragraph description of the application based on the schema's intent.

Architecture Overview: Briefly list the Core UI Pages, the Backend API structure, and the Database Tables generated.

Tech Stack: Explicitly state the technologies used (e.g., HTML, Tailwind CSS, Vanilla JS, Node.js/Python).

Getting Started: Provide clear, step-by-step terminal instructions on how a developer should install dependencies and run the backend server and frontend locally.

CRITICAL FORMATTING RULE — THIS IS MANDATORY:
Wrap EVERY generated file inside these exact XML tags:
<file name="path/to/filename.ext">
[COMPLETE RAW FILE CONTENT HERE]
</file>

Do NOT write any conversational prose, markdown headers, or explanatory
text OUTSIDE these XML tags. Every character of code must be inside a
<file> tag. Multiple files are allowed and expected.
Begin immediately with the first <file> tag.
"""
    return prompt


# ═══════════════════════════════════════════════════════════════════
# GEMINI API CALL
# ═══════════════════════════════════════════════════════════════════

def call_gemini(
    prompt: str,
    on_new_file=None,
) -> str:
    """
    Send prompt to Gemini 2.5 Flash and return the complete response text.

    Uses streaming (generate_content_stream) so that file-level progress
    can be reported in real time as Gemini generates output.

    Args:
        prompt      : The full code-generation prompt.
        on_new_file : Optional callable(filename: str) invoked the first time
                      each <file name="..."> tag is detected in the buffer.
                      Used by the Streamlit UI to display live synthesis logs.

    Uses google.genai client with:
      model = "gemini-2.5-flash"
      max_output_tokens = 65536
      temperature = 0.2  (low for deterministic code generation)

    Raises:
        EnvironmentError: if GEMINI_API_KEY missing
        RuntimeError: if API call fails or stream is empty
    """
    client = _get_gemini_client()

    from google.genai import types

    stream = client.models.generate_content_stream(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=65536,
            temperature=0.2,
            system_instruction=(
                "You are an elite full-stack web compiler. "
                "Output ONLY raw source code wrapped in "
                "<file name='path'> tags. Zero prose outside tags."
            ),
        ),
    )

    # ── Streaming accumulator with live file-detection ──────────────
    full_response = ""
    seen_files: set[str] = set()

    for chunk in stream:
        if chunk.text:
            full_response += chunk.text

            # Detect any new <file name="..."> tags that have appeared
            detected = re.findall(r'<file name="([^"]+)">', full_response)
            for filename in detected:
                if filename not in seen_files:
                    seen_files.add(filename)
                    if on_new_file is not None:
                        on_new_file(filename)

    if not full_response.strip():
        raise RuntimeError(
            "Gemini returned an empty response. "
            "This can happen if the schema is too large. "
            "Try regenerating with a simpler prompt."
        )

    return full_response


# ═══════════════════════════════════════════════════════════════════
# SELF-HEALING PARSER
# ═══════════════════════════════════════════════════════════════════

def parse_files_from_response(response_text: str) -> tuple[dict[str, str], list[str]]:
    """
    Extract {filename: code} dict from Gemini's raw response text.
    Returns (files_dict, warnings_list).

    Self-healing strategies applied in order:
      1. Primary: strict regex <file name="...">...</file> with DOTALL
      2. Repair: if closing tag missing (truncated response), patch it
      3. Fallback: extract fenced code blocks ```lang\\n...``` as unnamed files
      4. Emergency: return raw text as a single readme file

    warnings_list contains messages about which fallback was used.
    Never raises — always returns something usable.
    """
    files: dict[str, str] = {}
    warnings: list[str] = []

    if not response_text or not response_text.strip():
        warnings.append("Gemini returned empty text. Cannot parse any files.")
        return files, warnings

    # ── Strategy 1: Primary strict parse ───────────────────────────
    try:
        primary_matches = re.findall(
            r'<file\s+name=["\']([^"\']+)["\']\s*>(.*?)</file>',
            response_text,
            re.DOTALL | re.IGNORECASE,
        )
        if primary_matches:
            for filename, content in primary_matches:
                filename = filename.strip()
                content = content.strip()
                if filename and content:
                    files[filename] = content
            if files:
                return files, warnings
    except re.error as e:
        warnings.append(f"Primary regex failed: {e}")

    # ── Strategy 2: Repair truncated response ───────────────────────
    # Gemini sometimes cuts off before writing the final </file> tag.
    # We find all opening tags and take text until the next opening tag
    # or end of string.
    try:
        open_tag_pattern = re.compile(
            r'<file\s+name=["\']([^"\']+)["\']\s*>',
            re.IGNORECASE,
        )
        open_matches = list(open_tag_pattern.finditer(response_text))

        if open_matches:
            warnings.append(
                f"Repair mode: found {len(open_matches)} open <file> tags "
                "but missing closing tags. Patching truncated response."
            )
            for i, match in enumerate(open_matches):
                filename = match.group(1).strip()
                content_start = match.end()
                content_end = (
                    open_matches[i + 1].start()
                    if i + 1 < len(open_matches)
                    else len(response_text)
                )
                content = response_text[content_start:content_end].strip()
                # Strip any partial </file> or next <file tag remnants
                content = re.sub(r'</file\s*>?\s*$', '', content).strip()
                content = re.sub(r'<file\s+name=.*$', '', content).strip()
                if filename and content:
                    files[filename] = content

            if files:
                return files, warnings
    except Exception as e:
        warnings.append(f"Repair strategy failed: {e}")

    # ── Strategy 3: Extract markdown fenced code blocks ─────────────
    try:
        fence_matches = re.findall(
            r'```(?:\w+)?\n(.*?)```',
            response_text,
            re.DOTALL,
        )
        if fence_matches:
            warnings.append(
                f"Fallback: no <file> tags found. "
                f"Extracted {len(fence_matches)} fenced code block(s). "
                "Files named generically."
            )
            ext_map = {
                "<!doctype": "html", "<html": "html",
                "from fastapi": "py", "import express": "js",
                "version:": "yml", "services:": "yml",
            }
            for i, block_content in enumerate(fence_matches):
                block_lower = block_content.strip().lower()[:100]
                ext = "txt"
                for signature, detected_ext in ext_map.items():
                    if signature in block_lower:
                        ext = detected_ext
                        break
                filename = f"generated_file_{i + 1}.{ext}"
                files[filename] = block_content.strip()

            if files:
                return files, warnings
    except Exception as e:
        warnings.append(f"Fenced block fallback failed: {e}")

    # ── Strategy 4: Emergency — raw dump ───────────────────────────
    warnings.append(
        "Emergency fallback: could not parse any structured files. "
        "Returning raw Gemini response as raw_output.txt."
    )
    files["raw_output.txt"] = response_text
    return files, warnings


# ═══════════════════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════════

def detect_language(filename: str) -> str:
    """
    Map file extension to Streamlit st.code() language string.
    Returns "text" for unknown extensions.
    """
    ext_map = {
        ".py":      "python",
        ".js":      "javascript",
        ".ts":      "typescript",
        ".html":    "html",
        ".css":     "css",
        ".json":    "json",
        ".md":      "markdown",
        ".yml":     "yaml",
        ".yaml":    "yaml",
        ".toml":    "toml",
        ".sh":      "bash",
        ".bash":    "bash",
        ".txt":     "text",
        ".env":     "bash",
        ".sql":     "sql",
        ".dockerfile": "dockerfile",
    }
    suffix = Path(filename).suffix.lower()
    # Special cases
    name_lower = Path(filename).name.lower()
    if name_lower in ("dockerfile", ".dockerignore"):
        return "dockerfile"
    if name_lower in (".env.example", ".env"):
        return "bash"
    return ext_map.get(suffix, "text")


# ═══════════════════════════════════════════════════════════════════
# ZIP BUILDER
# ═══════════════════════════════════════════════════════════════════

def build_zip(files: dict[str, str]) -> bytes:
    """
    Pack {filename: content} dict into an in-memory ZIP archive.

    Args:
        files: Dict of {relative_path: source_code_string}

    Returns:
        bytes of the complete ZIP file (ready for st.download_button)

    Notes:
        - All paths are kept as-is (subdirectories preserved in ZIP)
        - Content encoded as UTF-8
        - ZIP uses ZIP_DEFLATED compression
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filepath, content in files.items():
            # Normalise path separators
            normalised_path = filepath.replace("\\\\", "/").lstrip("/")
            try:
                zf.writestr(normalised_path, content.encode("utf-8"))
            except Exception as e:
                # If encoding fails, write as latin-1 fallback
                try:
                    zf.writestr(normalised_path, content.encode("latin-1", errors="replace"))
                except Exception:
                    zf.writestr(
                        normalised_path,
                        f"# Encoding error for this file: {e}".encode("utf-8")
                    )
    zip_buffer.seek(0)
    return zip_buffer.read()


# ═══════════════════════════════════════════════════════════════════
# TIMESTAMPED LOG HELPER
# ═══════════════════════════════════════════════════════════════════

def ts_log(message: str) -> str:
    """Return a formatted log line with HH:MM:SS timestamp."""
    now = datetime.now().strftime("%H:%M:%S")
    return f"[{now}] {message}"


# ═══════════════════════════════════════════════════════════════════
# MAIN UI
# ═══════════════════════════════════════════════════════════════════

def render_studio():
    if st.button("← Back to Schema Compiler"):
        st.session_state.current_view = 'compiler'
        st.rerun()

    st.title("⚡ Full-Stack Studio")
    st.caption(
        "Stage 5 of the ZEMO.ai Pipeline — "
        "Schema → Production Source Code"
    )

    # ── API Key guard ───────────────────────────────────────────────
    if not GEMINI_API_KEY:
        st.error(
            "**GEMINI_API_KEY not configured.** "
            "Add `GEMINI_API_KEY=your_key` to your `.env` file and restart."
        )
        st.code("# .env\\nGEMINI_API_KEY=your_gemini_api_key_here", language="bash")
        st.stop()

    # ── Run selector in sidebar ─────────────────────────────────────
    with st.sidebar:
        st.subheader("📂 Schema Source")
        all_runs = fetch_all_runs_summary()

        if not all_runs:
            st.info("No schemas found. Generate one in the main pipeline first.")
            selected_run_id = None
        else:
            run_options = {
                f"{'✅' if r['is_valid'] else '⚠️'} {r['prompt_preview'][:45]}... "
                f"({r['created_at'][:10]})": r["id"]
                for r in all_runs
            }
            selected_label = st.selectbox(
                "Select schema run:",
                options=list(run_options.keys()),
                index=0,
            )
            selected_run_id = run_options[selected_label]

        st.divider()
        st.caption("**Stack generated by Gemini 2.5 Flash**")
        st.caption("Frontend: HTML + Tailwind + Vanilla JS")
        st.caption("Backend: Python FastAPI + SQLAlchemy")

    # ── Load schema ─────────────────────────────────────────────────
    if selected_run_id:
        schema = fetch_schema_by_run_id(selected_run_id)
    else:
        schema, _ = fetch_latest_schema()

    if schema is None:
        st.info(
            "🔍 No application schema found in compiler.db. "
            "Open the main pipeline (streamlit run app.py), "
            "enter a prompt, and generate a schema first."
        )
        st.stop()

    # Extract name, default to "App" if missing
    raw_app_name = schema.get("meta", {}).get("app_name") or schema.get("app_name", "App")

    # Sanitize: Remove special characters, replace spaces with underscores
    safe_app_name = re.sub(r'[^A-Za-z0-9]', '_', raw_app_name).strip('_')

    # Schema loaded — show app info banner
    meta = schema.get("meta", {})
    app_name = meta.get("app_name", "Unknown App")
    app_type = meta.get("app_type", "Application")
    complexity = meta.get("complexity_score", "?")

    info_col1, info_col2, info_col3, info_col4 = st.columns(4)
    info_col1.metric("📱 App", app_name)
    info_col2.metric("🗂 Type", app_type)
    info_col3.metric("📊 Complexity", f"{complexity}/10")
    info_col4.metric("🔌 Endpoints", len(schema.get("api_schema", [])))

    st.divider()

    # ── Dual-tab workspace ──────────────────────────────────────────
    tab_studio, tab_blueprint = st.tabs(
        ["🚀 Full-Stack Studio", "📦 Master JSON Blueprint"]
    )

    # ── TAB 2: Blueprint (show first so it's always accessible) ─────
    with tab_blueprint:
        st.subheader("Master JSON Blueprint")
        st.caption(
            "This is the raw AppSchema JSON that powers the code generator. "
            "Download it for inspection or to re-run generation."
        )
        schema_json_bytes = json.dumps(schema, indent=2).encode("utf-8")
        st.download_button(
            label=f"⬇️ Download {safe_app_name}_schema.json",
            data=schema_json_bytes,
            file_name=f"{safe_app_name}_schema.json",
            mime="application/json",
            use_container_width=True,
        )
        # Show stats without rendering the giant JSON string
        st.markdown("**Schema Contents:**")
        stats_c1, stats_c2, stats_c3, stats_c4, stats_c5 = st.columns(5)
        stats_c1.metric("UI Pages", len(schema.get("ui_schema", [])))
        stats_c2.metric("API Endpoints", len(schema.get("api_schema", [])))
        stats_c3.metric("DB Tables", len(schema.get("db_schema", [])))
        stats_c4.metric("Auth Roles", len(schema.get("auth_schema", [])))
        stats_c5.metric("Business Rules", len(schema.get("business_rules", [])))

        if schema.get("assumptions"):
            with st.expander(f"💡 Assumptions ({len(schema['assumptions'])})"):
                for assumption in schema["assumptions"]:
                    st.markdown(f"- {assumption}")

    # ── TAB 1: Full-Stack Studio ────────────────────────────────────
    with tab_studio:

        st.subheader(f"Generate Full-Stack Codebase for *{app_name}*")
        st.markdown(
            "Sends the schema to **Gemini 2.5 Flash** to generate a complete "
            "frontend + backend codebase. The output is parsed, packaged into "
            "a ZIP, and displayed in an interactive IDE viewer below."
        )

        # Warn if schema is large
        endpoint_count = len(schema.get("api_schema", []))
        if endpoint_count > 30:
            st.warning(
                f"⚠️ This schema has {endpoint_count} API endpoints. "
                "Generation may be slower and Gemini may truncate output. "
                "The self-healing parser will handle partial responses."
            )

        compile_btn = st.button(
            "🔨 Compile Full-Stack Codebase",
            type="primary",
            use_container_width=True,
            disabled=not bool(schema),
        )

        # Retry button (shown after failure)
        if st.session_state.get("show_retry"):
            retry_btn = st.button(
                "🔁 Retry Generation",
                use_container_width=True,
            )
            if retry_btn:
                st.session_state.pop("show_retry", None)
                st.session_state.pop("generated_files", None)
                st.session_state.pop("zip_bytes", None)
                st.rerun()

        # ── Generation pipeline ─────────────────────────────────────
        if compile_btn:
            st.session_state.pop("generated_files", None)
            st.session_state.pop("zip_bytes", None)
            st.session_state.pop("show_retry", None)
            st.session_state.pop("parse_warnings", None)

            with st.status(
                "Initializing Full-Stack Compiler...",
                expanded=True
            ) as status_box:

                # Stage A: Schema extraction
                st.write(ts_log("Extracting compiled schema from compiler.db..."))
                time.sleep(0.3)

                api_count = len(schema.get("api_schema", []))
                db_count = len(schema.get("db_schema", []))
                ui_count = len(schema.get("ui_schema", []))
                st.write(
                    ts_log(
                        f"Mapping {api_count} API endpoints, "
                        f"{db_count} database tables, "
                        f"{ui_count} UI pages..."
                    )
                )
                time.sleep(0.2)

                # Stage B: Build prompt
                st.write(ts_log("Building generation prompt from schema..."))
                try:
                    prompt = build_gemini_prompt(schema)
                    st.write(
                        ts_log(
                            f"Prompt constructed: {len(prompt):,} characters. "
                            f"Estimated tokens: ~{len(prompt)//4:,}"
                        )
                    )
                except Exception as e:
                    status_box.update(
                        label="Prompt construction failed",
                        state="error"
                    )
                    st.error(f"Could not build prompt: {e}")
                    st.session_state["show_retry"] = True
                    st.stop()

                # Stage C: Gemini API call (streaming with live file detection)
                st.write(
                    ts_log(
                        "Orchestrating Gemini 2.5 Flash synthesis engine... "
                        "(streaming live — files will appear below as they are generated)"
                    )
                )
                gemini_start = time.perf_counter()

                def _on_new_file(filename: str) -> None:
                    """Called each time a new <file> tag is detected in the stream."""
                    st.write(ts_log(f"⏳ Synthesizing {filename}..."))

                try:
                    raw_response = call_gemini(prompt, on_new_file=_on_new_file)
                    gemini_latency = (time.perf_counter() - gemini_start) * 1000
                    st.write(
                        ts_log(
                            f"✅ Stream complete: {len(raw_response):,} characters "
                            f"in {gemini_latency/1000:.1f}s"
                        )
                    )
                except EnvironmentError as e:
                    status_box.update(label="API Key Error", state="error")
                    st.error(str(e))
                    st.session_state["show_retry"] = True
                    st.stop()
                except Exception as e:
                    status_box.update(
                        label="Gemini API call failed",
                        state="error"
                    )
                    st.error(
                        f"Gemini API error: {e}. "
                        "Check your API key and quota, then retry."
                    )
                    st.session_state["show_retry"] = True
                    st.stop()

                # Stage D: Self-healing parse
                st.write(ts_log("Parsing generated code artifacts..."))
                files, parse_warnings = parse_files_from_response(raw_response)

                if parse_warnings:
                    for warn in parse_warnings:
                        st.write(ts_log(f"⚠️ PARSER: {warn}"))
                    st.session_state["parse_warnings"] = parse_warnings

                if not files:
                    status_box.update(
                        label="Parsing failed — no files extracted",
                        state="error"
                    )
                    st.error(
                        "The self-healing parser could not extract any files "
                        "from Gemini's response. This indicates a severe "
                        "formatting anomaly. Raw response saved for inspection."
                    )
                    st.text_area(
                        "Raw Gemini Response (first 2000 chars):",
                        value=raw_response[:2000],
                        height=300,
                    )
                    st.session_state["show_retry"] = True
                    st.stop()

                st.write(
                    ts_log(
                        f"Successfully extracted {len(files)} file(s): "
                        f"{', '.join(list(files.keys())[:5])}"
                        f"{'...' if len(files) > 5 else ''}"
                    )
                )

                # Stage E: ZIP packaging
                st.write(
                    ts_log(
                        "Compiling full-stack architecture into "
                        "deployment archive..."
                    )
                )
                try:
                    zip_bytes = build_zip(files)
                    zip_kb = len(zip_bytes) / 1024
                    st.write(
                        ts_log(
                            f"ZIP archive created: {zip_kb:.1f} KB, "
                            f"{len(files)} files"
                        )
                    )
                except Exception as e:
                    status_box.update(
                        label="ZIP creation failed",
                        state="error"
                    )
                    st.error(f"Could not create ZIP archive: {e}")
                    st.session_state["show_retry"] = True
                    st.stop()

                # Success
                status_box.update(
                    label="✅ Codebase Compiled Successfully!",
                    state="complete",
                    expanded=False,
                )

                # Persist to session state
                st.session_state["generated_files"] = files
                st.session_state["zip_bytes"] = zip_bytes

        # ── IDE Dashboard (shown after successful generation) ────────
        if st.session_state.get("generated_files") and st.session_state.get("zip_bytes"):

            generated_files: dict[str, str] = st.session_state["generated_files"]
            zip_bytes: bytes = st.session_state["zip_bytes"]
            parse_warnings: list[str] = st.session_state.get("parse_warnings", [])

            st.divider()

            # Parse warnings banner
            if parse_warnings:
                with st.expander(
                    f"⚠️ Parser used fallback strategies ({len(parse_warnings)} warning(s))",
                    expanded=False
                ):
                    for w in parse_warnings:
                        st.warning(w)

            # Download + stats row
            dl_col, stat1, stat2, stat3 = st.columns([2, 1, 1, 1])
            with dl_col:
                st.download_button(
                    label=f"⬇️ Download {safe_app_name}.zip",
                    data=zip_bytes,
                    file_name=f"{safe_app_name}.zip",
                    mime="application/zip",
                    use_container_width=True,
                    type="primary",
                )
            stat1.metric("📄 Files Generated", len(generated_files))
            stat2.metric(
                "📦 Archive Size",
                f"{len(zip_bytes)/1024:.1f} KB"
            )
            stat3.metric(
                "🌐 Frontend Files",
                len([f for f in generated_files if f.startswith("frontend/")])
            )

            st.divider()

            # ── Split-screen IDE ─────────────────────────────────────
            st.markdown("### 🖥 Interactive Code Viewer")

            # Sort files: frontend first, then backend, then others
            def sort_key(fname):
                if fname.startswith("frontend/"):
                    return (0, fname)
                elif fname.startswith("backend/"):
                    return (1, fname)
                else:
                    return (2, fname)

            sorted_filenames = sorted(generated_files.keys(), key=sort_key)

            nav_col, code_col = st.columns([1, 3])

            with nav_col:
                st.markdown(
                    '<div class="ide-header">📁 Workspace Directory</div>',
                    unsafe_allow_html=True,
                )
                selected_file = st.radio(
                    label="Workspace Directory",
                    options=sorted_filenames,
                    label_visibility="collapsed",
                    key="file_navigator",
                )

            with code_col:
                if selected_file:
                    detected_lang = detect_language(selected_file)
                    file_content = generated_files[selected_file]
                    line_count = len(file_content.splitlines())

                    st.markdown(
                        f'<div class="ide-header">'
                        f'<span class="file-badge">{selected_file}</span>'
                        f'&nbsp;&nbsp;{detected_lang.upper()} · {line_count} lines'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    # Per-file download
                    file_dl_col, _ = st.columns([1, 3])
                    with file_dl_col:
                        st.download_button(
                            label=f"⬇️ {Path(selected_file).name}",
                            data=file_content.encode("utf-8"),
                            file_name=Path(selected_file).name,
                            mime="text/plain",
                            key=f"dl_{selected_file}",
                        )

                    st.code(
                        file_content,
                        language=detected_lang,
                        line_numbers=True,
                    )

        elif not compile_btn:
            # Pre-generation placeholder
            st.info(
                "👆 Click **Compile Full-Stack Codebase** to begin generation. "
                f"The pipeline will generate a complete {app_type} codebase "
                f"from your **{app_name}** schema."
            )


if __name__ == "__main__":
    render_studio()
