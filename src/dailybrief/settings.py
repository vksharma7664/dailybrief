"""
Load configuration from:
  - .env  (secrets / env-specific overrides) via pydantic-settings
  - config/*.yaml  (sources, keywords, recipients) via PyYAML
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Environment / secret settings (loaded from .env)
# ---------------------------------------------------------------------------

class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = "brief-bot@company.com"
    smtp_use_tls: bool = True
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    archive_base_url: str = "https://brief.yourco.in"
    # Run behaviour
    dry_run: bool = True
    dry_run_skip_claude: bool = True    # when True + dry_run=True: zero API calls
    # Cost & call guardrails
    max_run_cost_inr: float = 50.0
    max_claude_calls_per_run: int = 15


# ---------------------------------------------------------------------------
# Typed source config
# ---------------------------------------------------------------------------

class SourceConfig(BaseModel):
    id: str
    type: str                    # "rss" | "html"
    url: str
    category: str
    priority: int = 2
    fetch_full_text: bool = False
    parser: str = ""             # used for html sources (Day 3+)


# ---------------------------------------------------------------------------
# Aggregate settings object
# ---------------------------------------------------------------------------

class Settings:
    def __init__(self, config_dir: Path | str = "config") -> None:
        config_dir = Path(config_dir)

        self.env = EnvSettings()

        # sources.yaml
        raw_sources = yaml.safe_load((config_dir / "sources.yaml").read_text(encoding="utf-8"))
        self.sources: list[SourceConfig] = [
            SourceConfig(**s) for s in raw_sources["sources"]
        ]
        self.source_defaults: dict[str, Any] = raw_sources.get("defaults", {})

        # keywords.yaml
        raw_kw = yaml.safe_load((config_dir / "keywords.yaml").read_text(encoding="utf-8"))
        self.keywords_high: list[str] = raw_kw.get("high_priority", [])
        self.keywords_medium: list[str] = raw_kw.get("medium_priority", [])
        self.keywords_exclude: list[str] = raw_kw.get("exclude", [])

        # recipients.yaml
        raw_rec = yaml.safe_load((config_dir / "recipients.yaml").read_text(encoding="utf-8"))
        self.email_recipients: list[dict] = raw_rec.get("email", [])
        self.whatsapp_recipients: list[dict] = raw_rec.get("whatsapp", [])
