"""Central application configuration.

Every tunable value in the system is resolved here from environment variables so
that no module needs to call ``os.getenv`` directly. Reconciliation thresholds,
FX behaviour, Mojaloop endpoints and scheduler frequency were previously
hardcoded inside service functions; they are now configuration.

Deliberately dependency-free (stdlib only) so that importing config never pulls
in pydantic/sqlalchemy and can be used from scripts and migrations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# Load backend/.env before any setting is read, so a local developer does not
# have to export variables by hand. Real environment variables always win over
# the file, which is what makes the same code work in CI and in production.
try:
    from dotenv import load_dotenv

    _ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
    if _ENV_FILE.is_file():
        load_dotenv(_ENV_FILE, override=False)
except ImportError:  # pragma: no cover - dotenv is optional at runtime
    pass

# Sentinel used by the demo/dev experience. If API_KEY is left at this value the
# app still runs, but logs a loud warning, because it is a publicly known string.
DEMO_API_KEY = "review2-demo-key"


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class MatchingSettings:
    """Weights and tolerances for the reconciliation scoring model.

    The weights are expressed out of 100 and must sum to 100 so that a perfect
    match on every signal yields exactly 100. The previous implementation used
    0.4 / 0.4 / +40 which capped a no-reference match at 80 and therefore made
    the 90-point ``matched`` threshold unreachable without an exact reference
    hit.
    """

    # Score weights (sum must be 100).
    weight_amount: float = _env_float("MATCH_WEIGHT_AMOUNT", 40.0)
    weight_name: float = _env_float("MATCH_WEIGHT_NAME", 35.0)
    weight_reference: float = _env_float("MATCH_WEIGHT_REFERENCE", 15.0)
    weight_date: float = _env_float("MATCH_WEIGHT_DATE", 10.0)

    # Classification thresholds on the 0-100 composite score.
    matched_threshold: float = _env_float("MATCH_THRESHOLD_MATCHED", 90.0)
    partial_threshold: float = _env_float("MATCH_THRESHOLD_PARTIAL", 70.0)

    # A candidate scoring below this is never considered a match at all.
    candidate_floor: float = _env_float("MATCH_CANDIDATE_FLOOR", 45.0)

    # Amount tolerance. Relative tolerance is a fraction of the transaction
    # value; absolute tolerance is a floor in base currency so that tiny
    # transactions are not penalised by rounding.
    amount_relative_tolerance: float = _env_float("MATCH_AMOUNT_REL_TOLERANCE", 0.02)
    amount_absolute_tolerance: float = _env_float("MATCH_AMOUNT_ABS_TOLERANCE", 1.0)

    # Date tolerance in days for bank settlement. Settlement lags the transfer,
    # so anything inside `date_tolerance_days` scores full marks, decaying to
    # zero at `date_window_days`.
    date_tolerance_days: int = _env_int("MATCH_DATE_TOLERANCE_DAYS", 2)
    date_window_days: int = _env_int("MATCH_DATE_WINDOW_DAYS", 7)

    # ERP invoices legitimately precede payment by much longer than a bank
    # settlement lags it (net-30 terms are normal), so comparing an ERP posting
    # date against the same 2-day tolerance manufactures false timing
    # anomalies. ERP candidates get their own, wider window.
    erp_date_tolerance_days: int = _env_int("MATCH_ERP_DATE_TOLERANCE_DAYS", 10)
    erp_date_window_days: int = _env_int("MATCH_ERP_DATE_WINDOW_DAYS", 45)

    # Minimum fuzzy name similarity (0-100) for a pair to be blocked in as a
    # candidate when no reference or amount key matched, and the floor used by
    # the corroboration gate below.
    name_similarity_floor: float = _env_float("MATCH_NAME_FLOOR", 70.0)

    # Require at least one identifying signal (reference or counterparty name)
    # before a pair may be accepted as matched or partial. Without this, two
    # unrelated payments that happen to share an amount score highly on the
    # amount component alone and get accepted, which is the single most
    # dangerous false positive in reconciliation. Pairs that fail the gate are
    # demoted to flag_for_review rather than silently accepted.
    require_corroboration: bool = _env_bool("MATCH_REQUIRE_CORROBORATION", True)
    reference_corroboration_floor: float = _env_float("MATCH_REF_CORROBORATION_FLOOR", 75.0)

    # Amount bucket width used as a blocking key, as a fraction of amount.
    amount_bucket_fraction: float = _env_float("MATCH_AMOUNT_BUCKET_FRACTION", 0.02)

    # Safety valve: if a single transaction blocks in more than this many
    # candidates, keep only the best-scoring ones.
    max_candidates_per_transaction: int = _env_int("MATCH_MAX_CANDIDATES", 50)

    def validate(self) -> None:
        total = self.weight_amount + self.weight_name + self.weight_reference + self.weight_date
        if abs(total - 100.0) > 1e-6:
            raise ValueError(
                f"Matching weights must sum to 100, got {total}. "
                "Check MATCH_WEIGHT_* environment variables."
            )
        if self.partial_threshold > self.matched_threshold:
            raise ValueError("partial_threshold cannot exceed matched_threshold")


@dataclass(frozen=True)
class FXSettings:
    base_currency: str = _env_str("FX_BASE_CURRENCY", "USD").upper()
    # When True an unknown currency raises instead of being silently treated as
    # 1:1 with the base currency, which previously corrupted amount comparisons.
    strict_unknown_currency: bool = _env_bool("FX_STRICT_UNKNOWN_CURRENCY", False)
    provider: str = _env_str("FX_PROVIDER", "static")
    provider_url: str = _env_str("FX_PROVIDER_URL", "")
    refresh_timeout_seconds: float = _env_float("FX_REFRESH_TIMEOUT", 10.0)


@dataclass(frozen=True)
class MojaloopSettings:
    """Connection details for the Mojaloop hub / ml-testing-toolkit simulator."""

    enabled: bool = _env_bool("MOJALOOP_ENABLED", False)
    # ml-testing-toolkit default ports: 4040 (mock/simulated API surface),
    # 5050 (toolkit admin + test-runner API), 6060 (web UI).
    base_url: str = _env_str("MOJALOOP_BASE_URL", "http://localhost:4040")
    settlement_base_url: str = _env_str("MOJALOOP_SETTLEMENT_BASE_URL", "")
    transfers_path: str = _env_str("MOJALOOP_TRANSFERS_PATH", "/transfers")
    settlement_windows_path: str = _env_str(
        "MOJALOOP_SETTLEMENT_WINDOWS_PATH", "/settlementWindows"
    )
    settlements_path: str = _env_str("MOJALOOP_SETTLEMENTS_PATH", "/settlements")
    # The Testing Toolkit mounts settlements_1.0 at the root and settlements_2.0
    # under a "v2" prefix. Default targets 1.0; set to "v2" for the 2.0 API.
    settlement_api_prefix: str = _env_str("MOJALOOP_SETTLEMENT_API_PREFIX", "")
    # Toolkit admin API, used to launch test collections from our side.
    toolkit_api_url: str = _env_str("MOJALOOP_TOOLKIT_API_URL", "http://localhost:5050")
    # Nominal settlement window length. Used only when the hub reports a window
    # whose open and close timestamps are identical or missing, which would
    # otherwise give a zero-width reconciliation scope that matches nothing.
    settlement_window_hours: float = _env_float("MOJALOOP_SETTLEMENT_WINDOW_HOURS", 24.0)
    fsp_id: str = _env_str("MOJALOOP_FSP_ID", "reconciliation-engine")
    timeout_seconds: float = _env_float("MOJALOOP_TIMEOUT", 15.0)
    max_retries: int = _env_int("MOJALOOP_MAX_RETRIES", 3)
    retry_backoff_seconds: float = _env_float("MOJALOOP_RETRY_BACKOFF", 0.5)
    # Falls back to the built-in simulated payload when the hub is unreachable,
    # so the demo still works offline.
    fallback_to_simulator: bool = _env_bool("MOJALOOP_FALLBACK_SIMULATOR", True)
    verify_tls: bool = _env_bool("MOJALOOP_VERIFY_TLS", True)

    @property
    def effective_settlement_base_url(self) -> str:
        return self.settlement_base_url or self.base_url


@dataclass(frozen=True)
class SchedulerSettings:
    """Automated reconciliation scheduling (configurable run frequency)."""

    enabled: bool = _env_bool("SCHEDULER_ENABLED", False)
    interval_minutes: int = _env_int("SCHEDULER_INTERVAL_MINUTES", 15)
    # Also pull from Mojaloop before each scheduled reconciliation run.
    sync_mojaloop: bool = _env_bool("SCHEDULER_SYNC_MOJALOOP", False)
    # Refresh the cash-flow forecast after each scheduled run.
    refresh_forecast: bool = _env_bool("SCHEDULER_REFRESH_FORECAST", True)
    # Prevent overlapping runs piling up if one run is slow.
    max_instances: int = _env_int("SCHEDULER_MAX_INSTANCES", 1)
    misfire_grace_seconds: int = _env_int("SCHEDULER_MISFIRE_GRACE", 300)


@dataclass(frozen=True)
class ForecastSettings:
    default_horizon_days: int = _env_int("FORECAST_DEFAULT_DAYS", 7)
    max_horizon_days: int = _env_int("FORECAST_MAX_DAYS", 90)
    # Prophet needs a meaningful amount of history; below this we fall back to a
    # simpler model rather than pretending to have a seasonal fit.
    min_history_days: int = _env_int("FORECAST_MIN_HISTORY_DAYS", 14)
    prefer_prophet: bool = _env_bool("FORECAST_PREFER_PROPHET", True)
    # Forecasts are cached; a GET older than this triggers a regeneration.
    cache_ttl_seconds: int = _env_int("FORECAST_CACHE_TTL", 300)
    random_seed: int = _env_int("FORECAST_RANDOM_SEED", 42)
    # Allow synthetic padding only when explicitly permitted (demo mode).
    allow_synthetic_padding: bool = _env_bool("FORECAST_ALLOW_SYNTHETIC", True)


@dataclass(frozen=True)
class SecuritySettings:
    api_key: str = _env_str("API_KEY", DEMO_API_KEY)
    cors_origins: List[str] = field(
        default_factory=lambda: _env_list(
            "CORS_ORIGINS", ["http://localhost:5173", "http://127.0.0.1:5173"]
        )
    )
    # HMAC-SHA256 shared secret for inbound Mojaloop webhooks. Empty disables
    # verification (required for the toolkit simulator, which does not sign).
    webhook_secret: str = _env_str("WEBHOOK_SECRET", "")
    webhook_signature_header: str = _env_str("WEBHOOK_SIGNATURE_HEADER", "X-Signature")
    # Simple in-process rate limit on write endpoints.
    rate_limit_enabled: bool = _env_bool("RATE_LIMIT_ENABLED", True)
    rate_limit_requests: int = _env_int("RATE_LIMIT_REQUESTS", 120)
    rate_limit_window_seconds: int = _env_int("RATE_LIMIT_WINDOW", 60)

    @property
    def is_using_demo_key(self) -> bool:
        return self.api_key == DEMO_API_KEY


@dataclass(frozen=True)
class Settings:
    app_name: str = _env_str("APP_NAME", "AI Payment Reconciliation Engine - Mojaloop")
    app_version: str = _env_str("APP_VERSION", "3.0.0")
    environment: str = _env_str("ENVIRONMENT", "development")
    database_url: str = _env_str("DATABASE_URL", "sqlite:///./reconciliation.db")
    sql_echo: bool = _env_bool("SQL_ECHO", False)
    db_pool_size: int = _env_int("DB_POOL_SIZE", 5)
    db_max_overflow: int = _env_int("DB_MAX_OVERFLOW", 10)
    db_pool_recycle_seconds: int = _env_int("DB_POOL_RECYCLE", 1800)
    # Seed synthetic demo data at startup when the database is empty.
    seed_on_startup: bool = _env_bool("SEED_ON_STARTUP", True)
    default_page_size: int = _env_int("DEFAULT_PAGE_SIZE", 100)
    max_page_size: int = _env_int("MAX_PAGE_SIZE", 1000)

    matching: MatchingSettings = field(default_factory=MatchingSettings)
    fx: FXSettings = field(default_factory=FXSettings)
    mojaloop: MojaloopSettings = field(default_factory=MojaloopSettings)
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)
    forecast: ForecastSettings = field(default_factory=ForecastSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def validate(self) -> List[str]:
        """Validate configuration, returning a list of human-readable warnings.

        Hard errors raise; soft problems are returned so the caller can log them
        without refusing to boot a development instance.
        """
        self.matching.validate()

        warnings: List[str] = []

        if self.security.is_using_demo_key:
            if self.is_production:
                raise RuntimeError(
                    "API_KEY is still set to the publicly known demo key. "
                    "Set a strong API_KEY before running in production."
                )
            warnings.append(
                "API_KEY is the built-in demo key; set API_KEY before exposing this service."
            )

        if "*" in self.security.cors_origins:
            warnings.append(
                "CORS_ORIGINS contains '*'; credentialed cross-origin requests will be rejected "
                "by browsers. List explicit origins instead."
            )

        if self.is_production and self.is_sqlite:
            warnings.append(
                "Running in production against SQLite. Concurrent writers will serialise on a "
                "single file; PostgreSQL is recommended."
            )

        if not self.security.webhook_secret:
            warnings.append(
                "WEBHOOK_SECRET is unset; inbound Mojaloop webhook signatures are not verified."
            )

        return warnings


settings = Settings()

__all__ = [
    "settings",
    "Settings",
    "MatchingSettings",
    "FXSettings",
    "MojaloopSettings",
    "SchedulerSettings",
    "ForecastSettings",
    "SecuritySettings",
    "DEMO_API_KEY",
]
