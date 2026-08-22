import json

import pytest

from job_application_agent.preflight import run_pre_application_check


def test_pre_application_check_reads_and_records_apply_learnings(tmp_path):
    learnings = tmp_path / "apply-learnings.md"
    learnings.write_text(
        "\n".join(
            [
                "# Application Safety Notes",
                "## Mandatory pre-application checks",
                "## Cover letters and research",
                "## Portal navigation",
                "## Known general portal patterns",
                "## Maintaining these notes",
            ]
        ),
        encoding="utf-8",
    )
    package_dir = tmp_path / "package"

    payload = run_pre_application_check(
        package_dir, "fill-form", learnings_path=learnings
    )

    assert payload["status"] == "passed"
    assert payload["action"] == "fill-form"
    assert payload["learnings_path"] == str(learnings.resolve())
    written = json.loads(
        (package_dir / "pre_application_check.json").read_text(encoding="utf-8")
    )
    assert written["sha256"] == payload["sha256"]
    assert (package_dir / "pre_application_check.md").exists()


def test_pre_application_check_rejects_missing_required_sections(tmp_path):
    learnings = tmp_path / "apply-learnings.md"
    learnings.write_text("# Apply Learnings\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Mandatory pre-application checks"):
        run_pre_application_check(tmp_path / "package", "fill-form", learnings)


def test_pre_application_check_rejects_missing_portal_navigation_section(tmp_path):
    learnings = tmp_path / "apply-learnings.md"
    learnings.write_text(
        "\n".join(
            [
                "# Application Safety Notes",
                "## Mandatory pre-application checks",
                "## Cover letters and research",
                "## Known general portal patterns",
                "## Maintaining these notes",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Portal navigation"):
        run_pre_application_check(tmp_path / "package", "fill-form", learnings)
