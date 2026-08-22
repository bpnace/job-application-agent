"""Local-only Humanizer policy loading and explicit baseline bootstrap.

The project deliberately ships no opinionated writing vocabulary.  A candidate's
German policy is local state, and any optional public baseline is downloaded only
when the user requests it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import default_agent_home


BASELINE_REPOSITORY = "blader/humanizer"
BASELINE_COMMIT = "523374dee72d67c7b2b5f858ea0094ffda49c3ac"
BASELINE_LICENSE = "MIT"
BASELINE_BASE_URL = f"https://raw.githubusercontent.com/{BASELINE_REPOSITORY}/{BASELINE_COMMIT}"
BASELINE_SKILL_URL = f"{BASELINE_BASE_URL}/SKILL.md"
BASELINE_LICENSE_URL = f"{BASELINE_BASE_URL}/LICENSE"


@dataclass(frozen=True)
class HumanizerPolicy:
    path: Path | None = None
    source_id: str = "not-configured"
    sha256: str = ""
    banned_terms: tuple[str, ...] = ()
    banned_patterns: tuple[str, ...] = ()
    replacements: dict[str, str] = field(default_factory=dict)
    forbid_colons: bool = False
    loaded: bool = False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _frontmatter(content: str, path: Path) -> tuple[dict[str, Any], str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)(.*)\Z", content, re.S)
    if not match:
        raise ValueError(f"Humanizer policy needs YAML frontmatter: {path}")
    raw = yaml.safe_load(match.group(1)) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Humanizer policy frontmatter must be a mapping: {path}")
    return raw, match.group(2)


def _string_list(raw: Any, key: str, path: Path) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ValueError(f"Humanizer policy `{key}` must be a list of non-empty strings: {path}")
    return tuple(item.strip() for item in raw)


def load_private_policy(path: Path | None) -> HumanizerPolicy:
    if path is None or not path.is_file():
        return HumanizerPolicy(path=path)
    content = path.read_text(encoding="utf-8")
    raw, _body = _frontmatter(content, path)
    banned_patterns = _string_list(raw.get("banned_patterns"), "banned_patterns", path)
    for pattern in banned_patterns:
        try:
            re.compile(pattern, re.I)
        except re.error as exc:
            raise ValueError(f"Invalid Humanizer regex in {path}: {exc}") from exc
    replacements_raw = raw.get("replacements") or {}
    if not isinstance(replacements_raw, dict) or not all(
        isinstance(source, str) and isinstance(target, str)
        for source, target in replacements_raw.items()
    ):
        raise ValueError(f"Humanizer policy `replacements` must be a string mapping: {path}")
    return HumanizerPolicy(
        path=path.resolve(),
        source_id=f"private:{path.resolve().name}",
        sha256=sha256_bytes(content.encode("utf-8")),
        banned_terms=_string_list(raw.get("banned_terms"), "banned_terms", path),
        banned_patterns=banned_patterns,
        replacements={source: target for source, target in replacements_raw.items()},
        forbid_colons=bool(raw.get("forbid_colons", False)),
        loaded=True,
    )


def baseline_paths(agent_home: Path | None = None) -> dict[str, Path]:
    root = (agent_home or default_agent_home()).expanduser().resolve() / "humanizer" / "public"
    return {"directory": root, "skill": root / "SKILL.md", "license": root / "LICENSE", "lock": root / "baseline.lock.json"}


def bootstrap_public_baseline(agent_home: Path | None = None, *, timeout: float = 20.0) -> dict[str, str | bool]:
    """Fetch the exact public baseline once; never refresh it implicitly."""
    paths = baseline_paths(agent_home)
    lock = paths["lock"]
    if lock.is_file():
        payload = json.loads(lock.read_text(encoding="utf-8"))
        if payload.get("commit") == BASELINE_COMMIT and paths["skill"].is_file() and paths["license"].is_file():
            return {"downloaded": False, "source_id": f"{BASELINE_REPOSITORY}@{BASELINE_COMMIT}", "lock_path": str(lock)}
        raise ValueError("Existing Humanizer baseline is incomplete or differs from the pinned lock; remove it manually before bootstrapping again.")
    paths["directory"].mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": "job-application-agent/0.1"}) as client:
            skill = client.get(BASELINE_SKILL_URL)
            license_text = client.get(BASELINE_LICENSE_URL)
        skill.raise_for_status()
        license_text.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Unable to download pinned Humanizer baseline: {exc}") from exc
    if "MIT License" not in license_text.text:
        raise ValueError("Pinned Humanizer baseline did not include the expected MIT license text.")
    paths["skill"].write_bytes(skill.content)
    paths["license"].write_bytes(license_text.content)
    lock_payload = {
        "repository": BASELINE_REPOSITORY,
        "commit": BASELINE_COMMIT,
        "license": BASELINE_LICENSE,
        "skill_url": BASELINE_SKILL_URL,
        "license_url": BASELINE_LICENSE_URL,
        "skill_sha256": sha256_bytes(skill.content),
        "license_sha256": sha256_bytes(license_text.content),
    }
    lock.write_text(json.dumps(lock_payload, indent=2) + "\n", encoding="utf-8")
    return {"downloaded": True, "source_id": f"{BASELINE_REPOSITORY}@{BASELINE_COMMIT}", "lock_path": str(lock)}


def public_baseline_status(agent_home: Path | None = None) -> dict[str, str | bool]:
    paths = baseline_paths(agent_home)
    if not paths["lock"].is_file():
        return {"status": "not_installed", "source_id": f"{BASELINE_REPOSITORY}@{BASELINE_COMMIT}", "required": False}
    try:
        payload = json.loads(paths["lock"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "invalid_lock", "source_id": f"{BASELINE_REPOSITORY}@{BASELINE_COMMIT}", "required": False}
    ok = (
        payload.get("repository") == BASELINE_REPOSITORY
        and payload.get("commit") == BASELINE_COMMIT
        and payload.get("license") == BASELINE_LICENSE
        and paths["skill"].is_file()
        and paths["license"].is_file()
        and payload.get("skill_sha256") == sha256_bytes(paths["skill"].read_bytes())
        and payload.get("license_sha256") == sha256_bytes(paths["license"].read_bytes())
    )
    return {
        "status": "ready" if ok else "integrity_error",
        "source_id": f"{BASELINE_REPOSITORY}@{BASELINE_COMMIT}",
        "required": False,
        "skill_sha256": str(payload.get("skill_sha256") or ""),
        "license_sha256": str(payload.get("license_sha256") or ""),
    }
