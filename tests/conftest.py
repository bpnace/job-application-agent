import pytest
import yaml


@pytest.fixture(autouse=True)
def isolate_canonical_anschreiben_dir(tmp_path, monkeypatch):
    agent_home = tmp_path / ".job-agent"
    monkeypatch.setenv(
        "JOB_AGENT_ANSCHREIBEN_DIR", str(tmp_path / "Lebenslauf" / "Anschreiben")
    )
    cv_path = tmp_path / "Test_Candidate_CV_26.pdf"
    cv_path.write_bytes(b"%PDF-1.4\n% test cv\n")
    cv_text_path = tmp_path / "cv.txt"
    cv_text_path.write_text("Test CV", encoding="utf-8")
    humanizer_path = agent_home / "humanizer" / "private.de.md"
    humanizer_path.parent.mkdir(parents=True, exist_ok=True)
    humanizer_path.write_text(
        "---\nversion: 1\nlanguage: de\nbanned_terms: []\nbanned_patterns: []\nreplacements: {}\n---\n\nPrivate test policy.\n",
        encoding="utf-8",
    )
    profile_path = agent_home / "candidate.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        yaml.safe_dump(
            {
                "profile": {
                    "name": "Test Candidate",
                    "email": "candidate@example.test",
                    "location": "Berlin",
                    "phone": "+49 30 000000",
                    "address": "Test Street 1, 10115 Berlin",
                    "street_address": "Test Street 1",
                    "postal_code": "10115",
                    "city": "Berlin",
                    "country": "Germany",
                    "github": "https://github.com/example",
                    "linkedin": "https://www.linkedin.com/in/example",
                    "summary": "Test developer.",
                    "core_skills": ["Python"],
                    "proof_points": ["Test evidence."],
                    "standard_application_answers": {},
                },
                "documents": {
                    "cv_text_path": str(cv_text_path),
                    "cv_pdf_path": str(cv_path),
                },
                "humanizer": {"private_policy_path": str(humanizer_path)},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_AGENT_HOME", str(agent_home))
    monkeypatch.setenv("JOB_AGENT_PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_path))
    monkeypatch.setenv("JOB_AGENT_CV_TEXT_PATH", str(cv_text_path))
    monkeypatch.setenv("JOB_AGENT_HUMANIZER_PATH", str(humanizer_path))
