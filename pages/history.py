# pages/history.py
"""
pages/history.py

Schema History & Diff Viewer.
Accessible at http://localhost:8501/history via Streamlit multi-page routing.

Features:
  - List all stored generation_runs (newest first)
  - Select any two runs to diff
  - Side-by-side JSON diff per schema section
  - Highlight added / removed / changed keys
  - Export diff report as Markdown
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

import streamlit as st

# ── Page config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="Schema History",
    page_icon="🕓",
    layout="wide",
)

DATABASE_PATH = "compiler.db"

SCHEMA_SECTIONS = [
    "meta",
    "ui_schema",
    "api_schema",
    "db_schema",
    "auth_schema",
    "business_rules",
    "assumptions",
    "warnings",
]


# ═══════════════════════════════════════════════════════════════════
# DB helpers (sqlite3 only)
# ═══════════════════════════════════════════════════════════════════

def _conn() -> sqlite3.Connection:
    if not Path(DATABASE_PATH).exists():
        st.error("compiler.db not found. Generate a schema first.")
        st.stop()
    c = sqlite3.connect(DATABASE_PATH)
    c.row_factory = sqlite3.Row
    return c


def load_all_runs() -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, prompt_preview, is_valid, created_at
        FROM generation_runs
        WHERE final_schema IS NOT NULL AND final_schema != ''
        ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def load_schema(run_id: str) -> dict:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT final_schema, prompt FROM generation_runs WHERE id = ?",
        (run_id,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["final_schema"]), row["prompt"]
    except json.JSONDecodeError:
        return {}, ""


# ═══════════════════════════════════════════════════════════════════
# Diff engine
# ═══════════════════════════════════════════════════════════════════

def _normalise(value) -> str:
    """Stable JSON string for comparison."""
    return json.dumps(value, sort_keys=True, indent=2)


def diff_section(a_val, b_val) -> tuple[str, str, str]:
    """
    Compare two section values.
    Returns (status, a_text, b_text) where status is one of:
      'identical' | 'changed' | 'added' | 'removed'
    """
    a_text = _normalise(a_val) if a_val is not None else "(absent)"
    b_text = _normalise(b_val) if b_val is not None else "(absent)"

    if a_val is None and b_val is not None:
        return "added", a_text, b_text
    if a_val is not None and b_val is None:
        return "removed", a_text, b_text
    if a_text == b_text:
        return "identical", a_text, b_text
    return "changed", a_text, b_text


STATUS_COLOURS = {
    "identical": "🟢",
    "changed":   "🟡",
    "added":     "🔵",
    "removed":   "🔴",
}


# ═══════════════════════════════════════════════════════════════════
# Markdown report builder
# ═══════════════════════════════════════════════════════════════════

def build_diff_report(
    run_a: dict,
    run_b: dict,
    schema_a: dict,
    schema_b: dict,
) -> str:
    lines = [
        "# Schema Diff Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Run A",
        f"- ID: `{run_a['id']}`",
        f"- Created: {run_a['created_at']}",
        f"- Valid: {'Yes' if run_a['is_valid'] else 'No'}",
        f"- Preview: {run_a['prompt_preview']}",
        "",
        "## Run B",
        f"- ID: `{run_b['id']}`",
        f"- Created: {run_b['created_at']}",
        f"- Valid: {'Yes' if run_b['is_valid'] else 'No'}",
        f"- Preview: {run_b['prompt_preview']}",
        "",
        "## Section Diffs",
        "",
    ]

    for section in SCHEMA_SECTIONS:
        a_val = schema_a.get(section)
        b_val = schema_b.get(section)
        status, a_text, b_text = diff_section(a_val, b_val)
        emoji = STATUS_COLOURS[status]

        lines.append(f"### {emoji} {section}  ({status})")
        if status != "identical":
            lines.append("**Run A:**")
            lines.append(f"```json\n{a_text}\n```")
            lines.append("**Run B:**")
            lines.append(f"```json\n{b_text}\n```")
        else:
            lines.append("_No changes in this section._")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# MAIN UI
# ═══════════════════════════════════════════════════════════════════

def main():
    st.title("🕓 Schema History & Diff Viewer")
    st.caption(
        "Compare any two stored generation runs section-by-section. "
        "Identify what changed between prompt iterations."
    )

    runs = load_all_runs()

    if len(runs) == 0:
        st.info("No schemas stored yet. Generate at least one schema from the main pipeline.")
        return

    if len(runs) == 1:
        st.warning("Only one schema stored — need at least two to diff.")
        # Still show the single run for inspection
        run = runs[0]
        schema, prompt = load_schema(run["id"])
        st.subheader("Single stored schema")
        st.json(schema)
        return

    # ── Run selectors ────────────────────────────────────────────────
    def run_label(r: dict) -> str:
        valid = "✅" if r["is_valid"] else "⚠️"
        date  = r["created_at"][:16]
        prev  = r["prompt_preview"][:50]
        return f"{valid}  {date}  —  {prev}…"

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### 🅐 Run A  (baseline)")
        labels = [run_label(r) for r in runs]
        idx_a  = st.selectbox("Select Run A:", range(len(runs)),
                              format_func=lambda i: labels[i], key="sel_a")
        run_a  = runs[idx_a]

    with col_b:
        st.markdown("#### 🅑 Run B  (compare)")
        # Default to second-most-recent
        default_b = 1 if len(runs) > 1 else 0
        idx_b = st.selectbox("Select Run B:", range(len(runs)),
                             format_func=lambda i: labels[i],
                             index=default_b, key="sel_b")
        run_b = runs[idx_b]

    if run_a["id"] == run_b["id"]:
        st.warning("Run A and Run B are the same. Select different runs to compare.")
        return

    st.divider()

    # ── Load schemas ─────────────────────────────────────────────────
    schema_a, prompt_a = load_schema(run_a["id"])
    schema_b, prompt_b = load_schema(run_b["id"])

    # ── Prompt diff ──────────────────────────────────────────────────
    with st.expander("📝 Prompt comparison", expanded=False):
        pc1, pc2 = st.columns(2)
        with pc1:
            st.markdown("**Run A prompt:**")
            st.text(prompt_a or "(not stored)")
        with pc2:
            st.markdown("**Run B prompt:**")
            st.text(prompt_b or "(not stored)")

    # ── Section-level summary ────────────────────────────────────────
    st.subheader("Section Change Summary")

    summary_data = []
    for section in SCHEMA_SECTIONS:
        a_val = schema_a.get(section)
        b_val = schema_b.get(section)
        status, _, _ = diff_section(a_val, b_val)
        emoji = STATUS_COLOURS[status]

        # Count items if list
        def _count(v):
            if isinstance(v, list): return len(v)
            if isinstance(v, dict): return len(v)
            return "—"

        summary_data.append({
            "Section": f"{emoji} {section}",
            "Status":  status,
            "Run A items": _count(a_val),
            "Run B items": _count(b_val),
        })

    st.dataframe(summary_data, use_container_width=True, hide_index=True)

    # ── Detailed section diffs ───────────────────────────────────────
    st.divider()
    st.subheader("Detailed Section Diffs")

    show_identical = st.checkbox("Show identical sections", value=False)

    for section in SCHEMA_SECTIONS:
        a_val  = schema_a.get(section)
        b_val  = schema_b.get(section)
        status, a_text, b_text = diff_section(a_val, b_val)
        emoji  = STATUS_COLOURS[status]

        if status == "identical" and not show_identical:
            continue

        with st.expander(f"{emoji}  **{section}**  — {status}", expanded=(status != "identical")):
            if status == "identical":
                st.success("No changes in this section.")
                st.code(a_text, language="json")
            else:
                d1, d2 = st.columns(2)
                with d1:
                    st.markdown("**🅐 Run A:**")
                    st.code(a_text, language="json")
                with d2:
                    st.markdown("**🅑 Run B:**")
                    st.code(b_text, language="json")

    # ── Export diff report ───────────────────────────────────────────
    st.divider()
    report_md = build_diff_report(run_a, run_b, schema_a, schema_b)
    st.download_button(
        label="⬇️ Download Diff Report (Markdown)",
        data=report_md.encode("utf-8"),
        file_name=f"schema_diff_{run_a['id'][:8]}_vs_{run_b['id'][:8]}.md",
        mime="text/markdown",
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
