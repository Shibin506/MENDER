from prometheus_client import Counter, Histogram

WEBHOOKS = Counter("mender_webhooks_total", "Webhook deliveries", ["event", "outcome"])
JOBS = Counter("mender_jobs_total", "Processed queue jobs", ["outcome"])
DIAGNOSES = Counter("mender_diagnoses_total", "Diagnoses produced", ["category", "used_llm"])
PATCHES = Counter("mender_patches_total", "Suggested patches by dry-run result", ["status"])
LLM_TOKENS = Counter("mender_llm_tokens_total", "Claude tokens", ["type"])
LLM_COST = Counter("mender_llm_cost_usd_total", "Estimated Claude spend in USD")
LLM_FAILURES = Counter("mender_llm_failures_total", "Diagnoses that fell back", ["error"])
LOG_COMPRESSION = Histogram(
    "mender_log_compression_ratio", "Share of the raw log sent to the model",
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
)
RUN_SECONDS = Histogram(
    "mender_run_seconds", "End-to-end time to diagnose a workflow run",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
)
