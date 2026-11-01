"""Application configuration loaded from the environment.

All secrets belong in a local ``.env`` file (see ``.env.example``). Never commit
real credentials, cookies or browser storage state to source control.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General ---------------------------------------------------------
    environment: str = "development"
    timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"

    # --- Database --------------------------------------------------------
    # Example: postgresql+psycopg://user:pass@host:5432/placement
    database_url: str = "postgresql+psycopg://localhost:5432/placement"

    # --- Superset portal -------------------------------------------------
    # Verified from sample_images/ (Oct 2026): the authenticated app lives at
    # app.joinsuperset.com under /students. The login page was NOT captured;
    # visiting /students while logged out presumably redirects to SSO.
    superset_base_url: str = "https://app.joinsuperset.com"
    superset_login_url: str = "https://app.joinsuperset.com/students"
    superset_notifications_path: str = "/students"
    superset_job_profiles_path: str = "/students/jobprofiles"
    # Path to the Playwright storage state captured by scripts/login.py.
    superset_storage_state: str = "secrets/storage_state.json"

    # --- Polling / reliability ------------------------------------------
    poll_interval_minutes: int = 30
    poll_nav_timeout_ms: int = 45_000
    poll_max_retries: int = 3
    poll_retry_base_seconds: float = 2.0
    poll_lock_file: str = "/tmp/placement-poll.lock"
    heartbeat_max_age_hours: int = 3
    # Live poll progress for the web app's progress bar
    poll_progress_file: str = ".cache/poll_progress.json"

    # --- Telegram --------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Email (SMTP) ----------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_email_to: str = ""
    smtp_use_tls: bool = True

    # Optional narrowing of the job list during scoped dry-runs (blank = no filter).
    job_search: str = ""
    job_status_filter: str = ""
    job_tab: str = ""
    # Optional JSON file overriding selectors in app/selectors.py.
    selectors_file: str = ""

    # --- Collector -----------------------------------------------------
    # "api" uses the portal's JSON endpoints (preferred); "dom" forces DOM
    # scraping; "auto" tries the API and falls back to the DOM.
    collector_mode: str = "auto"
    # Fetch full JD/eligibility detail per job (one extra request per job).
    fetch_job_details: bool = True
    # Cap detail requests (blank = no limit). Useful for cheap dry-runs.
    detail_limit: int = 0
    # The first successful poll seeds the database without alerting, so the
    # whole historical backlog does not arrive as one flood of notifications.
    suppress_alerts_on_first_run: bool = True
    # Maximum alerts queued per run; extras are recorded as suppressed.
    max_alerts_per_run: int = 25

    # --- Alerts ----------------------------------------------------------
    alert_digest_hour: int = 8
    alert_max_attempts: int = 5

    # --- AI / research ---------------------------------------------------
    openai_api_key: str = ""
    openai_base_url: str = ""
    research_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434"
    # Hard daily cap on model requests across all processes (Gemini free tier safety)
    llm_daily_request_limit: int = 500
    # Gap between requests, keeping under the free tier's requests-per-minute limit
    llm_min_interval_seconds: float = 6.5
    llm_usage_file: str = ".cache/llm_usage.json"

    # --- Search toolbox (week 1) -----------------------------------------
    # "auto" = Wikipedia + GDELT news + optional Brave + DuckDuckGo fallback.
    # Explicit values: auto | composite | wikipedia | gdelt | brave | duckduckgo
    search_backend: str = "auto"
    search_cache_dir: str = ".cache/search"
    search_cache_ttl_hours: int = 168  # 7 days, as planned
    search_timeout_seconds: float = 20.0
    search_max_results: int = 5
    # Wikimedia asks for a descriptive agent with contact information. Replace
    # the placeholder with your own repo/email when you deploy this.
    search_user_agent: str = "placement-intelligence/0.1 (contact: local@example.invalid)"
    # GDELT news lookback window (1d, 1w, 1m, 3m, 1y, 2y).
    search_gdelt_timespan: str = "2y"
    # Optional: Brave Search API free tier (no key = skipped).
    brave_search_api_key: str = ""

    # --- Page fetch (verification) ---------------------------------------
    # Comma-separated allow-list. Subdomains are included. An agent call may
    # add domains explicitly (e.g. a company's official site found in research).
    fetch_page_allowed_domains: str = (
        "wikipedia.org,wikimedia.org,wikidata.org,github.com,medium.com,dev.to,"
        "stackoverflow.com,reuters.com,bloomberg.com,techcrunch.com,"
        "economictimes.indiatimes.com,thehindu.com,business-standard.com,"
        "livemint.com,moneycontrol.com,yourstory.com,inc42.com,"
        "indianexpress.com,timesofindia.indiatimes.com"
    )
    fetch_page_max_bytes: int = 2_000_000
    fetch_page_max_chars: int = 20_000
    fetch_page_timeout_seconds: float = 20.0
    fetch_page_cache_ttl_hours: int = 168

    # --- Agent graph (week 2) -------------------------------------------
    agent_enabled: bool = True
    # Cap on full briefs per poll run, so a busy day cannot flood the search APIs.
    agent_max_briefs_per_run: int = 5
    # The critic retries once when more than this share of claims is unsupported.
    agent_max_unsupported_ratio: float = 0.3
    # Pages the critic may download per brief to verify a claim.
    agent_max_page_checks: int = 3
    # Stored company evidence is reused for this long before it is researched again.
    agent_company_cache_days: int = 7

    @property
    def storage_state_path(self) -> Path:
        return Path(self.superset_storage_state)

    @property
    def fetch_page_domains(self) -> tuple[str, ...]:
        raw = self.fetch_page_allowed_domains or ""
        return tuple(
            part.strip().lower().removeprefix("www.")
            for part in raw.split(",")
            if part.strip()
        )

    @property
    def search_cache_ttl_seconds(self) -> int:
        return max(0, int(self.search_cache_ttl_hours)) * 3600

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.alert_email_to)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
