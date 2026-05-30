import streamlit as st



import json
import time
from database.db import init_db, save_run, get_recent_runs, get_run_schema
from pipeline.orchestrator import PipelineOrchestrator

init_db()



def render_compiler():
    st.markdown("""
    <style>
        .stage-complete { color: #22c55e; font-weight: bold; }
        .stage-running  { color: #f59e0b; font-weight: bold; }
        .stage-failed   { color: #ef4444; font-weight: bold; }
        .stage-pending  { color: #94a3b8; }
        .metric-box {
            background: #1e293b;
            padding: 1rem;
            border-radius: 8px;
            border-left: 4px solid #6366f1;
        }
        .assumption-tag {
            background: #854d0e;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.85rem;
        }
    </style>
    """, unsafe_allow_html=True)


    with st.sidebar:
        st.title("🔧 ZEMO.ai")
        st.caption("Natural language → App schema")
        st.divider()

        st.subheader("📚 Recent Runs")
        recent_runs = get_recent_runs(10)
        if recent_runs:
            for run in recent_runs:
                status_icon = "✅" if run["is_valid"] else "⚠️"
                label = f"{status_icon} {run['prompt_preview'][:40]}..."
                if st.button(label, key=f"run_{run['id']}", use_container_width=True):
                    st.session_state["load_run_id"] = run["id"]
        else:
            st.caption("No runs yet. Generate your first app!")

        st.divider()
        st.subheader("📊 Evaluation")
        if st.button("Run Quick Eval (3 prompts)", use_container_width=True):
            st.session_state["run_eval"] = True

        st.divider()
        st.caption("Built with Gemini 2.5 Flash + Streamlit")


    if "load_run_id" in st.session_state:
        loaded_schema = get_run_schema(st.session_state["load_run_id"])
        if loaded_schema:
            st.session_state["last_result_schema"] = loaded_schema
        del st.session_state["load_run_id"]


    st.title("🔧 ZEMO.ai")
    st.markdown(
        "Describe your app in plain English. The system will generate a "
        "complete **UI → API → Database → Auth** schema using a "
        "multi-stage AI pipeline."
    )

    EXAMPLES = [
        "Build a CRM with login, contacts, dashboard, and role-based access for admin and users.",
        "Create a task management app with teams, projects, tasks, due dates, and comments.",
        "Build an e-commerce store with products, cart, checkout, and order tracking.",
        "Create a blog platform with posts, comments, tags, and author profiles.",
    ]

    st.markdown("**Quick examples:**")
    example_cols = st.columns(4)
    for i, (col, example) in enumerate(zip(example_cols, EXAMPLES)):
        if col.button(f"Example {i+1}", key=f"ex_{i}", use_container_width=True):
            st.session_state["prompt_value"] = example

    prompt = st.text_area(
        "Describe your app",
        value=st.session_state.get("prompt_value", ""),
        height=120,
        placeholder=(
            "Build a CRM with login, contacts, dashboard, role-based access, "
            "and premium plan with Stripe payments. Admins can see analytics."
        ),
        help="Be as specific or vague as you like. The system handles ambiguity."
    )

    generate_col, clear_col = st.columns([4, 1])
    with generate_col:
        generate_clicked = st.button(
            "🚀 Generate App Schema",
            type="primary",
            use_container_width=True,
            disabled=not prompt.strip()
        )
    with clear_col:
        if st.button("Clear", use_container_width=True):
            st.session_state["prompt_value"] = ""
            st.session_state.pop("last_result_schema", None)
            st.session_state.pop("last_result_meta", None)
            st.rerun()

    if generate_clicked and prompt.strip():
        st.divider()
        st.subheader("⚙️ Pipeline Progress")

        stage_labels = {
            "intent_extraction": "Stage 1 — Intent Extraction",
            "system_design":     "Stage 2 — System Design",
            "schema_generation": "Stage 3 — Schema Generation",
            "refinement":        "Stage 4 — Refinement",
            "validation":        "Stage 5 — Validation & Repair",
        }

        stage_placeholders = {}
        for key, label in stage_labels.items():
            stage_placeholders[key] = st.empty()
            stage_placeholders[key].markdown(f"⏳ `{label}` — *pending*")

        progress_bar = st.progress(0)
        status_text  = st.empty()
        stage_count  = [0]

        def progress_callback(stage_name: str, status: str, detail: str = ""):
            icons = {
                "running": "🔄", "complete": "✅",
                "failed":  "❌", "warning":  "⚠️", "pending": "⏳"
            }
            icon       = icons.get(status, "⏳")
            label      = stage_labels.get(stage_name, stage_name)
            detail_str = f" — *{detail}*" if detail else ""
            stage_placeholders[stage_name].markdown(f"{icon} `{label}`{detail_str}")
            if status in ("complete", "warning", "failed"):
                stage_count[0] += 1
                progress_bar.progress(min(stage_count[0] / 5, 1.0))
                status_text.text(f"Completed {stage_count[0]}/5 stages...")

        with st.spinner("Generating app schema..."):
            try:
                orchestrator = PipelineOrchestrator()
                result = orchestrator.generate(
                    prompt.strip(),
                    progress_callback=progress_callback
                )

                save_run(result)

                progress_bar.progress(1.0)
                status_text.empty()

                if result.final_schema:
                    st.session_state["last_result_schema"] = result.final_schema.model_dump()
                    st.session_state["last_result_meta"] = {
                        "validation_passed":  result.validation_passed,
                        "repair_attempts":    result.repair_attempts,
                        "total_latency_ms":   result.total_latency_ms,
                        "total_tokens_used":  result.total_tokens_used,
                        "estimated_cost_usd": result.estimated_cost_usd,
                        "stages": {
                            k: v.model_dump() for k, v in result.stages.items()
                        }
                    }
                    st.success("✅ Schema generated successfully!")
                else:
                    st.error("❌ Generation failed. Check the stage statuses above.")

            except Exception as e:
                st.error(f"❌ Pipeline error: {str(e)}")
                st.exception(e)


    if "last_result_schema" in st.session_state:
        # Display the transition button at the top of the results section
        # so it's always available once a schema exists.
        if st.button("🚀 Schema Complete! Proceed to Full-Stack Studio", type="primary"):
            st.session_state.current_view = "studio"
            st.rerun()
        schema = st.session_state["last_result_schema"]
        meta   = st.session_state.get("last_result_meta", {})

        st.divider()
    
        st.subheader("📊 Generation Metrics")
        m1, m2, m3, m4, m5, m6 = st.columns(6)

        with m1: st.metric("⏱ Latency", f"{meta.get('total_latency_ms', 0)/1000:.1f}s")
        with m2: st.metric("🔧 Repairs", meta.get("repair_attempts", 0))
        with m3: st.metric("🪙 Tokens", f"{meta.get('total_tokens_used', 0):,}")
        with m4: st.metric("💰 Est. Cost", f"${meta.get('estimated_cost_usd', 0):.4f}")
        with m5:
            validation_icon = "✅" if meta.get("validation_passed") else "⚠️"
            st.metric("🔍 Validation", validation_icon)
        with m6:
            st.metric("📄 Pages", len(schema.get("ui_schema", [])))
        
        assumptions = schema.get("assumptions", [])
        warnings    = schema.get("warnings", [])

        if assumptions or warnings:
            with st.expander(f"💡 Assumptions ({len(assumptions)}) & Warnings ({len(warnings)})", expanded=False):
                if assumptions:
                    st.markdown("**Assumptions made to fill gaps in your prompt:**")
                    for a in assumptions:
                        st.markdown(f"- 🟡 {a}")
                if warnings:
                    st.markdown("**Warnings:**")
                    for w in warnings:
                        st.markdown(f"- 🟠 {w}")

        app_meta = schema.get("meta", {})
        st.markdown(
            f"**App:** `{app_meta.get('app_name', 'Unknown')}` | "
            f"**Type:** `{app_meta.get('app_type', 'Unknown')}` | "
            f"**Complexity:** `{app_meta.get('complexity_score', '?')}/10`"
        )

        st.subheader("📋 Generated Schemas")

        tab_ui, tab_api, tab_db, tab_auth, tab_rules, tab_full = st.tabs([
            f"🖥 UI Schema ({len(schema.get('ui_schema', []))} pages)",
            f"🔌 API Schema ({len(schema.get('api_schema', []))} endpoints)",
            f"🗄 DB Schema ({len(schema.get('db_schema', []))} tables)",
            f"🔐 Auth Schema ({len(schema.get('auth_schema', []))} roles)",
            f"⚙ Business Rules ({len(schema.get('business_rules', []))} rules)",
            "📦 Full JSON",
        ])
    
        with tab_ui:
            ui_pages = schema.get("ui_schema", [])
            if ui_pages:
                for page in ui_pages:
                    with st.expander(f"📄 {page.get('title', 'Page')} — `{page.get('route', '/')}`"):
                        access = ", ".join(page.get("access_roles", []))
                        st.caption(f"Access: {access} | Layout: {page.get('layout', 'default')}")
                        components = page.get("components", [])
                        st.write(f"**{len(components)} components:**")
                        for comp in components:
                            ds = (f" → `{comp.get('data_source')}`" if comp.get("data_source") else "")
                            st.markdown(f"- `{comp.get('type')}` — {comp.get('id')}{ds}")
            else:
                st.info("No UI schema generated.")

        with tab_api:
            api_endpoints = schema.get("api_schema", [])
            if api_endpoints:
                METHOD_COLORS = {
                    "GET": "🟢", "POST": "🔵", "PUT": "🟡",
                    "PATCH": "🟠", "DELETE": "🔴"
                }
                for ep in api_endpoints:
                    method = ep.get("method", "GET")
                    icon   = METHOD_COLORS.get(method, "⚪")
                    with st.expander(f"{icon} {method} `{ep.get('path')}`"):
                        roles = ", ".join(ep.get("allowed_roles", []))
                        st.caption(f"Auth: {'Required' if ep.get('auth_required') else 'Public'} | Roles: {roles}")
                        if ep.get("request_body"):
                            st.markdown("**Request body:**")
                            st.json(ep["request_body"])
                        st.markdown("**Response schema:**")
                        st.json(ep.get("response_schema", {}))
                        if ep.get("validation_rules"):
                            st.markdown("**Validation:**")
                            for rule in ep["validation_rules"]:
                                st.markdown(f"- {rule}")
            else:
                st.info("No API schema generated.")

        with tab_db:
            import pandas as pd
            db_tables = schema.get("db_schema", [])
            if db_tables:
                for table in db_tables:
                    with st.expander(f"🗄 `{table.get('name')}`"):
                        cols = table.get("columns", [])
                        st.write(f"**{len(cols)} columns:**")

                        col_data = []
                        for col in cols:
                            flags = []
                            if col.get("primary_key"):   flags.append("PK")
                            if col.get("unique"):        flags.append("UNIQUE")
                            if not col.get("nullable", True):
                                flags.append("NOT NULL")
                            if col.get("foreign_key"):
                                flags.append(f"FK→{col['foreign_key']}")
                            col_data.append({
                                "Column":      col.get("name"),
                                "Type":        col.get("type"),
                                "Constraints": " | ".join(flags)
                            })
                        st.dataframe(
                            pd.DataFrame(col_data),
                            use_container_width=True,
                            hide_index=True
                        )

                        indexes = table.get("indexes", [])
                        if indexes:
                            st.caption(f"Indexes: {', '.join(indexes)}")
            else:
                st.info("No DB schema generated.")

        with tab_auth:
            auth_roles = schema.get("auth_schema", [])
            if auth_roles:
                for role in auth_roles:
                    with st.expander(f"🔐 Role: `{role.get('name')}`"):
                        inherits = role.get("inherits_from")
                        if inherits:
                            st.caption(f"Inherits from: {inherits}")
                        permissions = role.get("permissions", [])
                        if "*" in permissions:
                            st.success("Full access (admin)")
                        else:
                            for perm in permissions:
                                st.markdown(f"- `{perm}`")
            else:
                st.info("No auth schema generated.")

        with tab_rules:
            rules = schema.get("business_rules", [])
            if rules:
                for rule in rules:
                    with st.expander(f"⚙ {rule.get('name')}"):
                        st.markdown(f"**Condition:** `{rule.get('condition')}`")
                        st.markdown(f"**Action:** `{rule.get('action')}`")
                        affected = rule.get("affected_components", [])
                        if affected:
                            st.caption(f"Affects: {', '.join(affected)}")
            else:
                st.info("No business rules generated.")

        with tab_full:
            schema_json = json.dumps(schema, indent=2)
            prompt_hash = schema.get("meta", {}).get("prompt_hash", "unknown")
            st.download_button(
                label="⬇️ Download Full Schema JSON",
                data=schema_json,
                file_name=f"app_schema_{prompt_hash[:8]}.json",
                mime="application/json",
                use_container_width=True
            )
            st.json(schema)


    if st.session_state.get("run_eval"):
        st.session_state.pop("run_eval")

        st.divider()
        st.subheader("📊 Evaluation Run (3 sample prompts)")

        with st.spinner("Running evaluation... this takes ~2-3 minutes"):
            from evaluation.evaluator import run_evaluation
            report = run_evaluation(prompt_ids=[1, 11, 18], save_results=True)

        eval_m1, eval_m2, eval_m3, eval_m4 = st.columns(4)
        eval_m1.metric("Overall Success", f"{report.overall_success_rate*100:.0f}%")
        eval_m2.metric("Avg Latency", f"{report.avg_latency_ms/1000:.1f}s")
        eval_m3.metric("Avg Cost", f"${report.avg_cost_usd:.4f}")
        eval_m4.metric("Avg Repairs", f"{report.avg_repair_attempts:.1f}")

        st.markdown(report.to_markdown_table())
