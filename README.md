# App Compiler — AI-Powered Schema Generator

A multi-stage AI pipeline that converts natural language app descriptions into structured, validated JSON schemas covering UI, API, Database, and Auth layers.

**Live Demo:** [Live Streamlit App](https://share.streamlit.io)

- Python: 3.11+
- Streamlit: 1.35.0
- Model: claude-sonnet-4-20250514

## What This Does

You type a plain-English app description. The system runs it through a 5-stage AI pipeline. Each stage calls Claude Sonnet via the Anthropic API. The final output is a validated JSON configuration covering UI pages, API endpoints, database tables, auth roles, and business rules.

```text
  User Prompt
      │
      ▼
  Stage 1: Intent Extraction   (IntentExtractor)
      │  Outputs: IntentModel
      ▼
  Stage 2: System Design       (SystemDesigner)
      │  Outputs: DesignModel
      ▼
  Stage 3: Schema Generation   (SchemaGenerator)
      │  Outputs: UIPages, APIEndpoints, DBTables, AuthRoles, Rules
      ▼
  Stage 4: Refinement          (RefinementLayer)
      │  Outputs: Refined schema dict
      ▼
  Stage 5: Validation & Repair (ConsistencyChecker + RepairEngine)
      │  Up to 3 repair attempts (MAX_REPAIR_ATTEMPTS)
      ▼
  AppSchema (JSON) + GenerationResult
```

## Tech Stack

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| Runtime | Python | 3.11+ | Language |
| UI | Streamlit | 1.35.0 | Web interface |
| LLM | Anthropic API | — | claude-sonnet-4-20250514 |
| Validation | Pydantic | 2.7.0 | Schema validation |
| Database | SQLite | built-in | Run history storage |
| ORM | SQLAlchemy | 2.0.30 | Database access |
| Config | python-dotenv | 1.0.1 | Environment variables |

## Project Structure

```text
app-compiler/
├── app.py                    # Streamlit UI entrypoint
├── config.py                 # Environment variables & constants
├── requirements.txt          # Python dependencies
│
├── models/
│   ├── intent_model.py       # IntentModel (8 fields)
│   ├── design_model.py       # DesignModel, EntityField, EntityRelation
│   └── app_schema_model.py   # AppSchema, GenerationResult, + 8 others
│
├── pipeline/
│   ├── __init__.py           # PipelineStage base class, StageValidationError
│   ├── stage1_intent.py      # IntentExtractor.extract()
│   ├── stage2_design.py      # SystemDesigner.design()
│   ├── stage3_schema.py      # SchemaGenerator.generate_all()
│   ├── stage4_refinement.py  # RefinementLayer.refine()
│   └── orchestrator.py       # PipelineOrchestrator.generate()
│
├── validation/
│   ├── json_validator.py     # JSONValidator — raw string → dict
│   ├── consistency_checker.py# ConsistencyChecker, ValidationReport
│   └── repair_engine.py      # RepairEngine, MaxRepairAttemptsError
│
├── database/
│   ├── db.py                 # init_db, save_run, get_recent_runs, get_run_schema
│   └── run_model.py          # RunRecord SQLAlchemy ORM model
│
└── evaluation/
    ├── test_prompts.py       # EVALUATION_PROMPTS — 20 prompts (10 real, 10 edge)
    ├── evaluator.py          # EvalResult, EvaluationReport, run_evaluation()
    └── results/              # Auto-generated JSON run logs (git-ignored)
```

## Local Setup

### Prerequisites
- Python 3.11 or higher
- An Anthropic API key (get one at console.anthropic.com)
- Git

### Steps

1. **Clone the repository**
```bash
   git clone https://github.com/YOUR_USERNAME/app-compiler.git
   cd app-compiler
```

2. **Create and activate a virtual environment**
```bash
   python -m venv venv
   # macOS / Linux:
   source venv/bin/activate
   # Windows:
   venv\Scripts\activate
```

3. **Install dependencies**
```bash
   pip install -r requirements.txt
```

4. **Configure your API key**

   Create a file named `.env` in the project root:
Or create `.streamlit/secrets.toml`:
```toml
   ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```
   The app checks `.streamlit/secrets.toml` first, then falls back
   to `.env`.

5. **Run the app**
```bash
   streamlit run app.py
```
   Open http://localhost:8501 in your browser.

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| ANTHROPIC_API_KEY | (required) | Your Anthropic API key |
| DATABASE_URL | sqlite:///./compiler.db | SQLite database path |
| MAX_REPAIR_ATTEMPTS | 3 | Max validation repair loops |
| CLAUDE_MODEL | claude-sonnet-4-20250514 | Locked — do not change |
| CLAUDE_TEMPERATURE | 0.2 | Generation temperature |
| CLAUDE_MAX_TOKENS | 4000 | Max tokens per API call |
| CLAUDE_TEMPERATURE_REPAIR | 0.1 | Temperature during repairs |

## Usage Guide

### Generating a Schema

- Enter a plain-English description in the text area
- Click "🚀 Generate App Schema"
- Watch the 5-stage pipeline progress bar
- Review generated schemas across the 5 tabs: UI Schema, API Schema, DB Schema, Auth Schema, Business Rules
- Download the full JSON from the "Full JSON" tab
- Previous runs are accessible in the left sidebar

### Running the Evaluation Suite

**Via the UI:**
Click "Run Quick Eval (3 prompts)" in the sidebar. Runs prompts 1 (CRM), 11 (vague), and 18 (todo app). Takes 2–3 minutes.

**Via Python directly:**
```python
from evaluation.evaluator import run_evaluation

# Run all 20 prompts
report = run_evaluation()
print(f"Success rate: {report.overall_success_rate:.0%}")
print(report.to_markdown_table())

# Run specific prompts
report = run_evaluation(prompt_ids=[1, 2, 3])

# Run without saving to disk
report = run_evaluation(save_results=False)
```
Results are saved to `evaluation/results/run_YYYYMMDD_HHMMSS.json`.

### Example Prompts

**Works well:**
- "Build a CRM with login, contacts, dashboard, role-based access, and premium plan with Stripe payments. Admins can see analytics."
- "Create a healthcare appointment booking system with patient profiles, doctor availability, appointment reminders, and medical history."
- "Create a real estate listing platform with property search, agent profiles, appointment booking, and favorites."

**Handled gracefully (edge cases):**
- "Build an app."
- "I need a CRM. But also no login. But users should have completely separate data from each other."

## Output Schema Reference

The pipeline produces an AppSchema object (also available as JSON). Documented are each of the 7 top-level keys:

**`meta`** — App metadata
```json
{
  "app_name": "CRM System",
  "app_type": "web_application",
  "complexity_score": 7,
  "prompt_hash": "a1b2c3d4"
}
```

**`ui_schema`** — Array of UIPage objects. Each page has:
  `title`, `route`, `layout`, `access_roles`, `components`
  Each component has: `type`, `id`, `data_source` (nullable)

**`api_schema`** — Array of APIEndpoint objects. Each has:
  `method` (GET/POST/PUT/PATCH/DELETE), `path`, `auth_required`,
  `allowed_roles`, `request_body`, `response_schema`,
  `validation_rules`

**`db_schema`** — Array of DBTable objects. Each has:
  `name`, `columns` (with `primary_key`, `unique`, `nullable`,
  `foreign_key` flags), `indexes`

**`auth_schema`** — Array of AuthRole objects. Each has:
  `name`, `inherits_from` (nullable), `permissions`

**`business_rules`** — Array of BusinessRule objects. Each has:
  `name`, `condition`, `action`, `affected_components`

**`assumptions`** / **`warnings`** — String arrays of notes from
  the pipeline about gaps filled or issues detected.

## Architecture Decisions

**Why Pydantic v2 for model validation**: Every Claude API response is validated against a Pydantic model. If Claude returns malformed JSON or missing fields, the stage fails loudly with a StageValidationError rather than propagating None values silently through the pipeline.

**Why synchronous SQLAlchemy (not async)**: Streamlit's execution model is synchronous. Using async SQLAlchemy would require an event loop that conflicts with Streamlit's own runner. Synchronous SQLAlchemy with SQLite is simpler and correct for this use case.

**Why CLAUDE_TEMPERATURE=0.2 for generation and 0.1 for repair**: Lower temperature during repair (CLAUDE_TEMPERATURE_REPAIR=0.1) produces more deterministic corrections. Higher temperature during generation (0.2) allows the model to be creative while still being structured.

**Why the repair loop has a hard cap (MAX_REPAIR_ATTEMPTS=3)**: Unbounded repair loops would run indefinitely on prompts with fundamental ambiguities. Three attempts is enough to fix most schema inconsistencies; if the schema is still invalid after three repairs, the pipeline surfaces a warning rather than looping forever.

**Why a single app.py for the entire UI**: Streamlit apps work best as a single file for simpler applications. All UI state lives in st.session_state. Splitting the UI into modules adds complexity without benefit at this scale.

## Extending the Pipeline

### Adding a New Pipeline Stage

1. Create `pipeline/stage5_yourname.py`
2. Define a class that inherits from `PipelineStage` (imported from `pipeline/__init__.py`)
3. Implement your method: call `self.call_claude(system, user)` to get `(response_text, tokens, cost)`; call `self._extract_json_object(response_text)` to parse JSON
4. Validate the parsed dict with a Pydantic model from `models/`
5. Add the stage call to `PipelineOrchestrator.generate()` in `pipeline/orchestrator.py`, call `progress_callback("your_stage", "complete")`, and add tokens and cost to the running totals.

### Adding New Test Prompts

Add a dict to `EVALUATION_PROMPTS` in `evaluation/test_prompts.py`:
```python
{
    "id": 21,
    "category": "real",
    "prompt": "Your new test prompt here."
}
```
Then run: `python -c "from evaluation.evaluator import run_evaluation; run_evaluation(prompt_ids=[21])"`

### Changing the Claude Model

Update `CLAUDE_MODEL` in `config.py`. The model string is passed to every `self.call_claude()` call via `PipelineStage`. No other changes are needed.

## Evaluation Results

| Metric | Value |
|---|---|
| Overall Success Rate | _% |
| Real Prompts (1–10) | _% |
| Edge Cases (11–20) | _% |
| Avg Latency | _s |
| Avg Tokens per Run | _ |
| Avg Cost per Run | $_ |
| Avg Repair Attempts | _ |

> Run `python -c "from evaluation.evaluator import run_evaluation; r = run_evaluation(); print(r.to_markdown_table())"` to populate these numbers.

## Known Limitations

- **No code generation**: The pipeline produces a schema/blueprint, not runnable application code. It describes what to build, not the implementation.
- **SQLite is ephemeral on Streamlit Cloud**: Run history resets on each deployment restart. For persistence, swap DATABASE_URL for a PostgreSQL connection string and replace SQLite-specific SQLAlchemy syntax.
- **Cost on long prompts**: Very detailed prompts (100+ words) can cost $0.05–$0.15 per run. The "Est. Cost" metric in the UI shows the precise cost after each generation.
- **Repair loop does not always converge**: Prompts with fundamental logical contradictions (e.g. prompt 14: CRM with no login but separate user data) will exhaust MAX_REPAIR_ATTEMPTS and may return a schema with warnings rather than a clean validation pass.
- **Rate limits**: High-volume evaluation (all 20 prompts) may hit Anthropic API rate limits on free-tier API keys. Add `time.sleep(2)` between prompts in `run_evaluation()` if this occurs.

## License

MIT License. See LICENSE file.
