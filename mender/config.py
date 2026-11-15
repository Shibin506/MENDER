from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MENDER_", env_file=".env", extra="ignore")

    # GitHub App
    github_app_id: str = ""
    github_private_key: str = ""
    github_private_key_path: Path | None = None
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"

    # Model backend: "claude" or "gemini"
    provider: str = "claude"
    gemini_model: str = "gemini-3.5-flash"
    gemini_api_key: str = ""
    # "low" | "medium" | "high"; empty uses the model default. Lower is faster.
    gemini_reasoning_effort: str = ""

    # Claude
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_output_tokens: int = 16000

    # Prompt budgets, in characters. Logs and diffs are usually far larger than
    # what is useful to the model; these bound the cost of a single diagnosis.
    log_budget_chars: int = 24_000
    diff_budget_chars: int = 30_000
    file_context_budget_chars: int = 30_000
    max_context_files: int = 4

    # Storage and worker
    database_url: str = "sqlite:///./mender.db"
    worker_poll_seconds: float = 1.0
    job_max_attempts: int = 4
    job_visibility_timeout_seconds: int = 600
    worker_metrics_port: int = 9100

    dry_run: bool = False
    # Re-run failed jobs once when every failure looks like an infrastructure flake.
    auto_rerun_flaky: bool = False

    def private_key(self) -> str:
        if self.github_private_key:
            return self.github_private_key.replace("\\n", "\n")
        if self.github_private_key_path:
            return self.github_private_key_path.read_text()
        raise RuntimeError("Set MENDER_GITHUB_PRIVATE_KEY or MENDER_GITHUB_PRIVATE_KEY_PATH")


@lru_cache
def get_settings() -> Settings:
    return Settings()
