from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://oncall:oncall@localhost:5433/oncall"

    anthropic_api_key: str = ""
    slack_webhook_url: str = ""

    # Per-step model selection. Sonnet 5 for the reasoning-heavy steps,
    # Haiku 4.5 for the classification-shaped ones.
    model_commit_analysis: str = "claude-sonnet-5"
    model_runbook_match: str = "claude-haiku-4-5"
    model_impact: str = "claude-haiku-4-5"
    model_postmortem: str = "claude-sonnet-5"

    runbooks_dir: str = "/srv/runbooks"
    postmortems_dir: str = "/srv/postmortems"
    demo_repo_dir: str = "/srv/demo-repo"
    demo_log_file: str = "/srv/demo-logs/access.log"


@lru_cache
def get_settings() -> Settings:
    return Settings()
