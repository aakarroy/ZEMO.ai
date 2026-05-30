# health_check.py
"""
health_check.py

Pre-flight diagnostic for the App Compiler pipeline.
Run with:  python health_check.py

Checks (in order):
  1.  Python version >= 3.10
  2.  .env file present and readable
  3.  GEMINI_API_KEY set and non-empty
  4.  All required packages importable
  5.  compiler.db exists
  6.  generation_runs table exists with correct columns
  7.  At least one run with a non-null final_schema exists
  8.  At least one VALID run (is_valid=1) exists  [warn-only]
  9.  google.genai.Client instantiation succeeds (no live call)

Exit codes:
  0  all hard checks passed  (warnings allowed)
  1  one or more hard checks failed
"""

import sys
import os
import sqlite3
import importlib
from pathlib import Path

# ── Colour helpers (stdlib only) ────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _c(colour, text): return f"{colour}{text}{RESET}"
def green(s):  return _c(GREEN,  s)
def red(s):    return _c(RED,    s)
def yellow(s): return _c(YELLOW, s)
def bold(s):   return _c(BOLD,   s)

PASS_LABEL = green("  PASS")
FAIL_LABEL = red("  FAIL")
WARN_LABEL = yellow("  WARN")

failures: list[str] = []
warnings: list[str] = []


def check(label: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
    symbol = PASS_LABEL if ok else (WARN_LABEL if warn_only else FAIL_LABEL)
    suffix = f"  →  {detail}" if detail else ""
    print(f"{symbol}  {label}{suffix}")
    if not ok:
        (warnings if warn_only else failures).append(f"{label}: {detail}")
    return ok


def section(title: str):
    print(f"\n{bold(title)}")
    print("─" * 52)


# ── Load .env early so all subsequent checks see the vars ───────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    _dotenv_available = True
except ImportError:
    _dotenv_available = False


# ═══════════════════════════════════════════════════════════════════
# CHECK 1 — Python version
# ═══════════════════════════════════════════════════════════════════
section("1. Runtime")
major, minor = sys.version_info[:2]
check(
    f"Python >= 3.10  (detected {major}.{minor})",
    ok=(major, minor) >= (3, 10),
    detail="" if (major, minor) >= (3, 10) else "Upgrade to Python 3.10+",
)


# ═══════════════════════════════════════════════════════════════════
# CHECK 2 — .env file
# ═══════════════════════════════════════════════════════════════════
section("2. Environment File")
env_path = Path(".env")
check(
    ".env file present",
    ok=env_path.exists(),
    detail=str(env_path.resolve()) if env_path.exists() else
           "Create .env from .env.example and populate keys",
)
check(
    "python-dotenv importable",
    ok=_dotenv_available,
    detail="" if _dotenv_available else "pip install python-dotenv",
)


# ═══════════════════════════════════════════════════════════════════
# CHECK 3 — GEMINI_API_KEY
# ═══════════════════════════════════════════════════════════════════
section("3. Gemini API Key")
gemini_key = os.getenv("GEMINI_API_KEY", "")
_gk_set = bool(gemini_key)
check(
    "GEMINI_API_KEY is set",
    ok=_gk_set,
    detail="Add GEMINI_API_KEY=AIza... to .env" if not _gk_set else
           f"prefix={gemini_key[:8]}…",
)


# ═══════════════════════════════════════════════════════════════════
# CHECK 4 — Required packages
# ═══════════════════════════════════════════════════════════════════
section("4. Required Packages")

REQUIRED_PACKAGES = [
    ("streamlit",          "streamlit"),
    ("google.genai",       "google-genai==0.8.0"),
    ("sqlalchemy",         "sqlalchemy"),
    ("pydantic",           "pydantic"),
    ("dotenv",             "python-dotenv"),
    ("alembic",            "alembic"),
    ("httpx",              "httpx"),
]

for module_name, install_hint in REQUIRED_PACKAGES:
    try:
        importlib.import_module(module_name)
        check(f"{module_name} importable", ok=True)
    except ImportError:
        check(
            f"{module_name} importable",
            ok=False,
            detail=f"pip install {install_hint}",
        )


# ═══════════════════════════════════════════════════════════════════
# CHECK 5 — compiler.db exists
# ═══════════════════════════════════════════════════════════════════
section("5. Database File")
db_path = Path("compiler.db")
db_exists = db_path.exists()
check(
    f"compiler.db present  ({db_path.resolve()})",
    ok=db_exists,
    detail="Run 'streamlit run app.py' and generate at least one schema"
           if not db_exists else f"{db_path.stat().st_size / 1024:.1f} KB",
)


# ═══════════════════════════════════════════════════════════════════
# CHECK 6 — generation_runs table & columns
# ═══════════════════════════════════════════════════════════════════
section("6. Database Schema")

REQUIRED_COLUMNS = {
    "id", "prompt", "prompt_preview", "final_schema",
    "is_valid", "created_at",
}

if db_exists:
    try:
        conn = sqlite3.connect(str(db_path))
        cur  = conn.cursor()

        # Table exists?
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='generation_runs'"
        )
        table_exists = cur.fetchone() is not None
        check("generation_runs table exists", ok=table_exists,
              detail="Run the main pipeline once to initialise the DB"
                     if not table_exists else "")

        if table_exists:
            cur.execute("PRAGMA table_info(generation_runs)")
            actual_cols = {row[1] for row in cur.fetchall()}
            missing = REQUIRED_COLUMNS - actual_cols
            check(
                f"All required columns present  ({', '.join(sorted(REQUIRED_COLUMNS))})",
                ok=not missing,
                detail=f"Missing columns: {', '.join(sorted(missing))}"
                       if missing else "",
            )

        conn.close()
    except sqlite3.Error as e:
        check("DB schema inspection", ok=False, detail=str(e))
else:
    print(f"{WARN_LABEL}  Skipping DB schema check (compiler.db absent)")


# ═══════════════════════════════════════════════════════════════════
# CHECK 7 — At least one run with a schema
# ═══════════════════════════════════════════════════════════════════
section("7. Stored Schemas")

total_with_schema = 0
valid_runs        = 0

if db_exists:
    try:
        conn = sqlite3.connect(str(db_path))
        cur  = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) FROM generation_runs "
            "WHERE final_schema IS NOT NULL AND final_schema != ''"
        )
        total_with_schema = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM generation_runs "
            "WHERE is_valid = 1 AND final_schema IS NOT NULL"
        )
        valid_runs = cur.fetchone()[0]

        conn.close()
    except sqlite3.Error:
        pass

check(
    f"At least one run with a schema  (found {total_with_schema})",
    ok=total_with_schema > 0,
    detail="Generate a schema from the main pipeline first"
           if total_with_schema == 0 else "",
)


# ═══════════════════════════════════════════════════════════════════
# CHECK 8 — At least one VALID run  (warn-only)
# ═══════════════════════════════════════════════════════════════════
check(
    f"At least one VALID run  (is_valid=1, found {valid_runs})",
    ok=valid_runs > 0,
    detail="build_frontend.py will fall back to any schema — "
           "re-run a schema through the validator for a valid run",
    warn_only=True,
)


# ═══════════════════════════════════════════════════════════════════
# CHECK 9 — google.genai client instantiation
# ═══════════════════════════════════════════════════════════════════
section("9. Gemini Client")
if _gk_set:
    try:
        from google import genai as _genai
        _client = _genai.Client(api_key=gemini_key)
        check("google.genai.Client instantiated  (no live call)", ok=True)
    except Exception as e:
        check(
            "google.genai.Client instantiated",
            ok=False,
            detail=str(e),
        )
else:
    print(f"{WARN_LABEL}  Skipping Gemini client check (key not set)")


# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "═" * 52)
if failures:
    print(red(f"  {len(failures)} HARD FAILURE(S) — fix before running the pipeline:"))
    for f in failures:
        print(f"    • {f}")
    if warnings:
        print(yellow(f"\n  {len(warnings)} warning(s):"))
        for w in warnings:
            print(f"    • {w}")
    sys.exit(1)
else:
    print(green("  All hard checks PASSED ✓"))
    if warnings:
        print(yellow(f"  {len(warnings)} non-critical warning(s):"))
        for w in warnings:
            print(f"    • {w}")
    print()
    print("  Ready to launch:")
    print("    streamlit run app.py            # Main pipeline  (port 8501)")
    print("    streamlit run build_frontend.py # Full-Stack Studio (port 8502)")
    sys.exit(0)
