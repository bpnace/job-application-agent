from pathlib import Path

from job_application_agent.application import (
    build_form_fill_plan,
    extract_form_fields_from_html,
    infer_application_route,
)
from job_application_agent.models import (
    ApplicationFormField,
    CandidateProfile,
    JobListing,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _profile() -> CandidateProfile:
    return CandidateProfile(
        name="Test Candidate",
        email="candidate@example.com",
        location="Berlin",
        phone="+49 30 000000",
        address="Musterstraße 1, 10115 Berlin",
        street_address="Musterstraße 1",
        postal_code="10115",
        city="Berlin",
        country="Deutschland",
        github="https://github.com/example",
        linkedin="https://www.linkedin.com/in/example",
        summary="AI automation builder",
        core_skills=["n8n", "LLM", "React"],
        proof_points=["Built workflow automation"],
        cv_excerpt="CV",
        humanizer_excerpt="Humanizer",
    )


def test_infer_application_route_classifies_ats_and_job_boards():
    greenhouse = infer_application_route(
        "https://boards.greenhouse.io/example/jobs/123"
    )
    arbeitnow = infer_application_route("https://www.arbeitnow.com/jobs/example")
    email = infer_application_route("mailto:jobs@example.com")

    assert greenhouse.method == "ats_form"
    assert greenhouse.resume_upload == "likely"
    assert greenhouse.can_agent_fill
    assert arbeitnow.method == "job_board_listing"
    assert arbeitnow.platform == "arbeitnow"
    assert email.method == "email"
    assert email.resume_upload == "not_applicable"


def test_infer_application_route_keeps_unknown_external_company_url_pending():
    route = infer_application_route(
        "https://www.pandata.de/career/full-stack-developer/apply",
        source="public_job_board",
    )

    assert route.method == "external_form"
    assert route.can_agent_fill is False
    assert route.requires_human_approval


def test_extract_form_fields_classifies_common_application_fields():
    fields = extract_form_fields_from_html(
        (FIXTURES / "application_form_sample.html").read_text(encoding="utf-8")
    )

    classifications = {field.classification for field in fields}

    assert "full_name" in classifications
    assert "email" in classifications
    assert "resume_upload" in classifications
    assert "cover_letter" in classifications
    assert "consent" in classifications


def test_extract_form_fields_classifies_referral_before_full_name():
    html = """
    <form>
      <label for="referral">
        Have you been referred by an employee? If yes, please add full name
      </label>
      <input id="referral" name="custom_attribute_referral" required />
    </form>
    """

    fields = extract_form_fields_from_html(html)

    assert fields[0].classification == "referral"


def test_extract_form_fields_does_not_treat_contact_placeholders_as_cv_uploads():
    html = """
    <form>
      <section>
        <p>Upload CV later in the process</p>
        <label for="first">Max</label>
        <input id="first" name="firstName" placeholder="Max" />
        <label for="last">Mustermann</label>
        <input id="last" name="lastName" placeholder="Mustermann" />
        <label for="email">max@beispiel.de</label>
        <input id="email" name="email" type="email" placeholder="max@beispiel.de" />
      </section>
    </form>
    """

    fields = extract_form_fields_from_html(html)
    by_selector = {field.selector: field for field in fields}

    assert by_selector["#first"].classification == "first_name"
    assert by_selector["#last"].classification == "last_name"
    assert by_selector["#email"].classification == "email"
    assert all(field.classification != "resume_upload" for field in fields)


def test_extract_form_fields_classifies_generic_document_upload():
    html = """
    <form>
      <label for="doc">Dokument hochladen</label>
      <input id="doc" type="file" />
    </form>
    """

    fields = extract_form_fields_from_html(html)

    assert fields[0].classification == "document_upload"


def test_extract_form_fields_classifies_german_cover_letter_upload():
    html = """
    <form>
      <label for="cv">Lebenslauf hochladen</label>
      <input id="cv" type="file" />
      <label for="letter">Anschreiben hochladen</label>
      <input id="letter" type="file" />
    </form>
    """

    fields = extract_form_fields_from_html(html)
    by_selector = {field.selector: field for field in fields}

    assert by_selector["#cv"].classification == "resume_upload"
    assert by_selector["#letter"].classification == "cover_letter_upload"


def test_extract_form_fields_classifies_german_available_from():
    html = """
    <form>
      <label for="available">Verfügbar ab *</label>
      <input id="available" name="available_from" />
    </form>
    """

    fields = extract_form_fields_from_html(html)

    assert fields[0].classification == "availability"


def test_extract_form_fields_classifies_available_from():
    html = """
    <form>
      <label for="available">Available from *</label>
      <input id="available" name="available_from" />
    </form>
    """

    fields = extract_form_fields_from_html(html)

    assert fields[0].classification == "availability"


def test_extract_form_fields_prefers_source_over_linkedin_option():
    html = """
    <form>
      <label for="source">Where did you hear about this position?</label>
      <select id="source" name="source">
        <option>LinkedIn</option>
        <option>Google for Jobs</option>
      </select>
    </form>
    """

    fields = extract_form_fields_from_html(html)

    assert fields[0].classification == "source"


def test_build_form_fill_plan_uses_where_did_you_hear_source_answer(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Customer Success Manager",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="source">Where did you hear about this position?</label>
          <select id="source" name="source">
            <option>LinkedIn</option>
            <option>Google for Jobs</option>
          </select>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "opportunity_source": "Google for Jobs",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)

    assert plan.instructions[0].classification == "source"
    assert plan.instructions[0].action == "select"
    assert plan.instructions[0].value == "Google for Jobs"


def test_build_form_fill_plan_maps_personio_language_and_source_options(tmp_path):
    listing = JobListing(
        source="serpapi",
        source_url="https://example.com/jobs/example",
        title="Customer Success Manager",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="german">German language proficiency *</label>
          <select id="german" name="german">
            <option>Please select</option>
            <option>A1 - Beginner</option>
            <option>B1 - Intermediate</option>
            <option>C1 - Advanced</option>
            <option>C2 - Fluent / Native</option>
          </select>
          <label for="source">Where did you hear about us?</label>
          <select id="source" name="source">
            <option>Please select</option>
            <option>Company Website</option>
            <option>Social Media (e.g., LinkedIn)</option>
            <option>Job Board (e.g., Indeed, Glassdoor)</option>
          </select>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "german_proficiency": "Native/Fluent",
                "opportunity_source": "Google for Jobs",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_class = {
        instruction.classification: instruction for instruction in plan.instructions
    }

    assert by_class["language"].action == "select"
    assert by_class["language"].value == "C2 - Fluent / Native"
    assert by_class["source"].action == "select"
    assert by_class["source"].value == "Job Board (e.g., Indeed, Glassdoor)"


def test_extract_form_fields_uses_upload_helper_text_for_cover_letter():
    html = """
    <form>
      <label for="resume">CV / Resume *</label>
      <input id="resume" type="file" />
      <div>
        <label for="additional">Additional Documents</label>
        <p>Cover letter, certificates, portfolio - PDF, JPEG or PNG</p>
        <input id="additional" type="file" />
      </div>
    </form>
    """

    fields = extract_form_fields_from_html(html)
    by_selector = {field.selector: field for field in fields}

    assert by_selector["#resume"].classification == "resume_upload"
    assert by_selector["#additional"].classification == "cover_letter_upload"


def test_extract_form_fields_handles_groups_and_manual_review_fields():
    html = """
    <form>
      <fieldset>
        <legend>Work authorization required *</legend>
        <label><input type="radio" name="work_auth" value="yes" /> Yes</label>
        <label><input type="radio" name="work_auth" value="no" /> No</label>
      </fieldset>
      <label for="salary">Salary expectation</label>
      <input id="salary" name="salary" placeholder="EUR gross yearly" />
      <label for="language">Preferred language</label>
      <select id="language" name="language">
        <option>Deutsch</option>
        <option>English</option>
      </select>
    </form>
    """

    fields = extract_form_fields_from_html(html)
    by_class = {field.classification: field for field in fields}

    assert by_class["work_authorization"].required
    assert by_class["work_authorization"].options == ["Yes", "No"]
    assert by_class["work_authorization"].requires_manual_review
    assert by_class["salary"].requires_manual_review
    assert by_class["language"].options == ["Deutsch", "English"]
    assert "referral" not in by_class


def test_extract_form_fields_classifies_address_fields_and_country_radio():
    html = """
    <form>
      <label for="address">Postanschrift *</label>
      <textarea id="address" name="postalAddress"></textarea>
      <label for="street">Straße und Hausnummer</label>
      <input id="street" name="street" />
      <label for="zip">PLZ</label>
      <input id="zip" name="zip" />
      <label for="city">Stadt</label>
      <input id="city" name="city" />
      <label for="country">Land</label>
      <select id="country" name="country">
        <option>Österreich</option>
        <option>Deutschland</option>
      </select>
      <fieldset>
        <legend>Ich lebe zur Zeit in Deutschland *</legend>
        <label><input type="radio" name="livingInGermany" value="de" /> Ja, Deutschland</label>
        <label><input type="radio" name="livingInGermany" value="other" /> Nein</label>
      </fieldset>
    </form>
    """

    fields = extract_form_fields_from_html(html)
    by_selector = {field.selector: field for field in fields}

    assert by_selector["#address"].classification == "address"
    assert by_selector["#street"].classification == "street_address"
    assert by_selector["#zip"].classification == "postal_code"
    assert by_selector["#city"].classification == "city"
    assert by_selector["#country"].classification == "country"
    assert (
        by_selector[
            'input[type="radio"][name="livingInGermany"][value="de"]'
        ].classification
        == "country"
    )


def test_build_form_fill_plan_never_allows_submit(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://boards.greenhouse.io/example/jobs/123",
        title="AI Automation Engineer",
        company="Example",
        apply_url="https://boards.greenhouse.io/example/jobs/123",
    )
    fields = extract_form_fields_from_html(
        (FIXTURES / "application_form_sample.html").read_text(encoding="utf-8")
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        cover_letter_text="Cover letter text",
    )

    assert plan.route.method == "ats_form"
    assert plan.submit_allowed is False
    assert any(
        instruction.action == "fill" and instruction.value == "Test Candidate"
        for instruction in plan.instructions
    )
    assert any(
        instruction.action == "upload"
        and instruction.classification == "cover_letter_upload"
        for instruction in plan.instructions
    )
    assert all(instruction.frame_url == "" for instruction in plan.instructions)


def test_build_form_fill_plan_preserves_empty_live_field_list(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="AI Automation Engineer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=[],
        single_upload_verified=True,
    )

    assert plan.fields == []
    assert plan.instructions == []


def test_build_form_fill_plan_uses_separate_uploads_when_cover_upload_exists(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    cover_pdf = tmp_path / "cover_letter.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Example.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    cover_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="Full Stack Developer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">Lebenslauf hochladen</label>
          <input id="cv" type="file" />
          <label for="letter">Anschreiben hochladen</label>
          <input id="letter" type="file" />
          <label for="docs">Weitere Dokumente hochladen</label>
          <input id="docs" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#cv"].action == "upload"
    assert by_selector["#cv"].file_path == str(cv_pdf.resolve())
    assert by_selector["#letter"].action == "upload"
    assert by_selector["#letter"].file_path == str(cover_pdf)
    assert by_selector["#docs"].action == "skip"


def test_build_form_fill_plan_uses_cv_and_cover_for_generic_additional_upload(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    cover_pdf = tmp_path / "cover_letter.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Example.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    cover_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="AI Engineer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">Upload Lebenslauf</label>
          <input id="cv" type="file" />
          <label for="other">Upload Andere</label>
          <input id="other" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#cv"].action == "upload"
    assert by_selector["#cv"].file_path == str(cv_pdf.resolve())
    assert by_selector["#other"].action == "upload"
    assert by_selector["#other"].file_path == str(cover_pdf)


def test_build_form_fill_plan_skips_specific_unapproved_document_uploads(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    cover_pdf = tmp_path / "cover_letter.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    cover_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="AI Consultant",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">Upload CV</label>
          <input id="cv" type="file" />
          <label for="ref">Upload Employment reference</label>
          <input id="ref" type="file" />
          <label for="cert">Upload Certificate</label>
          <input id="cert" type="file" />
          <label for="other">Upload Other</label>
          <input id="other" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#cv"].action == "upload"
    assert by_selector["#ref"].action == "skip"
    assert by_selector["#cert"].action == "skip"
    assert by_selector["#other"].action == "upload"
    assert by_selector["#other"].file_path == str(cover_pdf)


def test_build_form_fill_plan_uses_combined_pdf_when_no_cover_upload_exists(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Example.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="Full Stack Developer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">Lebenslauf hochladen</label>
          <input id="cv" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )

    instruction = plan.instructions[0]
    assert instruction.classification == "resume_upload"
    assert instruction.action == "upload"
    assert instruction.file_path == str(combined_pdf.resolve())
    assert "No separate cover-letter upload field" in instruction.safety_note


def test_build_form_fill_plan_keeps_join_first_upload_cv_only(tmp_path, monkeypatch):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Join.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/example/jobs/ai-experience-designer",
        title="AI Experience Designer",
        company="Example",
        apply_url="https://join.com/companies/example/jobs/ai-experience-designer/apply/cv",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">CV / Resume</label>
          <input id="cv" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )

    instruction = plan.instructions[0]
    assert instruction.classification == "resume_upload"
    assert instruction.action == "upload"
    assert instruction.file_path == str(cv_pdf)
    assert instruction.file_path != str(combined_pdf.resolve())
    assert "CV-only" in instruction.safety_note
    assert "never upload the combined application PDF" in instruction.safety_note


def test_build_form_fill_plan_keeps_verified_arbeitnow_single_upload_combined(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Arbeitnow.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="arbeitnow",
        source_url="https://www.arbeitnow.com/jobs/123",
        title="AI Experience Designer",
        company="Example",
        apply_url="https://www.arbeitnow.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">CV / Resume</label>
          <input id="cv" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )

    instruction = plan.instructions[0]
    assert instruction.classification == "resume_upload"
    assert instruction.action == "upload"
    assert instruction.file_path == str(combined_pdf.resolve())


def test_build_form_fill_plan_blocks_join_generic_combined_upload(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Join.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="arbeitnow",
        source_url="https://www.arbeitnow.com/jobs/123",
        title="AI Experience Designer",
        company="Example",
        apply_url="https://join.com/companies/example/jobs/ai-experience-designer/apply/cv",
    )
    fields = [
        ApplicationFormField(
            label="Upload document",
            selector="#document",
            field_type="file",
            classification="document_upload",
        )
    ]

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )

    instruction = plan.instructions[0]
    assert instruction.action == "manual"
    assert instruction.file_path == ""
    assert "Do not upload the combined PDF" in instruction.safety_note


def test_build_form_fill_plan_does_not_upload_into_resume_route_radio(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Example.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="Full Stack Developer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label><input type="radio" name="is_cv" value="false" /> Link zum LinkedIn oder Xing Profil</label>
          <label><input type="radio" name="is_cv" value="true" /> Upload CV</label>
          <label for="cv">Upload CV (PDF)</label>
          <input id="cv" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert (
        by_selector['input[type="radio"][name="is_cv"][value="false"]'].action
        == "manual"
    )
    assert (
        by_selector['input[type="radio"][name="is_cv"][value="true"]'].action == "check"
    )
    assert by_selector["#cv"].action == "upload"
    assert by_selector["#cv"].file_path == str(combined_pdf.resolve())


def test_build_form_fill_plan_blocks_single_upload_until_flow_is_verified(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Example.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="Full Stack Developer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">Lebenslauf hochladen</label>
          <input id="cv" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(), listing, package_dir=tmp_path, fields=fields
    )

    instruction = plan.instructions[0]
    assert instruction.action == "manual"
    assert instruction.file_path == ""
    assert "Inspect the next form step" in instruction.safety_note
    assert "combined three-page application PDF" in instruction.safety_note


def test_build_form_fill_plan_blocks_single_upload_until_combined_pdf_exists(
    tmp_path, monkeypatch
):
    cv_pdf = tmp_path / "Test_Candidate_CV.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="Full Stack Developer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="cv">Lebenslauf hochladen</label>
          <input id="cv" type="file" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(),
        listing,
        package_dir=tmp_path,
        fields=fields,
        single_upload_verified=True,
    )

    instruction = plan.instructions[0]
    assert instruction.action == "manual"
    assert instruction.file_path == ""
    assert "combined three-page application PDF" in instruction.safety_note


def test_build_form_fill_plan_maps_address_values_and_germany_choice(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/123",
        title="HubSpot Developer",
        company="Example",
        apply_url="https://example.com/jobs/123",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="address">Postanschrift *</label>
          <textarea id="address" name="postalAddress"></textarea>
          <label for="zip">PLZ</label>
          <input id="zip" name="zip" />
          <label for="country">Land</label>
          <select id="country" name="country">
            <option>Österreich</option>
            <option>Deutschland</option>
          </select>
          <fieldset>
            <legend>Ich lebe zur Zeit in Deutschland *</legend>
            <label><input type="radio" name="livingInGermany" value="de" /> Ja, Deutschland</label>
            <label><input type="radio" name="livingInGermany" value="other" /> Nein</label>
          </fieldset>
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(), listing, package_dir=tmp_path, fields=fields
    )
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#address"].action == "fill"
    assert by_selector["#address"].value == "Musterstraße 1, 10115 Berlin"
    assert by_selector["#zip"].value == "10115"
    assert by_selector["#country"].action == "select"
    assert by_selector["#country"].value == "Deutschland"
    germany_radio = by_selector[
        'input[type="radio"][name="livingInGermany"][value="de"]'
    ]
    other_radio = by_selector[
        'input[type="radio"][name="livingInGermany"][value="other"]'
    ]
    assert germany_radio.action == "check"
    assert other_radio.action == "manual"


def test_build_form_fill_plan_skips_arbeitnow_honeypot_fields(tmp_path):
    listing = JobListing(
        source="arbeitnow_api",
        source_url="https://www.arbeitnow.com/jobs/example",
        title="Full-Stack Software Engineer",
        company="Example",
        apply_url="https://www.arbeitnow.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <input id="arbeitnow_name_vWdHWLoOTUfS7Fd1" name="arbeitnow_name_vWdHWLoOTUfS7Fd1" />
          <input name="arbeitnow_valid_from" />
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(), listing, package_dir=tmp_path, fields=fields
    )

    assert {instruction.classification for instruction in plan.instructions} == {
        "honeypot"
    }
    assert all(instruction.action == "skip" for instruction in plan.instructions)


def test_build_form_fill_plan_maps_application_source_select(tmp_path):
    listing = JobListing(
        source="stepstone_public",
        source_url="https://www.stepstone.de/jobs/ai-automation-engineer/in-deutschland",
        title="AI Automation Engineer",
        company="Example",
        apply_url="https://www.stepstone.de/stellenangebote--AI-Automation-Engineer--123-inline.html",
        apply_platform="stepstone.de",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="source">Wo bist du auf diese Stellenanzeige aufmerksam geworden?</label>
          <select id="source" name="source">
            <option>Glassdoor</option>
            <option>Google for Jobs</option>
            <option>LinkedIn</option>
            <option>Stepstone</option>
          </select>
        </form>
        """
    )

    plan = build_form_fill_plan(
        _profile(), listing, package_dir=tmp_path, fields=fields
    )

    assert plan.submit_allowed is False
    assert plan.instructions[0].classification == "source"
    assert plan.instructions[0].action == "select"
    assert plan.instructions[0].value == "Stepstone"


def test_build_form_fill_plan_uses_approved_standard_answers(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://www.arbeitnow.com/jobs/example",
        title="Product Engineer",
        company="Example",
        apply_url="https://www.arbeitnow.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="salary">Salary expectation</label>
          <input id="salary" name="salary" />
          <label for="start">Earliest possible starting date</label>
          <input id="start" name="start" />
          <label for="terms">
            <input id="terms" type="checkbox" name="terms" />
            I agree to the terms and conditions and confirm the privacy policy
          </label>
          <label for="gender">How would you describe your gender identity?</label>
          <select id="gender" name="gender">
            <option>Female</option>
            <option>Male</option>
            <option>Prefer not to say</option>
          </select>
          <label for="ethnicity">How would you describe your ethnic background?</label>
          <select id="ethnicity" name="ethnicity">
            <option>White</option>
            <option>Prefer not to say</option>
          </select>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "salary": "59000",
                "availability": "01.07.2026",
                "terms_consent": "Yes",
                "gender_identity": "Male",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#salary"].action == "fill"
    assert by_selector["#salary"].value == "59000"
    assert by_selector["#start"].value == "01.07.2026"
    assert by_selector["#terms"].action == "check"
    assert by_selector["#gender"].action == "select"
    assert by_selector["#gender"].value == "Male"
    assert by_selector["#ethnicity"].action == "manual"


def test_build_form_fill_plan_maps_current_situation_answer(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://partner.studysmarter.de/apply-form/?job_id=1",
        title="AI Automation Engineer",
        company="Ecoturn GmbH",
        apply_url="https://partner.studysmarter.de/apply-form/?job_id=1",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="situation">Wie ist deine aktuelle Situation?</label>
          <select id="situation" name="current_situation">
            <option>Ich bin aktuell beschäftigt</option>
            <option>Arbeitssuchend, nicht bei der BA gemeldet</option>
            <option>Arbeitssuchend, bei der BA gemeldet</option>
          </select>
          <label>
            <input type="radio" name="status" value="not_registered" />
            Arbeitssuchend, nicht bei der BA gemeldet
          </label>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "current_situation": "Arbeitssuchend, nicht bei der BA gemeldet",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#situation"].action == "select"
    assert (
        by_selector["#situation"].value == "Arbeitssuchend, nicht bei der BA gemeldet"
    )
    assert (
        by_selector['input[type="radio"][name="status"][value="not_registered"]'].action
        == "check"
    )


def test_build_form_fill_plan_uses_listed_salary_floor_plus_increment(tmp_path):
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="salary">Salary expectation</label>
          <input id="salary" name="salary" />
        </form>
        """
    )
    profile = _profile().model_copy(
        update={"standard_application_answers": {"salary": "59000"}}
    )

    for compensation in [
        "€60000 - €80000",
        "60.000-75.000 EUR",
        "60k-80k EUR",
    ]:
        listing = JobListing(
            source="fixture",
            source_url="https://example.com/jobs/example",
            title="Product Engineer",
            company="Example",
            apply_url="https://example.com/jobs/example",
            compensation=compensation,
        )

        plan = build_form_fill_plan(
            profile, listing, package_dir=tmp_path, fields=fields
        )
        by_selector = {
            instruction.selector: instruction for instruction in plan.instructions
        }

        assert by_selector["#salary"].action == "fill"
        assert by_selector["#salary"].value == "61000"


def test_build_form_fill_plan_keeps_default_salary_when_listed_floor_is_lower(
    tmp_path,
):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Product Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
        compensation="55000 - 70000 EUR",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="salary">Salary expectation</label>
          <input id="salary" name="salary" />
        </form>
        """
    )
    profile = _profile().model_copy(
        update={"standard_application_answers": {"salary": "59000"}}
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#salary"].action == "fill"
    assert by_selector["#salary"].value == "59000"


def test_build_form_fill_plan_selects_salary_range_option_for_numeric_answer(
    tmp_path,
):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Product Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
        compensation="€60000 - €80000",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="salary">Salary expectation</label>
          <select id="salary" name="salary">
            <option>40.001 - 60.000 EUR</option>
            <option>60.001 - 80.000 EUR</option>
          </select>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={"standard_application_answers": {"salary": "59000"}}
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#salary"].action == "select"
    assert by_selector["#salary"].value == "60.001 - 80.000 EUR"


def test_build_form_fill_plan_keeps_current_salary_separate_from_expectation(
    tmp_path,
):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Product Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
        compensation="€60000 - €80000",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="current_salary">What is your current salary?</label>
          <input id="current_salary" name="current_salary" />
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "salary": "59000",
                "current_salary": "56000",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#current_salary"].action == "fill"
    assert by_selector["#current_salary"].value == "56000"


def test_build_form_fill_plan_uses_german_proficiency_answer(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Customer Success Manager",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="german">What is your level of German? (speaking, writing, listening)</label>
          <input id="german" name="german" />
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "german_proficiency": "Native/Fluent",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)

    assert plan.instructions[0].classification == "language"
    assert plan.instructions[0].action == "fill"
    assert plan.instructions[0].value == "Native/Fluent"


def test_build_form_fill_plan_uses_approved_project_story_answers(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/frontend",
        title="Frontend Software Engineer",
        company="Example",
        apply_url="https://example.com/jobs/frontend",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="built">What was the last thing you built with TypeScript/JavaScript?</label>
          <textarea id="built" name="built"></textarea>
          <label for="admire">Who on your team do you admire and why?</label>
          <textarea id="admire" name="admire"></textarea>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "last_typescript_javascript_build": "Built a Next.js automation workspace.",
                "admired_teammate": "A teammate who documents decisions clearly.",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#built"].action == "fill"
    assert by_selector["#built"].value == "Built a Next.js automation workspace."
    assert by_selector["#admire"].action == "fill"
    assert by_selector["#admire"].value == "A teammate who documents decisions clearly."


def test_build_form_fill_plan_uses_cv_grounded_screening_answers(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Customer Success Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="backend">Wie viele Jahre Berufserfahrung hast du in der Backend-Entwicklung, und mit welchen drei Backend-Technologien hast du die meiste Erfahrung?</label>
          <input id="backend" name="backend" />
          <label for="frontend">Wie viele Jahre Berufserfahrung hast du in der Frontend-Entwicklung, und mit welchen drei Frontend-Technologien hast du die meiste Erfahrung?</label>
          <input id="frontend" name="frontend" />
          <label for="office">An wie vielen Tagen pro Woche möchtest du im Office arbeiten?</label>
          <input id="office" name="office" />
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "backend_experience": "5+ years; strongest backend technologies: Node.js, PostgreSQL/Supabase, REST APIs/Express.js.",
                "frontend_experience": "5+ years; strongest frontend technologies: React, Next.js, TypeScript.",
                "office_days_per_week": "max 3",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#backend"].value.startswith("5+ years")
    assert "Node.js" in by_selector["#backend"].value
    assert by_selector["#frontend"].value.startswith("5+ years")
    assert "React" in by_selector["#frontend"].value
    assert by_selector["#office"].value == "max 3"


def test_build_form_fill_plan_uses_language_and_general_experience_answers(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Full Stack AI Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = [
        ApplicationFormField(
            label="Language Skills: English* (required)",
            selector="#english",
            field_type="select",
            required=True,
            options=["Please select", "None", "C1", "Native / C2"],
            classification="language",
        ),
        ApplicationFormField(
            label="Years of Experience* (required)",
            selector="#years",
            field_type="select",
            required=True,
            options=["Please select", "3–4 years", "4–5 years", "5–6 years"],
            classification="unknown",
        ),
    ]
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "english_proficiency": "Native/Fluent",
                "general_experience_years": "5+ years",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#english"].action == "select"
    assert by_selector["#english"].value == "Native / C2"
    assert by_selector["#years"].action == "select"
    assert by_selector["#years"].value == "5–6 years"


def test_build_form_fill_plan_maps_language_and_keeps_residency_manual(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Solution Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = [
        ApplicationFormField(
            label="Do you have full professional proficiency in German?",
            selector="#german",
            field_type="select",
            options=["Please select", "Yes", "No"],
            classification="language",
        ),
        ApplicationFormField(
            label="Are you based in the required country?",
            selector="#residency",
            field_type="select",
            options=["Please select", "Yes", "No"],
            classification="country",
        ),
    ]
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "german_proficiency": "Native/Fluent",
                "current_location": "Example City, Example Country",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert by_selector["#german"].action == "select"
    assert by_selector["#german"].value == "Yes"
    assert by_selector["#residency"].action == "manual"


def test_build_form_fill_plan_keeps_combined_residency_question_manual(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Technical Project Manager",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = [
        ApplicationFormField(
            label="Wohnst du bereits in Deutschland oder Spanien oder bist umzugsbereit?",
            selector="#residence",
            field_type="select",
            required=True,
            options=[
                "Bitte auswählen",
                "Ja, ich lebe bereits in Deutschland/Spanien",
                "Ja, ich würde nach Deutschland/Spanien umziehen",
                "Nein",
            ],
            classification="country",
        )
    ]
    profile = _profile().model_copy(
        update={"standard_application_answers": {"current_location": "Example City"}}
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)

    assert plan.instructions[0].action == "manual"


def test_build_form_fill_plan_uses_cv_grounded_energy_answer(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="Technical Project Manager",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = [
        ApplicationFormField(
            label="Hast du Kenntnisse in der Energiebranche, insbesondere im Bereich elektrischer Stromnetze?",
            selector="#energy",
            field_type="textarea",
            required=True,
            classification="unknown",
        )
    ]
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "energy_industry_knowledge": "No deep grid-domain experience yet; strong overlap in data integration."
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)

    assert plan.instructions[0].action == "fill"
    assert (
        plan.instructions[0].value
        == "No deep grid-domain experience yet; strong overlap in data integration."
    )


def test_build_form_fill_plan_maps_online_search_source_select(tmp_path):
    listing = JobListing(
        source="serpapi",
        source_url="https://example.com/jobs/example",
        title="Solutions Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = [
        ApplicationFormField(
            label="Where did you hear about this position?",
            selector="#source",
            field_type="select",
            required=True,
            options=[
                "Please select",
                "LinkedIn post",
                "Online Search / Research",
                "Other",
            ],
            classification="source",
        )
    ]
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {"opportunity_source": "Google for Jobs"}
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)

    assert plan.instructions[0].action == "select"
    assert plan.instructions[0].value == "Online Search / Research"


def test_extract_form_fields_classifies_how_found_german_as_source():
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="found">Wie hast du uns gefunden?</label>
          <select id="found" name="custom_attribute_123">
            <option>Google</option>
            <option>LinkedIn</option>
          </select>
        </form>
        """
    )

    assert fields[0].classification == "source"


def test_extract_form_fields_classifies_how_learned_english_as_source():
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="found">How did you learn about EverReal?</label>
          <input id="found" name="custom_attribute_456" />
        </form>
        """
    )

    assert fields[0].classification == "source"


def test_build_form_fill_plan_uses_workable_screening_answers(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://apply.workable.com/example",
        title="AI Automation Specialist",
        company="Example",
        apply_url="https://apply.workable.com/example",
        compensation="€60000 - €80000",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <fieldset>
            <legend>Which of the following timezones are you able to work in?</legend>
            <label><input type="checkbox" name="tz_us" value="us" /> US</label>
            <label><input type="checkbox" name="tz_europe" value="europe" /> Europe</label>
            <label><input type="checkbox" name="tz_no" value="no" /> No</label>
          </fieldset>
          <fieldset>
            <legend>Are you able to work in Eastern Time and Dubai Time?</legend>
            <label><input type="radio" name="et_dubai" value="true" /> YES</label>
            <label><input type="radio" name="et_dubai" value="false" /> NO</label>
          </fieldset>
          <label for="usd">What are your monthly salary expectations in USD?</label>
          <input id="usd" name="usd" />
          <label for="prev">What were you making in your previous role as a monthly salary?</label>
          <input id="prev" name="prev" />
          <label for="product">What product or technology do you think should already exist by now?</label>
          <textarea id="product" name="product"></textarea>
        </form>
        """
    )
    product_answer = "Reliable personal automation still feels unfinished."
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "timezones": "US, UK, Europe, Australia, Canada if remote",
                "eastern_dubai_time": "Yes, if remote",
                "salary_usd_monthly": "5500",
                "previous_salary_usd_monthly": "N/A - not disclosed",
                "limetax_product_answer": product_answer,
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)
    by_selector = {
        instruction.selector: instruction for instruction in plan.instructions
    }

    assert (
        by_selector['input[type="checkbox"][name="tz_us"][value="us"]'].action
        == "check"
    )
    assert (
        by_selector['input[type="checkbox"][name="tz_europe"][value="europe"]'].action
        == "check"
    )
    assert (
        by_selector['input[type="checkbox"][name="tz_no"][value="no"]'].action
        == "manual"
    )
    assert (
        by_selector['input[type="radio"][name="et_dubai"][value="true"]'].action
        == "check"
    )
    assert by_selector["#usd"].value == "5500"
    assert by_selector["#prev"].value == "N/A - not disclosed"
    assert by_selector["#product"].value == product_answer


def test_build_form_fill_plan_keeps_residence_relocation_choice_manual(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="AI Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="living">Are you currently living in City A or City B, or would you be open to relocating for this role?</label>
          <select id="living" name="living">
            <option>No</option>
            <option>City A, Example Country</option>
            <option>City B, Example Country</option>
          </select>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "current_location": "City A, Example Country",
                "relocation": "No",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)

    instruction = plan.instructions[0]
    assert instruction.action == "manual"


def test_build_form_fill_plan_keeps_residence_relocation_yes_no_manual(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/example",
        title="AI Engineer",
        company="Example",
        apply_url="https://example.com/jobs/example",
    )
    fields = extract_form_fields_from_html(
        """
        <form>
          <label for="living">Are you currently living in City A or City B, or would you be open to relocating for this role?</label>
          <select id="living" name="living">
            <option>Please select</option>
            <option>yes</option>
            <option>no</option>
          </select>
        </form>
        """
    )
    profile = _profile().model_copy(
        update={
            "standard_application_answers": {
                "current_location": "City A, Example Country",
                "relocation": "No",
            }
        }
    )

    plan = build_form_fill_plan(profile, listing, package_dir=tmp_path, fields=fields)

    instruction = plan.instructions[0]
    assert instruction.action == "manual"


def test_build_form_fill_plan_submit_requires_explicit_flag(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://boards.greenhouse.io/example/jobs/123",
        title="AI Automation Engineer",
        company="Example",
        apply_url="https://boards.greenhouse.io/example/jobs/123",
    )

    plan = build_form_fill_plan(
        _profile(), listing, package_dir=tmp_path, submit_allowed=True
    )

    assert plan.submit_allowed is True
