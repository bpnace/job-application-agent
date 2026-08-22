from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from .models import ScoringPolicy


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")


def default_runs_dir() -> Path:
    """Return the local directory for generated run artifacts.

    ``JOB_AGENT_RUNS_DIR`` is intentionally machine-local. It keeps generated
    browser state, caches, and audit artifacts out of a synced checkout.
    """
    configured = os.getenv("JOB_AGENT_RUNS_DIR", "").strip()
    if not configured:
        return default_agent_home() / "runs"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def default_agent_home() -> Path:
    """Return the local, ignored home for profiles, state and generated files."""
    configured = os.getenv("JOB_AGENT_HOME", "").strip()
    if not configured:
        return ROOT / ".job-agent"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def default_profile_path() -> Path:
    configured = os.getenv("JOB_AGENT_PROFILE_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    return default_agent_home() / "candidate.yaml"


def default_search_profile_path() -> Path:
    """Return the local search profile used by normal CLI runs.

    The bundled file is deliberately only a PII-free template.  A real search
    must live next to the candidate profile so every machine can have a
    different role, country, blacklist and portal query set without changing
    the checkout.
    """
    configured = os.getenv("JOB_AGENT_SEARCH_PROFILE_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    return default_agent_home() / "search_profile.yaml"


def bundled_search_profile_path() -> Path:
    """Return the versioned, generic starter profile."""
    return ROOT / "config" / "search_profile.yaml"


def default_tracker_path() -> Path:
    configured = os.getenv("JOB_AGENT_TRACKER_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    return default_agent_home() / "data" / "applications.jsonl"


def default_approvals_dir() -> Path:
    return default_agent_home() / "approvals"


class SearchSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    market: str = "Germany"
    profile_configured: bool = False
    target_roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    hard_exclusions: list[str] = Field(default_factory=list)
    employer_blacklist: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    required_location_terms: list[str] = Field(default_factory=list)
    allow_unknown_location: bool = True
    max_listing_age_days: int = 21
    fresh_listing_boost_days: int = 7
    allow_unknown_date: bool = True
    top_n: int = 10
    max_candidates: int = 220
    host_delay_seconds: float = 0.5

    def to_scoring_policy(self) -> ScoringPolicy:
        return ScoringPolicy(
            keywords=self.keywords,
            hard_exclusions=self.hard_exclusions,
            employer_blacklist=self.employer_blacklist,
            preferred_locations=self.preferred_locations,
            required_location_terms=self.required_location_terms,
            allow_unknown_location=self.allow_unknown_location,
            max_listing_age_days=self.max_listing_age_days,
            fresh_listing_boost_days=self.fresh_listing_boost_days,
            allow_unknown_date=self.allow_unknown_date,
            target_roles=self.target_roles,
            profile_configured=self.profile_configured,
        )


class SourceSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    urls: list[str] = Field(default_factory=list)
    feed_urls: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    search: SearchSettings
    sources: dict[str, SourceSettings] = Field(default_factory=dict)


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load an explicit profile or the local profile with a safe template fallback."""
    requested = Path(path).expanduser() if path is not None else default_search_profile_path()
    resolved = requested.resolve()
    if not resolved.is_file() and path is None:
        resolved = bundled_search_profile_path()
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    raw.setdefault("search", {})
    raw.setdefault("sources", {})
    raw["search"]["max_candidates"] = int(os.getenv("JOB_AGENT_LIVE_MAX_CANDIDATES", raw["search"].get("max_candidates", 220)))
    raw["search"]["host_delay_seconds"] = float(
        os.getenv("JOB_AGENT_HOST_DELAY_SECONDS", raw["search"].get("host_delay_seconds", 0.5))
    )
    return AppConfig.model_validate(raw)
