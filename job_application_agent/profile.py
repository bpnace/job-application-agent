from __future__ import annotations

import os
from pathlib import Path

import yaml

from .config import ROOT, default_profile_path
from .humanizer_policy import load_private_policy, public_baseline_status
from .models import CandidateProfile
from .utils import read_text_limited

def _resolve_path(value: object, config_path: Path) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _load_profile_file(path: Path) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Candidate profile is missing: {path}. Run `job-agent init` and complete candidate.yaml."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Candidate profile must be a YAML object: {path}")
    profile = raw.get("profile", raw)
    documents = raw.get("documents", {})
    humanizer = raw.get("humanizer", {})
    if not isinstance(profile, dict) or not isinstance(documents, dict) or not isinstance(humanizer, dict):
        raise ValueError("candidate.yaml requires `profile`, `documents`, and optional `humanizer` objects.")
    return profile, documents, humanizer


def candidate_document_paths(path: Path | None = None) -> dict[str, Path]:
    config_path = (path or default_profile_path()).expanduser().resolve()
    _profile, documents, humanizer = _load_profile_file(config_path)
    overrides = {
        "cv_text": os.getenv("JOB_AGENT_CV_TEXT_PATH", "").strip(),
        "cv_pdf": os.getenv("JOB_AGENT_CV_PDF_PATH", "").strip(),
        "humanizer": os.getenv("JOB_AGENT_HUMANIZER_PATH", "").strip(),
    }
    names = {
        "cv_text": "cv_text_path",
        "cv_pdf": "cv_pdf_path",
        "humanizer": "private_policy_path",
    }
    return {
        name: _resolve_path(
            overrides[name]
            or (humanizer.get(key, "") if name == "humanizer" else documents.get(key, ""))
            or (documents.get("humanizer_path", "") if name == "humanizer" else ""),
            config_path,
        )
        for name, key in names.items()
    }


def configured_cv_pdf_path(path: Path | None = None) -> Path | None:
    """Return the configured, existing CV PDF without exposing configuration data.

    Environment overrides retain precedence through :func:`candidate_document_paths`.
    A missing configured path is intentionally represented as ``None`` so callers
    preserve the existing fail-closed upload behaviour.
    """
    config_path = (path or default_profile_path()).expanduser().resolve()
    override = os.getenv("JOB_AGENT_CV_PDF_PATH", "").strip()
    if override:
        cv_pdf = _resolve_path(override, config_path)
        return cv_pdf if cv_pdf.is_file() else None
    try:
        cv_pdf = candidate_document_paths(config_path)["cv_pdf"]
    except FileNotFoundError:
        # Form-plan helpers remain usable with an in-memory profile before a
        # user has completed `job-agent init`.
        return None
    return cv_pdf if cv_pdf.is_file() else None


def load_candidate_profile(path: Path | None = None) -> CandidateProfile:
    config_path = (path or default_profile_path()).expanduser().resolve()
    profile_data, _documents, _humanizer = _load_profile_file(config_path)
    document_paths = candidate_document_paths(config_path)
    policy = load_private_policy(document_paths["humanizer"])
    baseline = public_baseline_status()
    return CandidateProfile.model_validate(
        {
            **profile_data,
            "cv_excerpt": read_text_limited(document_paths["cv_text"], 5000),
            "humanizer_excerpt": "private policy loaded" if policy.loaded else "",
            "humanizer_policy_path": str(policy.path or ""),
            "humanizer_policy_sha256": policy.sha256,
            "humanizer_baseline_id": str(baseline.get("source_id") or ""),
            "humanizer_baseline_sha256": str(baseline.get("skill_sha256") or ""),
        }
    )


def profile_status() -> str:
    profile_path = default_profile_path()
    profile = load_candidate_profile(profile_path)
    document_paths = candidate_document_paths(profile_path)
    return "\n".join(
        [
            f"profile_path={profile_path}",
            "profile_loaded=true",
            f"phone_loaded={bool(profile.phone)}",
            f"address_loaded={bool(profile.address)}",
            f"cv_loaded={document_paths['cv_text'].is_file() and bool(profile.cv_excerpt)}",
            f"cv_pdf_loaded={document_paths['cv_pdf'].is_file()}",
            f"humanizer_loaded={document_paths['humanizer'].is_file() and bool(profile.humanizer_excerpt)}",
            f"project_root={ROOT}",
        ]
    )
