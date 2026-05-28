import time
import json
from dataclasses import dataclass, field
from pipeline.orchestrator import PipelineOrchestrator
from evaluation.test_prompts import EVALUATION_PROMPTS

@dataclass
class EvalResult:
    prompt_id:          int
    category:           str
    prompt:             str
    success:            bool
    latency_ms:         float = 0.0
    repair_attempts:    int   = 0
    tokens_used:        int   = 0
    cost_usd:           float = 0.0
    validation_passed:  bool  = False
    failure_type:       str | None = None
    error_message:      str | None = None
    pages_generated:    int   = 0
    endpoints_generated:int   = 0
    tables_generated:   int   = 0

@dataclass
class EvaluationReport:
    total_prompts:        int
    overall_success_rate: float
    real_success_rate:    float
    edge_success_rate:    float
    avg_latency_ms:       float
    avg_repair_attempts:  float
    avg_tokens:           float
    avg_cost_usd:         float
    failures_by_type:     dict  = field(default_factory=dict)
    per_prompt_results:   list[EvalResult] = field(default_factory=list)

    def to_markdown_table(self) -> str:
        """
        Returns a GitHub-flavored markdown table string.
        Header row:
          | ID | Category | Success | Latency | Repairs | Tokens | Pages | Endpoints | Tables |
        Separator row uses |---|---|...| format.
        Data rows: one per EvalResult in self.per_prompt_results.
          - success renders as "✅" if True, "❌" if False
          - latency_ms formatted as f"{r.latency_ms:.0f}ms"
          - cost_usd NOT shown in the table (already in summary metrics)
        Return the full string with newlines joining all rows.
        """
        lines = [
            "| ID | Category | Success | Latency | Repairs | Tokens | Pages | Endpoints | Tables |",
            "|---|---|---|---|---|---|---|---|---|"
        ]
        for r in self.per_prompt_results:
            success_str = "✅" if r.success else "❌"
            latency_str = f"{r.latency_ms:.0f}ms"
            lines.append(f"| {r.prompt_id} | {r.category} | {success_str} | {latency_str} | {r.repair_attempts} | {r.tokens_used} | {r.pages_generated} | {r.endpoints_generated} | {r.tables_generated} |")
        return "\n".join(lines)


def run_evaluation(prompt_ids: list[int] = None, save_results: bool = True) -> EvaluationReport:
    """
    Run evaluation on all or selected prompts.
    """
    orchestrator = PipelineOrchestrator()
    
    if prompt_ids is None:
        filtered_prompts = EVALUATION_PROMPTS
    else:
        filtered_prompts = [p for p in EVALUATION_PROMPTS if p["id"] in prompt_ids]

    results = []
    
    for prompt_data in filtered_prompts:
        print(f"Running prompt {prompt_data['id']}/20: {prompt_data['prompt'][:50]}...")
        
        try:
            result = orchestrator.generate(prompt_data["prompt"])
            success = result.final_schema is not None
            
            eval_result = EvalResult(
                prompt_id=prompt_data["id"],
                category=prompt_data["category"],
                prompt=prompt_data["prompt"],
                success=success,
                latency_ms=result.total_latency_ms,
                repair_attempts=result.repair_attempts,
                tokens_used=result.total_tokens_used,
                cost_usd=result.estimated_cost_usd,
                validation_passed=result.validation_passed,
                pages_generated=len(result.final_schema.ui_schema) if result.final_schema else 0,
                endpoints_generated=len(result.final_schema.api_schema) if result.final_schema else 0,
                tables_generated=len(result.final_schema.db_schema) if result.final_schema else 0,
            )
        except Exception as e:
            eval_result = EvalResult(
                prompt_id=prompt_data["id"],
                category=prompt_data["category"],
                prompt=prompt_data["prompt"],
                success=False,
                failure_type=type(e).__name__,
                error_message=str(e)
            )
            
        results.append(eval_result)
        status_str = '✅ OK' if eval_result.success else '❌ FAIL'
        print(f"  → {status_str} | {eval_result.latency_ms:.0f}ms | {eval_result.tokens_used} tokens")

    # AGGREGATE METRICS
    successful = [r for r in results if r.success]
    real_results = [r for r in results if r.category == "real"]
    edge_results = [r for r in results if r.category == "edge"]
    
    failure_types = {}
    for r in results:
        if not r.success and r.failure_type is not None:
            failure_types[r.failure_type] = failure_types.get(r.failure_type, 0) + 1
            
    overall_success_rate = len(successful) / len(results) if results else 0
    real_success_rate = len([r for r in real_results if r.success]) / len(real_results) if real_results else 0
    edge_success_rate = len([r for r in edge_results if r.success]) / len(edge_results) if edge_results else 0
    avg_latency_ms = sum(r.latency_ms for r in successful) / len(successful) if successful else 0
    avg_repair_attempts = sum(r.repair_attempts for r in results) / len(results) if results else 0
    avg_tokens = sum(r.tokens_used for r in results) / len(results) if results else 0
    avg_cost_usd = sum(r.cost_usd for r in results) / len(results) if results else 0
    
    report = EvaluationReport(
        total_prompts=len(results),
        overall_success_rate=overall_success_rate,
        real_success_rate=real_success_rate,
        edge_success_rate=edge_success_rate,
        avg_latency_ms=avg_latency_ms,
        avg_repair_attempts=avg_repair_attempts,
        avg_tokens=avg_tokens,
        avg_cost_usd=avg_cost_usd,
        failures_by_type=failure_types,
        per_prompt_results=results
    )
    
    if save_results:
        import os
        from datetime import datetime
        os.makedirs("evaluation/results", exist_ok=True)
        filename = f"evaluation/results/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_data = {
            "summary": {
                "overall_success_rate": report.overall_success_rate,
                "avg_latency_ms": report.avg_latency_ms,
                "avg_cost_usd": report.avg_cost_usd,
            },
            "results": [r.__dict__ for r in results]
        }
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
            
        print(f"\nResults saved to {filename}")

    return report
