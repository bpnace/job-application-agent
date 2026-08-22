from job_application_agent.humanizer import (
    check_cover_letter_quality,
    rewrite_cover_letter_for_humanizer,
)
from job_application_agent.models import CandidateProfile


def _profile(policy_path=None) -> CandidateProfile:
    return CandidateProfile(
        name="Test Candidate",
        email="candidate@example.com",
        location="Berlin",
        github="https://github.com/example",
        linkedin="https://www.linkedin.com/in/example",
        summary="Developer",
        core_skills=["n8n"],
        proof_points=[],
        cv_excerpt="CV",
        humanizer_excerpt="Humanizer rules loaded",
        humanizer_policy_path=str(policy_path or ""),
    )


def _policy(tmp_path):
    path = tmp_path / "private.de.md"
    path.write_text(
        "---\nversion: 1\nlanguage: de\nbanned_terms: [PRIVATE_TEST_BANNED]\nbanned_patterns: []\nreplacements: {PRIVATE_TEST_REPLACE: Private test replacement}\nforbid_colons: true\n---\n\nPrivate test instructions.\n",
        encoding="utf-8",
    )
    return path


def test_humanizer_quality_uses_private_policy_terms_and_colons(tmp_path):
    pdf = tmp_path / "one-page.pdf"
    pdf.write_bytes(b"%PDF-1.4 /Type /Pages /Type /Page")
    markdown = """# Bewerbung als Developer

Sehr geehrtes Team,

PRIVATE_TEST_REPLACE Text mit PRIVATE_TEST_BANNED: Er bleibt synthetisch.

GitHub https://github.com/example
LinkedIn https://www.linkedin.com/in/example
"""

    quality = check_cover_letter_quality(markdown, _profile(_policy(tmp_path)), pdf)

    assert quality.passed is False
    assert quality.checks["no_banned_humanizer_terms"] is False
    assert quality.checks["no_colon_prose"] is False


def test_humanizer_quality_accepts_compact_one_page_letter(tmp_path):
    pdf = tmp_path / "one-page.pdf"
    pdf.write_bytes(b"%PDF-1.4 /Type /Pages /Type /Page")
    markdown = """# Bewerbung als Frontend Engineer

Sehr geehrtes Team,

Ihre Rolle passt gut zu meiner Arbeit mit React, Next.js, PostgreSQL und n8n. Ich baue Weboberflächen und Automatisierungen, die nah an echten Abläufen bleiben.

In eigenen Projekten habe ich praktische Automatisierungen und Webanwendungen umgesetzt. Konkrete Beispiele stehen im beigefügten Lebenslauf.

Mich interessiert die Verbindung aus sauberer Umsetzung, Prozessblick und Kundennähe. Genau dort kann ich schnell produktiv werden.

Ich mag Rollen, in denen man nicht nur über Automatisierung spricht, sondern sie an realen Übergaben, Formularen, Daten und kleinen Fehlerfällen festmacht. Das ist oft weniger Show, aber deutlich näher an produktiver Arbeit. Dabei dokumentiere ich Entscheidungen nachvollziehbar und stimme mich früh mit Beteiligten ab.

Öffentliche Referenzen

GitHub https://github.com/example
LinkedIn https://www.linkedin.com/in/example

Viele Grüße
Test Candidate
"""

    quality = check_cover_letter_quality(markdown, _profile(_policy(tmp_path)), pdf)

    assert quality.passed is True
    assert quality.page_count == 1


def test_humanizer_rewrite_repairs_mechanical_style_failures(tmp_path):
    pdf = tmp_path / "one-page.pdf"
    pdf.write_bytes(b"%PDF-1.4 /Type /Pages /Type /Page")
    markdown = """# Bewerbung als Frontend Engineer

Sehr geehrtes Team,

Dieser Einstieg ist klar: Ihre Rolle passt gut zu meiner Arbeit mit React, Next.js, PostgreSQL und n8n. Ich baue Weboberflächen und Automatisierungen, die nah an echten Abläufen bleiben.

In eigenen Projekten habe ich praktische Automatisierungen und Webanwendungen umgesetzt. Konkrete Beispiele stehen im beigefügten Lebenslauf.

Mich interessiert die Verbindung aus sauberer Umsetzung, Prozessblick und Kundennähe. Genau dort kann ich schnell produktiv werden.

Ich mag Rollen, in denen man nicht nur über Automatisierung spricht, sondern sie an realen Übergaben, Formularen, Daten und kleinen Fehlerfällen festmacht. Das ist oft weniger Show, aber deutlich näher an produktiver Arbeit. Dabei dokumentiere ich Entscheidungen nachvollziehbar und stimme mich früh mit Beteiligten ab.

Öffentliche Referenzen

GitHub: https://github.com/example
LinkedIn: https://www.linkedin.com/in/example

Viele Grüße
Test Candidate
"""

    profile = _profile(_policy(tmp_path))
    repaired = rewrite_cover_letter_for_humanizer(markdown, profile)
    quality = check_cover_letter_quality(repaired, profile, pdf)

    assert "PRIVATE_TEST_REPLACE" not in repaired
    assert "PRIVATE_TEST_BANNED:" not in repaired
    assert "GitHub:" not in repaired
    assert quality.passed is True
