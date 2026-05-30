

# ⚡ ZEMO.ai

### The AI-Powered Full-Stack Application Compiler

**Describe your app in plain English. ZEMO.ai architects, compiles, and delivers a production-ready full-stack codebase — in seconds.**

Built on a state-driven multi-stage pipeline powered by **Google Gemini 2.5 Flash**.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](#technical-stack)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35-FF4B4B?logo=streamlit&logoColor=white)](#technical-stack)
[![Gemini 2.5 Flash](https://img.shields.io/badge/Gemini-2.5_Flash-4285F4?logo=google&logoColor=white)](#technical-stack)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](#technical-stack)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)


---

## What is ZEMO.ai?

ZEMO.ai is not a chatbot wrapper. It is a **deterministic, multi-stage compiler** that transforms a single natural language sentence into a fully validated JSON master schema — covering UI pages, REST API endpoints, database tables, RBAC roles, and business rules — then orchestrates Google Gemini to synthesize a complete, downloadable full-stack codebase from that schema.

The system handles ambiguity by design. Vague prompts like *"Build an app"* don't crash the pipeline — they trigger documented architectural assumptions that flow through every downstream stage. Contradictory requirements like *"no login but separate user data"* are detected, flagged, and resolved logically. Every output is validated against strict Pydantic v2 models and cross-layer consistency checks before it ever reaches the user.

---

## Core Architectural Features

### 1. Five-Stage Compilation Pipeline

ZEMO.ai processes every prompt through a deterministic, ordered pipeline. Each stage has a dedicated Gemini system prompt, a typed Pydantic output contract, and built-in retry logic with exponential backoff for transient API failures.

```
 User Prompt
     │
     ▼
 Stage 1 — Intent Extraction        → IntentModel
     │     (brand name, entities, roles, features, ambiguities, assumptions)
     ▼
 Stage 2 — System Design            → DesignModel
     │     (entity fields, relationships, auth strategy, data flows)
     ▼
 Stage 3 — Schema Generation        → 5× parallel sub-schemas
     │     (UIPages, APIEndpoints, DBTables, AuthRoles, BusinessRules)
     ▼
 Stage 4 — Refinement               → Normalized schema dict
     │     (cross-entity naming consistency, foreign key alignment)
     ▼
 Stage 5 — Validation & Repair      → Final AppSchema
           (3-check consistency engine + surgical per-layer repair loop)
```

Every stage's output — tokens consumed, latency, cost estimate — is tracked in a `StageResult` object and persisted to SQLite for historical analysis.

### 2. Self-Healing Validation & Surgical Repair Engine

Stage 5 runs three independent cross-layer consistency checks:

| Check | What It Validates |
|---|---|
| **API ↔ DB** | Every POST/PUT/PATCH request body field exists as a column in at least one DB table |
| **UI ↔ API** | Every `UIComponent.data_source` path resolves to a defined API endpoint |
| **Auth ↔ All** | Every role name referenced in UI pages and API endpoints is defined in the auth schema |

When errors are found, the `RepairEngine` regenerates **only the broken layer** — not the entire pipeline. Auth errors are often fixed with zero-cost pure Python patches (adding a missing role). API/DB and UI/API errors trigger targeted Gemini calls at repair temperature (`0.1`) for deterministic corrections. The loop runs up to `MAX_REPAIR_ATTEMPTS` (default: 3) before surfacing residual issues as warnings.

### 3. Full-Stack Code Synthesis & In-Browser IDE

Once a validated schema exists, the **Full-Stack Studio** (`build_frontend.py`) sends it to Gemini 2.5 Flash with a comprehensive code-generation prompt. The response is parsed through a **4-strategy self-healing parser**:

1. **Primary** — Strict `<file name="...">...</file>` XML extraction
2. **Repair** — Patch truncated responses missing closing tags
3. **Fallback** — Extract fenced markdown code blocks
4. **Emergency** — Dump raw output as plain text

Generated files are compiled into an in-memory ZIP archive with `ZIP_DEFLATED` compression, dynamically named after the project's brand name (e.g., `LumiCart.zip`). An interactive split-pane IDE viewer displays the full file tree with syntax-highlighted source code, per-file downloads, and project-level metrics.

**Streaming synthesis** — file names appear in the UI in real-time as Gemini generates them, via live regex detection on the accumulating response buffer.

### 4. State-Driven SPA Routing

The entire application runs as a single Streamlit process with two views — **Schema Compiler** and **Full-Stack Studio** — routed through `st.session_state.current_view`. View transitions are triggered by explicit user actions (the "Proceed to Full-Stack Studio" button), ensuring no accidental data loss. All generated schemas, metrics, and pipeline state persist in `st.session_state` across Streamlit reruns.

### 5. Schema History & Diff Viewer

The `pages/history.py` module provides a dedicated schema comparison tool accessible via Streamlit's multi-page routing. Users can select any two stored generation runs and view section-by-section diffs across all eight schema layers (`meta`, `ui_schema`, `api_schema`, `db_schema`, `auth_schema`, `business_rules`, `assumptions`, `warnings`), with color-coded change summaries and an exportable Markdown diff report.

---

## Technical Stack

| Layer | Technology | Role |
|---|---|---|
| **Core Runtime** | Python 3.10+ | Language runtime |
| **SPA Engine** | Streamlit 1.35 | Single-page application framework with session state routing |
| **LLM Orchestration** | Google Generative AI (`google-genai` 0.8.0) | Gemini 2.5 Flash — schema generation, code synthesis, repair |
| **Data Modeling** | Pydantic v2.7 | Strict schema enforcement with `model_validator` null coercion |
| **Storage** | SQLite via SQLAlchemy 2.0 | Historical build tracking in `compiler.db` |
| **Configuration** | python-dotenv 1.0 | Secure `.env`-based API key management |
| **Data Display** | pandas 2.2 | Database schema table rendering |
| **Testing** | pytest 8.2 | Evaluation framework execution |

---

## Project Structure

```
zemo-ai/
├── main.py                        # Entrypoint — SPA view router
├── app.py                         # Schema Compiler UI (Stages 1-5)
├── build_frontend.py              # Full-Stack Studio UI (Code Synthesis)
├── config.py                      # Environment config & constants
├── health_check.py                # Pre-flight diagnostic (9 checks)
├── run_all.py                     # Dev orchestrator (both servers)
│
├── models/
│   ├── intent_model.py            # IntentModel (Stage 1 output)
│   ├── design_model.py            # DesignModel, EntityRelation (Stage 2)
│   └── app_schema_model.py        # AppSchema, UIPage, APIEndpoint,
│                                  # DBTable, AuthRole, BusinessRule,
│                                  # StageResult, GenerationResult
│
├── pipeline/
│   ├── __init__.py                # PipelineStage base class (Gemini client,
│   │                              # 4-strategy JSON extraction, retry logic)
│   ├── stage1_intent.py           # IntentExtractor
│   ├── stage2_design.py           # SystemDesigner
│   ├── stage3_schema.py           # SchemaGenerator (5 sub-schemas)
│   ├── stage4_refinement.py       # RefinementLayer
│   └── orchestrator.py            # PipelineOrchestrator (end-to-end)
│
├── validation/
│   ├── json_validator.py          # JSONValidator — raw string → dict
│   ├── consistency_checker.py     # 3 cross-layer checks → ValidationReport
│   └── repair_engine.py           # Surgical per-layer repair (Python + Gemini)
│
├── database/
│   ├── db.py                      # SQLAlchemy engine, CRUD helpers
│   └── run_model.py               # GenerationRun ORM model
│
├── evaluation/
│   ├── test_prompts.py            # 20-prompt stress-test dataset
│   ├── evaluator.py               # EvalResult, EvaluationReport, runner
│   └── results/                   # Timestamped JSON run logs (git-ignored)
│
├── pages/
│   └── history.py                 # Schema History & Diff Viewer
│
├── requirements.txt
├── .env                           # API keys (git-ignored)
├── .gitignore
└── LICENSE                        # MIT
```

---

## Rigorous Evaluation Framework

The `evaluation/` module contains a production-grade testing infrastructure designed to measure pipeline reliability under adversarial conditions.

### 20-Prompt Stress-Test Dataset

| Category | Count | Purpose |
|---|---|---|
| **Real Prompts** (IDs 1–10) | 10 | High-fidelity production targets — CRM, e-commerce, healthcare, SaaS invoicing, real estate, LMS, restaurant management, recruitment, fitness tracking |
| **Edge Cases** (IDs 11–20) | 10 | Engineered adversarial inputs — extremely vague (`"Build an app."`), contradictory (`"CRM with no login but separate user data"`), overspecified, compliance-heavy, logical inconsistencies, minimal prompts |

### Real-Time Performance Metrics

Every evaluation run tracks per-prompt:

- **Success Rate** — Did the pipeline produce a non-null `AppSchema` that passes Pydantic validation?
- **Latency** — Wall-clock time from prompt submission to final schema delivery
- **Retry Loops** — Number of repair attempts consumed by the validation engine
- **Token Consumption** — Total input + output tokens across all Gemini calls
- **Estimated Cost** — USD cost estimate using blended Gemini 2.5 Flash pricing
- **Failure Type Categorization** — Structured `try/except` blocks classify failures as `StageValidationError`, `json.JSONDecodeError`, `ValidationError`, `EnvironmentError`, or generic `Exception`
- **Schema Depth** — Pages, endpoints, and tables generated per prompt

### Automated Historical Performance Ledger

Every evaluation run is serialized as a timestamped JSON snapshot:

```
evaluation/results/run_20260530_193700.json
```

These files accumulate over time, creating a historical record of pipeline performance across code changes, model updates, and prompt engineering iterations.

### Running the Evaluation

```bash
# Full 20-prompt stress test
python -c "from evaluation.evaluator import run_evaluation; run_evaluation()"

# Quick 3-prompt smoke test (from the Streamlit sidebar)
# Uses prompts 1 (CRM), 11 (vague), and 18 (minimal)
```

---

## Installation & Local Deployment

### Prerequisites

- **Python 3.10+** (tested on 3.11 and 3.12)
- A **Google Gemini API key** — get one free at [aistudio.google.com](https://aistudio.google.com)
- Git

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/zemo-ai.git
cd zemo-ai

# 2. Create and activate a virtual environment
python -m venv ZEMO

# macOS / Linux:
source ZEMO/bin/activate

# Windows:
ZEMO\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your API key
#    Create a .env file in the project root:
echo GEMINI_API_KEY=your_gemini_api_key_here > .env

# 5. Run the pre-flight health check (optional but recommended)
python health_check.py

# 6. Launch the app
streamlit run main.py
```

Open **http://localhost:8501** in your browser.

### Health Check

The `health_check.py` script runs 9 automated diagnostics before you launch — verifying Python version, `.env` configuration, API key presence, package availability, database state, and Gemini client instantiation. Run it once after setup to catch configuration issues early:

```bash
python health_check.py
```

### Configuration Reference

All configuration is managed through environment variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(required)* | Google Gemini API key |
| `DATABASE_URL` | `sqlite:///./compiler.db` | SQLAlchemy database connection string |
| `MAX_REPAIR_ATTEMPTS` | `3` | Maximum validation repair cycles |
| `GEMINI_MODEL` | `gemini-2.5-flash` | LLM model identifier (hardcoded) |
| `GEMINI_TEMPERATURE` | `0.2` | Generation temperature |
| `GEMINI_TEMPERATURE_REPAIR` | `0.1` | Repair temperature (lower = more deterministic) |
| `GEMINI_MAX_OUTPUT_TOKENS` | `65536` | Maximum tokens per Gemini API call |

---

## Architecture Decisions

**Why Pydantic v2 with `model_validator(mode='before')`** — Gemini frequently outputs `"field": null` for fields that should be empty lists or dicts. Every schema model includes a `coerce_nulls` pre-validator that converts `null` → `[]` or `{}` before Pydantic's strict type checking runs, eliminating an entire class of `list_type` / `dict_type` validation errors without relaxing type safety.

**Why synchronous SQLAlchemy** — Streamlit's execution model is synchronous. Async SQLAlchemy would require an event loop that conflicts with Streamlit's own runner. Synchronous SQLAlchemy with `check_same_thread=False` is the correct choice for this architecture.

**Why surgical per-layer repair instead of full pipeline retry** — Regenerating the entire 5-stage pipeline costs ~15,000 tokens and 8+ seconds. Repairing a single broken layer (e.g., adding a missing auth role via pure Python) costs 0 tokens and <1ms. The `RepairEngine` dispatches to the cheapest possible fix for each error category.

**Why 4-strategy JSON extraction** — Gemini does not always comply with "output only JSON" instructions. The pipeline handles markdown fences, wrapper objects (`{"result": {...}}`), and bracket-matching extraction as cascading fallbacks, making the parser resilient to prompt-engineering failures without requiring model-specific hacks.

**Why `temperature=0.2` for generation and `0.1` for repair** — Lower temperature during repair produces more deterministic corrections. Slightly higher temperature during generation allows creative entity naming and feature inference while keeping structured output reliable.

---

## Future Roadmap

- **Serverless Persistence** — Transition from the current ephemeral local SQLite footprint to a fully serverless persistence layer (Supabase or Neon Serverless Postgres) to enable cloud deployment with durable build history across sessions and team-level collaboration.

- **Live Preview Sandbox** — Embed an isolated iframe-based preview environment where generated frontend HTML/CSS/JS is rendered live in the browser, with hot-reload as Gemini streams output.

- **Multi-Model Support** — Abstract the LLM layer to support model switching (Gemini Pro, Claude, GPT-4o) per-stage, enabling cost/quality tradeoff optimization at the stage level.

- **CI/CD Integration** — Export generated codebases directly to GitHub repositories with automated PR creation, enabling one-click deployment pipelines from natural language descriptions.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
