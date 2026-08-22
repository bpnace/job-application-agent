from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import ROOT
from .utils import write_json


APPLY_LEARNINGS_PATH = ROOT / "docs" / "apply-learnings.md"
REQUIRED_APPLY_LEARNINGS_MARKERS = (
    "## Mandatory pre-application checks",
    "## Cover letters and research",
    "## Portal navigation",
    "## Known general portal patterns",
    "## Maintaining these notes",
)


def run_pre_application_check(
    package_dir: Path,
    action: str,
    learnings_path: Path = APPLY_LEARNINGS_PATH,
) -> dict[str, Any]:
    package_dir = package_dir.expanduser().resolve()
    package_dir.mkdir(parents=True, exist_ok=True)
    learnings_path = learnings_path.expanduser().resolve()
    text = _read_apply_learnings(learnings_path)
    missing_markers = [
        marker for marker in REQUIRED_APPLY_LEARNINGS_MARKERS if marker not in text
    ]
    if missing_markers:
        raise RuntimeError(
            "Apply learnings are missing required pre-application sections: "
            + ", ".join(missing_markers)
        )
    payload = {
        "status": "passed",
        "action": action,
        "learnings_path": str(learnings_path),
        "read_at": datetime.now(UTC).isoformat(),
        "sha256": sha256(text.encode("utf-8")).hexdigest(),
        "bytes": len(text.encode("utf-8")),
        "required_markers": list(REQUIRED_APPLY_LEARNINGS_MARKERS),
        "note": (
            "Read docs/apply-learnings.md before this application action. "
            "Apply portal-specific rules before filling, uploading, submitting, or marking status."
        ),
    }
    write_json(package_dir / "pre_application_check.json", payload)
    (package_dir / "pre_application_check.md").write_text(
        "\n".join(
            [
                "# Pre-Application Check",
                "",
                f"- Status: {payload['status']}",
                f"- Action: {action}",
                f"- Apply learnings: {learnings_path}",
                f"- Read at: {payload['read_at']}",
                f"- SHA256: {payload['sha256']}",
                "",
                "Portal-specific apply learnings were loaded before this action.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def _read_apply_learnings(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Required pre-application learnings file is missing: {path}"
        )
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise RuntimeError(f"Required pre-application learnings file is empty: {path}")
    return text
