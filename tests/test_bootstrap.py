from __future__ import annotations

import json

import httpx
import yaml

from job_application_agent import bootstrap
from job_application_agent.cli import main
from job_application_agent import humanizer_policy


def test_init_creates_ignored_layout_without_replacing_profile(tmp_path):
    first = bootstrap.initialize_local_state(agent_home=tmp_path / "agent-home")
    profile_path = tmp_path / "agent-home" / "candidate.yaml"
    profile_path.write_text("profile: {}\ndocuments: {}\n", encoding="utf-8")

    second = bootstrap.initialize_local_state(agent_home=tmp_path / "agent-home")

    assert first["profile_created"] is True
    assert second["profile_created"] is False
    assert profile_path.read_text(encoding="utf-8") == "profile: {}\ndocuments: {}\n"
    assert (tmp_path / "agent-home" / "approvals").is_dir()
    assert (tmp_path / "agent-home" / "humanizer" / "private.de.md").is_file()


def test_interactive_init_creates_private_candidate_and_search_profiles(tmp_path):
    answers = iter(
        [
            "Example User",
            "user@example.test",
            "",
            "Deutschland",
            "Köln",
            "Product Manager, Product Owner",
            "B2B, SaaS",
            "Praktikum, Werkstudent",
            "Example Employer",
            "ja",
            "Produktmanager mit Erfahrung in digitalen Produkten.",
            "Discovery, Stakeholdermanagement",
            "Produktlaunch; Nutzerinterviews",
            "",
            "",
            "ja",
            "documents/cv.txt",
            "documents/cv.pdf",
        ]
    )

    result = bootstrap.initialize_local_state(
        agent_home=tmp_path / "agent-home",
        interactive=True,
        input_fn=lambda _prompt: next(answers),
    )
    profile = yaml.safe_load(
        (tmp_path / "agent-home" / "candidate.yaml").read_text(encoding="utf-8")
    )
    search = yaml.safe_load(
        (tmp_path / "agent-home" / "search_profile.yaml").read_text(encoding="utf-8")
    )

    assert result["interactive_setup"] == "completed"
    assert profile["profile"]["name"] == "Example User"
    assert search["search"]["target_roles"] == ["Product Manager", "Product Owner"]
    assert search["search"]["hard_exclusions"] == ["Praktikum", "Werkstudent"]
    assert search["sources"]["arbeitsagentur"]["queries"] == [
        "Product Manager",
        "Product Owner",
    ]
    assert any(
        "Product+Manager" in url for url in search["sources"]["linkedin"]["urls"]
    )


def test_interactive_init_without_cv_creates_a_classic_basic_resume(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_AGENT_CV_PDF_PATH", raising=False)
    monkeypatch.delenv("JOB_AGENT_CV_TEXT_PATH", raising=False)
    answers = iter(
        [
            "Example User",
            "user@example.test",
            "",
            "Deutschland",
            "Köln",
            "Product Manager",
            "B2B, SaaS",
            "Praktikum, Werkstudent",
            "",
            "ja",
            "Produktmanager mit Erfahrung in digitalen Produkten.",
            "Discovery, Stakeholdermanagement",
            "Produktlaunch; Nutzerinterviews",
            "",
            "",
            "nein",
            "Product Manager",
            "",
            "ja",
            "Product Manager",
            "Example GmbH",
            "2023 - heute",
            "Köln",
            "Produktlaunch verantwortet; Nutzerinterviews etabliert",
            "nein",
            "nein",
            "Deutsch: Muttersprache; Englisch: C1",
            "UX Certificate | Example Institute | 2025",
        ]
    )

    result = bootstrap.initialize_local_state(
        agent_home=tmp_path / "agent-home",
        interactive=True,
        input_fn=lambda _prompt: next(answers),
    )
    profile = yaml.safe_load(
        (tmp_path / "agent-home" / "candidate.yaml").read_text(encoding="utf-8")
    )

    generated_cv_path = result["generated_cv_path"]
    assert isinstance(generated_cv_path, str)
    assert generated_cv_path.endswith("Lebenslauf_User.pdf")
    assert (tmp_path / "agent-home" / "documents" / "Lebenslauf_User.pdf").is_file()
    assert profile["documents"]["cv_pdf_path"] == "documents/Lebenslauf_User.pdf"
    assert profile["resume"]["experience"][0]["employer"] == "Example GmbH"
    assert profile["resume"]["languages"] == [
        {"language": "Deutsch", "level": "Muttersprache"},
        {"language": "Englisch", "level": "C1"},
    ]


def test_doctor_reports_readiness_without_profile_values(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(bootstrap, "_check_browser", lambda: (True, "Fixture browser ready."))

    # The production doctor always uses ignored local state.  Build that state
    # explicitly so this test does not depend on a developer's real profile.
    home = tmp_path / "agent-home"
    monkeypatch.setenv("JOB_AGENT_HOME", str(home))
    bootstrap.initialize_local_state(agent_home=home)
    candidate = yaml.safe_load((home / "candidate.yaml").read_text(encoding="utf-8"))
    candidate["profile"].update(
        {
            "name": "Test Candidate",
            "email": "candidate@example.test",
            "location": "Test City, Germany",
            "summary": "Private test profile.",
        }
    )
    candidate["documents"] = {
        "cv_text_path": "documents/cv.txt",
        "cv_pdf_path": "documents/cv.pdf",
    }
    (home / "candidate.yaml").write_text(
        yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8"
    )
    (home / "documents" / "cv.txt").write_text("Test CV", encoding="utf-8")
    (home / "documents" / "cv.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    search = yaml.safe_load((home / "search_profile.yaml").read_text(encoding="utf-8"))
    search["search"].update(
        {"profile_configured": True, "target_roles": ["Test Role"]}
    )
    (home / "search_profile.yaml").write_text(
        yaml.safe_dump(search, sort_keys=False), encoding="utf-8"
    )

    exit_code = main(["doctor"])

    output = capsys.readouterr().out
    report = json.loads(output)
    assert exit_code == 0
    assert report["ready"] is True
    assert report["checks"]["browser"]["ok"] is True
    assert "candidate@example.test" not in output
    assert "Private test policy" not in output


def test_humanizer_bootstrap_pins_and_locks_public_baseline(monkeypatch, tmp_path):
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            _ = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            calls.append(url)
            text = "# Humanizer\n" if url.endswith("SKILL.md") else "MIT License\n"
            return httpx.Response(200, text=text, request=httpx.Request("GET", url))

    monkeypatch.setattr(humanizer_policy.httpx, "Client", FakeClient)

    first = humanizer_policy.bootstrap_public_baseline(tmp_path / "agent-home")
    second = humanizer_policy.bootstrap_public_baseline(tmp_path / "agent-home")
    lock = json.loads(
        (tmp_path / "agent-home" / "humanizer" / "public" / "baseline.lock.json").read_text()
    )

    assert first["downloaded"] is True
    assert second["downloaded"] is False
    assert len(calls) == 2
    assert lock["commit"] == humanizer_policy.BASELINE_COMMIT
    assert lock["license"] == "MIT"
    assert len(lock["skill_sha256"]) == 64
