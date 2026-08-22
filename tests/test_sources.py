from pathlib import Path

import httpx
import pytest

from job_application_agent.sources import (
    classify_remote_type,
    FREELANCERMAP_HOSTS,
    ArbeitsagenturApiSource,
    ArbeitsagenturPublicSearchSource,
    detect_language,
    fetch_text,
    parse_arbeitsagentur_json,
    parse_arbeitsagentur_search_html,
    parse_arbeitnow_json,
    parse_freelancermap_html,
    parse_jobposting_jsonld,
    parse_linkedin_jobs_html,
    parse_personio_xml,
    parse_public_job_board_html,
    parse_remoteok_json,
    parse_remotive_json,
    parse_scrapling_job_board_html,
    parse_serpapi_google_results,
    parse_stepstone_html,
    PublicJobBoardSource,
    PublicSearchSource,
    ScraplingPublicJobBoardSource,
    validate_public_source_url,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_parse_personio_xml_extracts_positions():
    listings = parse_personio_xml(
        (FIXTURES / "personio_sample.xml").read_text(), "fixture://personio"
    )
    assert len(listings) == 2
    assert listings[0].title == "Junior AI Automation Engineer"
    assert "n8n" in listings[0].description


def test_parse_personio_xml_never_uses_office_as_company():
    listings = parse_personio_xml(
        """<?xml version="1.0" encoding="UTF-8"?>
        <workzag-jobs><position>
          <name>AI Automation Engineer</name>
          <office>Berlin</office>
          <subcompany>Example Automation GmbH</subcompany>
        </position></workzag-jobs>""",
        "https://example-automation.jobs.personio.de/xml",
    )

    assert listings[0].company == "Example Automation GmbH"
    assert listings[0].location == "Berlin"


def test_parse_freelancermap_cards():
    listings = parse_freelancermap_html(
        (FIXTURES / "freelancermap_sample.html").read_text(),
        "https://www.freelancermap.com/projects/remote",
    )
    assert len(listings) == 2
    assert listings[0].company == "Automation Startup GmbH"
    assert listings[0].apply_url.endswith("/project/ai-workflow-developer")


def test_parse_jobposting_jsonld():
    listings = parse_jobposting_jsonld(
        (FIXTURES / "jobposting_sample.html").read_text(), "fixture://jobposting"
    )
    assert len(listings) == 1
    assert listings[0].title == "Fullstack Developer AI Tools"
    assert listings[0].company == "SaaS Tools GmbH"


def test_parse_public_job_board_links():
    html = """
    <html>
      <body>
        <a href="/jobs/frontend-ai-engineer">Frontend AI Engineer</a>
        <a href="/jobs-with-salary">Jobs with Salary</a>
        <a href="/jobs/companies">Companies Hiring in Germany</a>
        <a href="/jobs/locations/aachen">Aachen</a>
        <a href="/jobs/android">Android</a>
        <a href="/jobs/backend">Backend</a>
        <a href="/skill-areas/fullstack/">Fullstack</a>
        <a href="/stellenangebote/ausbildung">Ausbildung</a>
        <a href="/tag/jobs">Jobs</a>
        <a href="/jobs">All jobs</a>
        <a href="/privacy">Privacy</a>
      </body>
    </html>
    """

    listings = parse_public_job_board_html(
        html, "https://jobs.example.com/jobs", "Example Jobs"
    )

    assert len(listings) == 1
    assert listings[0].source == "public_job_board"
    assert listings[0].title == "Frontend AI Engineer"
    assert listings[0].apply_url == "https://jobs.example.com/jobs/frontend-ai-engineer"
    assert listings[0].application_method == "job_board_listing"


def test_public_job_board_source_uses_search_urls_and_skips_disabled(monkeypatch):
    calls: list[str] = []

    def fake_fetch_text(url: str, **_: object) -> tuple[str, str | None]:
        calls.append(url)
        suffix = "frontend" if "Frontend" in url else "react"
        return (
            f"""
            <html><body>
              <a href="/jobs/{suffix}-engineer">{suffix.title()} Engineer</a>
            </body></html>
            """,
            None,
        )

    monkeypatch.setattr("job_application_agent.sources.fetch_text", fake_fetch_text)

    result = PublicJobBoardSource(
        [
            {
                "name": "Example Jobs",
                "url": "https://jobs.example.com/jobs",
                "search_urls": [
                    "https://jobs.example.com/jobs?search=Frontend",
                    "https://jobs.example.com/jobs?search=React",
                ],
                "allowed_hosts": ["jobs.example.com"],
            },
            {
                "name": "Blocked Jobs",
                "enabled": False,
                "url": "https://blocked.example.com/jobs",
            },
        ],
        per_board_limit=2,
    ).collect(max_candidates=5, host_delay_seconds=0)

    assert calls == [
        "https://jobs.example.com/jobs?search=Frontend",
        "https://jobs.example.com/jobs?search=React",
    ]
    assert [listing.title for listing in result.listings] == [
        "Frontend Engineer",
        "React Engineer",
    ]
    assert (
        result.listings[0].source_url == "https://jobs.example.com/jobs?search=Frontend"
    )
    assert "Skipped disabled boards: Blocked Jobs" in result.health.message


def test_public_job_board_source_continues_after_403(monkeypatch):
    def fake_fetch_text(url: str, **_: object) -> tuple[str, str | None]:
        if "blocked.example.com" in url:
            return "", "HTTPStatusError: 403 Forbidden"
        return (
            """
            <html><body>
              <a href="/jobs/frontend-ai-engineer">Frontend AI Engineer</a>
            </body></html>
            """,
            None,
        )

    monkeypatch.setattr("job_application_agent.sources.fetch_text", fake_fetch_text)

    result = PublicJobBoardSource(
        [
            {"name": "Blocked Jobs", "url": "https://blocked.example.com/jobs"},
            {"name": "Good Jobs", "url": "https://jobs.example.com/jobs"},
        ],
        per_board_limit=2,
    ).collect(max_candidates=5, host_delay_seconds=0)

    assert result.health.status == "available"
    assert len(result.listings) == 1
    assert result.listings[0].title == "Frontend AI Engineer"
    assert "Blocked Jobs" in result.health.message
    assert "403 Forbidden" in result.health.message


def test_parse_stepstone_links_extracts_single_job_pages():
    html = """
    <html>
      <body>
        <article data-at="job-item">
          <a href="/stellenangebote--AI-Automation-Engineer-Berlin-Example-GmbH--123456-inline.html">
            <h2>AI Automation Engineer (m/w/d)</h2>
          </a>
          <span data-at="job-item-company-name">Example GmbH</span>
          <span data-at="job-item-location">Berlin / Remote</span>
        </article>
        <a href="/jobs/ai-automation-engineer/in-deutschland">AI Automation Engineer Jobs</a>
      </body>
    </html>
    """

    listings = parse_stepstone_html(
        html, "https://www.stepstone.de/jobs/ai-automation-engineer/in-deutschland"
    )

    assert len(listings) == 1
    assert listings[0].source == "stepstone_public"
    assert listings[0].title == "AI Automation Engineer (m/w/d)"
    assert listings[0].company == "Example GmbH"
    assert listings[0].remote_type == "remote"
    assert listings[0].application_method == "job_board_listing"
    assert listings[0].apply_platform == "stepstone.de"


def test_parse_stepstone_hybrid_does_not_mark_remote():
    html = """
    <html>
      <body>
        <article data-at="job-item">
          <a href="/stellenangebote--Frontend-Engineer-Hamburg-Example-GmbH--123456-inline.html">
            <h2>Frontend Engineer - hybrid</h2>
          </a>
          <span data-at="job-item-company-name">Example GmbH</span>
          <span data-at="job-item-location">Hamburg, Hybrid</span>
        </article>
      </body>
    </html>
    """

    listings = parse_stepstone_html(
        html, "https://www.stepstone.de/jobs/frontend-engineer/in-deutschland"
    )

    assert len(listings) == 1
    assert listings[0].remote_type == "hybrid"


def test_classify_remote_type_treats_homeoffice_moeglich_as_hybrid():
    assert (
        classify_remote_type("Hamburg office with Homeoffice möglich after onboarding")
        == "hybrid"
    )
    assert classify_remote_type("Remote option in Munich") == "hybrid"


def test_parse_linkedin_jobs_extracts_public_job_views():
    html = """
    <html>
      <body>
        <li class="base-search-card">
          <a href="https://www.linkedin.com/jobs/view/123456789?trk=public_jobs_jserp-result_search-card">
            <h3 class="base-search-card__title">Workflow Automation Developer</h3>
          </a>
          <h4 class="base-search-card__subtitle">Automation GmbH</h4>
          <span class="job-search-card__location">Germany Remote</span>
        </li>
        <a href="https://www.linkedin.com/jobs/search/?keywords=AI">Search results</a>
        <a href="https://www.linkedin.com/login">Sign in</a>
      </body>
    </html>
    """

    listings = parse_linkedin_jobs_html(
        html, "https://www.linkedin.com/jobs/search/?keywords=Workflow"
    )

    assert len(listings) == 1
    assert listings[0].source == "linkedin_public"
    assert listings[0].title == "Workflow Automation Developer"
    assert listings[0].company == "Automation GmbH"
    assert listings[0].remote_type == "remote"
    assert listings[0].application_method == "linkedin_job"
    assert listings[0].apply_url == "https://www.linkedin.com/jobs/view/123456789"


def test_parse_scrapling_job_board_links():
    pytest.importorskip("scrapling")
    html = """
    <html>
      <body>
        <article><a href="/jobs/workflow-automation-engineer">Workflow Automation Engineer</a></article>
        <a href="/jobs-with-relocation">Jobs with Relocation Package</a>
        <a href="/jobs/companies">Companies Hiring in Germany</a>
        <a href="/jobs/locations/aachen">Aachen</a>
        <a href="/jobs/android">Android</a>
        <a href="/jobs/backend">Backend</a>
        <a href="/skill-areas/fullstack/">Fullstack</a>
        <a href="/stellenangebote/ausbildung">Ausbildung</a>
        <a href="/tag/jobs">Jobs</a>
        <a href="/privacy">Privacy</a>
      </body>
    </html>
    """

    listings = parse_scrapling_job_board_html(
        html, "https://jobs.example.com/jobs", "Example Jobs"
    )

    assert len(listings) == 1
    assert listings[0].source == "scrapling_public_job_board"
    assert listings[0].title == "Workflow Automation Engineer"
    assert (
        listings[0].apply_url
        == "https://jobs.example.com/jobs/workflow-automation-engineer"
    )


def test_scrapling_source_is_disabled_without_optional_dependency(monkeypatch):
    monkeypatch.setattr(
        "job_application_agent.sources.scrapling_available", lambda: False
    )

    result = ScraplingPublicJobBoardSource(
        [{"name": "Example", "url": "https://jobs.example.com/jobs"}]
    ).collect(host_delay_seconds=0)

    assert result.listings == []
    assert result.health.status == "disabled"
    assert "uv sync --extra scraping" in result.health.message


def test_parse_arbeitnow_json_extracts_api_jobs():
    payload = {
        "data": [
            {
                "title": "AI Automation Developer",
                "company_name": "Automation GmbH",
                "location": "Berlin",
                "remote": True,
                "job_types": ["full-time"],
                "tags": ["n8n", "LLM"],
                "description": "<p>Build workflow automation with LLMs.</p>",
                "created_at": 1760000000,
                "url": "https://www.arbeitnow.com/view/ai-automation-developer",
            }
        ]
    }

    listings = parse_arbeitnow_json(
        payload, "https://www.arbeitnow.com/api/job-board-api"
    )

    assert len(listings) == 1
    assert listings[0].source == "arbeitnow_api"
    assert listings[0].company == "Automation GmbH"
    assert listings[0].remote_type == "remote"
    assert listings[0].tags == ["n8n", "LLM"]
    assert listings[0].application_method == "job_board_listing"
    assert listings[0].apply_platform == "arbeitnow"


def test_parse_arbeitsagentur_json_extracts_public_jobs():
    payload = {
        "stellenangebote": [
            {
                "beruf": "Frontend-Entwickler/in",
                "titel": "Frontend Engineer (gn)",
                "refnr": "12928-KYHVXI40A4MYHZAG-S",
                "arbeitsort": {
                    "ort": "Berlin",
                    "region": "Berlin",
                    "land": "Deutschland",
                },
                "arbeitgeber": "Experis GmbH",
                "aktuelleVeroeffentlichungsdatum": "2026-05-26",
            },
            {
                "titel": "React Developer",
                "arbeitgeber": "Product GmbH",
                "externeUrl": "https://www.jobvector.de/job/abc123/",
            },
        ]
    }

    listings = parse_arbeitsagentur_json(
        payload,
        "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs",
    )

    assert len(listings) == 2
    assert listings[0].source == "arbeitsagentur_api"
    assert listings[0].location == "Berlin Berlin Deutschland"
    assert listings[0].apply_url.endswith("/12928-KYHVXI40A4MYHZAG-S")
    assert listings[0].application_method == "job_board_listing"
    assert listings[1].apply_url == "https://www.jobvector.de/job/abc123/"


def test_parse_arbeitsagentur_public_search_state_extracts_listings():
    html = """
    <html><body><script id="ng-state" type="application/json">
    {"suchergebnis":{"ergebnisliste":[{
      "stellenangebotsTitel":"Product Manager (m/w/d)",
      "firma":"Example GmbH",
      "referenznummer":"REF-123",
      "stellenlokationen":[{"adresse":{"ort":"Köln","region":"NORDRHEIN_WESTFALEN","land":"DEUTSCHLAND"}}],
      "homeofficemoeglich":true,
      "datumErsteVeroeffentlichung":"2026-08-12",
      "hauptberuf":"Produktmanager/in"
    }]}}
    </script></body></html>
    """

    listings = parse_arbeitsagentur_search_html(
        html, "https://www.arbeitsagentur.de/jobsuche/suche?was=Product+Manager"
    )

    assert len(listings) == 1
    assert listings[0].source == "arbeitsagentur_public"
    assert listings[0].company == "Example GmbH"
    assert listings[0].apply_url.endswith("/REF-123")
    assert listings[0].remote_type == "hybrid"


def test_arbeitsagentur_public_source_uses_official_search_page(monkeypatch):
    html = "<script id='ng-state'>{\"suchergebnis\": {\"ergebnisliste\": []}}</script>"
    requests: list[str] = []

    def fake_fetch(url, **_kwargs):
        requests.append(url)
        return html, None

    monkeypatch.setattr("job_application_agent.sources.fetch_text", fake_fetch)
    result = ArbeitsagenturPublicSearchSource(
        queries=["Product Manager"], locations=["Köln"]
    ).collect(host_delay_seconds=0)

    assert result.health.name == "arbeitsagentur_public"
    assert requests and requests[0].startswith(
        "https://www.arbeitsagentur.de/jobsuche/suche?"
    )


def test_arbeitsagentur_source_fetches_pages_until_budget():
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        page = int(params["page"])
        size = int(params["size"])
        items = [
            {
                "titel": f"Frontend Engineer Page {page}-{index}",
                "arbeitgeber": "Example GmbH",
                "refnr": f"REF-{page}-{index}",
                "arbeitsort": {
                    "ort": "Berlin",
                    "region": "Berlin",
                    "land": "Deutschland",
                },
            }
            for index in range(size)
        ]
        return httpx.Response(200, json={"stellenangebote": items})

    result = ArbeitsagenturApiSource(
        queries=["Frontend Engineer"],
        locations=["Berlin"],
        page_size=2,
        max_pages=3,
        transport=httpx.MockTransport(handler),
    ).collect(max_candidates=3, host_delay_seconds=0)

    assert len(result.listings) == 3
    assert [call["page"] for call in calls] == ["1", "2"]
    assert [call["size"] for call in calls] == ["2", "1"]
    assert result.health.candidates_returned == 3


def test_parse_remoteok_json_filters_to_relevant_terms():
    payload = [
        {"legal": "source attribution required"},
        {
            "position": "Merchandising Representative",
            "company": "Sales Co",
            "location": "United States",
            "description": "Retail field role.",
            "apply_url": "https://remoteok.com/remote-jobs/sales-1",
        },
        {
            "position": "Frontend Product Engineer",
            "company": "Remote SaaS",
            "location": "Worldwide",
            "tags": ["react", "typescript"],
            "description": "<p>Build customer-facing web apps.</p>",
            "date": "2026-06-02T19:37:02+00:00",
            "apply_url": "https://remoteok.com/remote-jobs/frontend-2",
        },
    ]

    listings = parse_remoteok_json(
        payload, "https://remoteok.com/api", ["frontend", "react"]
    )

    assert len(listings) == 1
    assert listings[0].source == "remoteok_api"
    assert listings[0].title == "Frontend Product Engineer"
    assert listings[0].remote_type == "remote"
    assert listings[0].application_method == "job_board_listing"
    assert listings[0].apply_platform == "remoteok"


def test_parse_remotive_json_extracts_remote_jobs():
    payload = {
        "jobs": [
            {
                "title": "iOS Developer",
                "company_name": "Mobile Co",
                "category": "Software Development",
                "candidate_required_location": "USA",
                "description": "<p>Mobile implementation work.</p>",
                "url": "https://remotive.com/remote-jobs/software-development/ios-developer-1",
            },
            {
                "title": "Director of Revenue Systems and AI Automation",
                "company_name": "Ops Co",
                "category": "Artificial Intelligence",
                "candidate_required_location": "LATAM",
                "description": "<p>Automation leadership.</p>",
                "url": "https://remotive.com/remote-jobs/artificial-intelligence/director-1",
            },
            {
                "title": "Customer Success Engineer",
                "company_name": "Remote SaaS",
                "category": "Customer Support",
                "tags": ["api", "saas"],
                "job_type": "full_time",
                "publication_date": "2026-06-02T07:53:42",
                "candidate_required_location": "Europe",
                "salary": "EUR 55k",
                "description": "<p>Technical onboarding and integrations.</p>",
                "url": "https://remotive.com/remote-jobs/customer-support/customer-success-engineer-1",
            },
        ]
    }

    listings = parse_remotive_json(
        payload, "https://remotive.com/api/remote-jobs", include_terms=["customer success"]
    )

    assert len(listings) == 1
    assert listings[0].source == "remotive_api"
    assert listings[0].location == "Europe"
    assert listings[0].remote_type == "remote"
    assert listings[0].application_method == "job_board_listing"
    assert listings[0].apply_platform == "remotive"


def test_parse_serpapi_google_results_extracts_organic_links():
    payload = {
        "organic_results": [
            {
                "title": "AI Automation Engineer",
                "link": "https://jobs.example.com/ai-automation-engineer",
                "displayed_link": "jobs.example.com",
                "snippet": "Remote role with n8n, LLMs and workflow automation.",
            },
            {"title": "Local file", "link": "file:///tmp/nope", "snippet": "ignore"},
        ]
    }

    listings = parse_serpapi_google_results(
        payload, "AI Automation Engineer remote Deutschland", "serpapi_google:test"
    )

    assert len(listings) == 1
    assert listings[0].source == "serpapi_google"
    assert listings[0].apply_url == "https://jobs.example.com/ai-automation-engineer"
    assert listings[0].application_method == "external_form"


def test_parse_serpapi_google_results_keeps_known_ats_links_direct_applyable():
    payload = {
        "organic_results": [
            {
                "title": "AI Experience Designer",
                "link": "https://jobs.ashbyhq.com/example/123",
                "displayed_link": "jobs.ashbyhq.com",
                "snippet": "Remote AI experience design role.",
            }
        ]
    }

    listings = parse_serpapi_google_results(
        payload, "site:jobs.ashbyhq.com AI Experience Designer Germany", "serpapi_google:test"
    )

    assert len(listings) == 1
    assert listings[0].application_method == "ats_form"
    assert listings[0].apply_platform == "ashby"


def test_remote_api_parsers_keep_ai_experience_and_conversational_titles():
    remoteok_payload = [
        {
            "position": "AI Experience Designer",
            "company": "Remote Design",
            "url": "https://remoteok.com/remote-jobs/123",
            "location": "Remote",
            "description": "Design human AI workflows with LLM systems.",
        }
    ]
    remotive_payload = {
        "jobs": [
            {
                "title": "Conversational AI Designer",
                "company_name": "Remote UX",
                "url": "https://remotive.com/remote-jobs/design/456",
                "candidate_required_location": "Europe",
                "description": "Design conversational UX for AI products.",
            }
        ]
    }

    remoteok = parse_remoteok_json(remoteok_payload, "https://remoteok.com/api")
    remotive = parse_remotive_json(
        remotive_payload, "https://remotive.com/api/remote-jobs"
    )

    assert [listing.title for listing in remoteok] == ["AI Experience Designer"]
    assert [listing.title for listing in remotive] == ["Conversational AI Designer"]


def test_parse_serpapi_google_results_skips_job_search_pages():
    payload = {
        "organic_results": [
            {
                "title": "Ai Automation Jobs in Home Office",
                "link": "https://de.indeed.com/q-ai-automation-l-home-office-jobs.html",
                "snippet": "Search result page, not a single job.",
            },
            {
                "title": "Ai automation jobs",
                "link": "https://www.getonbrd.world/jobs-AI+Automation",
                "snippet": "Aggregate page, not a single job.",
            },
            {
                "title": "7327+ Ai Automation Engineer-Stellen in Deutschland 2026",
                "link": "https://bebee.com/de/jobs/role/ai-automation-engineer",
                "snippet": "Aggregate role page, not a single job.",
            },
            {
                "title": "AI Automation Engineer / KI-Entwickler (m/w/d) - remote",
                "link": "https://www.ingenieurjobs.de/sv/jobb/ai-automation-engineer-ki-entwickler-mwd-remote-1",
                "snippet": "Remote AI automation role.",
            },
        ]
    }

    listings = parse_serpapi_google_results(
        payload, "AI Automation Engineer remote Deutschland", "serpapi_google:test"
    )

    assert len(listings) == 1
    assert listings[0].company == "ingenieurjobs.de"


def test_parse_serpapi_google_results_skips_non_job_content_pages():
    payload = {
        "organic_results": [
            {
                "title": "n8n - Secure Workflow Automation for Technical Teams",
                "link": "https://github.com/n8n-io/n8n",
                "snippet": "Open source workflow automation.",
            },
            {
                "title": "N8n",
                "link": "https://de.wikipedia.org/wiki/N8n",
                "snippet": "Wikipedia article.",
            },
            {
                "title": "n8n im Praxistest: Workflow-Automatisierung",
                "link": "https://www.hco.de/blog/n8n-im-praxistest-workflow-automatisierung",
                "snippet": "Blog article.",
            },
            {
                "title": "Junior Automation & AI Developer (m/f/d)",
                "link": "https://www.remotely.de/job/funke-mediengruppe-junior-automation-ai-developer-mfd",
                "snippet": "Remote full-time job with automation and AI.",
            },
        ]
    }

    listings = parse_serpapi_google_results(
        payload,
        "n8n Workflow Automation Developer Berlin remote Job",
        "serpapi_google:test",
    )

    assert len(listings) == 1
    assert listings[0].company == "remotely.de"


def test_public_search_source_is_disabled_without_serpapi_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("SERP_API_KEY", raising=False)

    result = PublicSearchSource(["AI Automation Engineer remote Deutschland"]).collect()

    assert result.listings == []
    assert result.health.status == "disabled"
    assert "SERPAPI_API_KEY" in result.health.message


def test_public_search_source_can_be_forced_disabled_with_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setenv("JOB_AGENT_DISABLE_SERPAPI", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("SerpAPI transport should not be called when disabled")

    result = PublicSearchSource(
        ["AI Automation Engineer remote Deutschland"],
        max_queries=1,
        transport=httpx.MockTransport(handler),
    ).collect(max_candidates=5, host_delay_seconds=0)

    assert result.listings == []
    assert result.health.status == "disabled"
    assert "disabled" in result.health.message


def test_public_search_source_uses_serpapi_with_key(monkeypatch):
    monkeypatch.delenv("JOB_AGENT_DISABLE_SERPAPI", raising=False)
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["engine"] == "google"
        assert params["q"] == "AI Automation Engineer remote Deutschland"
        assert params["api_key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "organic_results": [
                    {
                        "title": "AI Automation Engineer",
                        "link": "https://jobs.example.com/ai-automation-engineer",
                        "snippet": "Remote n8n LLM workflow automation.",
                    }
                ]
            },
        )

    result = PublicSearchSource(
        ["AI Automation Engineer remote Deutschland"],
        max_queries=1,
        transport=httpx.MockTransport(handler),
    ).collect(max_candidates=5, host_delay_seconds=0)

    assert len(result.listings) == 1
    assert result.health.status == "available"


def test_public_search_env_query_cap_limits_configured_queries(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setenv("SERPAPI_MAX_QUERIES_PER_RUN", "1")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params["q"]))
        return httpx.Response(
            200,
            json={
                "organic_results": [
                    {
                        "title": "AI Automation Engineer",
                        "link": f"https://jobs.example.com/{len(calls)}",
                        "snippet": "Remote n8n LLM workflow automation.",
                    }
                ]
            },
        )

    result = PublicSearchSource(
        ["first query", "second query"],
        max_queries=3,
        transport=httpx.MockTransport(handler),
    ).collect(max_candidates=5, host_delay_seconds=0)

    assert len(result.listings) == 1
    assert calls == ["first query"]


def test_detect_language_handles_german_freelancermap_titles():
    text = "Expert:in für End-to-End Tracking Setup mit Piano Junico GmbH 100% remote"
    assert detect_language(text) == "de"


def test_source_url_policy_rejects_non_public_and_wrong_hosts():
    assert validate_public_source_url("file:///tmp/job.html")
    assert validate_public_source_url("http://localhost/jobs")
    assert validate_public_source_url("http://169.254.169.254/latest/meta-data")
    assert validate_public_source_url("http://100.64.0.1/jobs")
    assert validate_public_source_url("http://198.51.100.1/jobs")
    assert validate_public_source_url("http://[fd00::1]/jobs")
    assert validate_public_source_url("http://[fe80::1]/jobs")
    assert validate_public_source_url("http://2130706433/jobs")
    assert validate_public_source_url("http://0x7f000001/jobs")
    assert validate_public_source_url("http://127.1/jobs")
    assert validate_public_source_url(
        "https://example.com/jobs", allowed_hosts=FREELANCERMAP_HOSTS
    )
    assert validate_public_source_url(
        "https://jobs.example.com/xml", required_host_suffix=".jobs.personio.de"
    )
    assert (
        validate_public_source_url(
            "https://www.freelancermap.com/projects/remote",
            allowed_hosts=FREELANCERMAP_HOSTS,
        )
        is None
    )
    assert (
        validate_public_source_url(
            "https://company.jobs.personio.de/xml?language=de",
            required_host_suffix=".jobs.personio.de",
        )
        is None
    )


def test_fetch_text_revalidates_redirect_destinations():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://www.freelancermap.com/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(200, text="should not fetch")

    body, error = fetch_text(
        "https://www.freelancermap.com/start",
        allowed_hosts=FREELANCERMAP_HOSTS,
        transport=httpx.MockTransport(handler),
    )

    assert body == ""
    assert error is not None
    assert "Redirect blocked" in error


def test_fetch_text_keeps_allowed_public_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://www.freelancermap.com/start":
            return httpx.Response(302, headers={"location": "/projects/remote"})
        return httpx.Response(200, text="ok")

    body, error = fetch_text(
        "https://www.freelancermap.com/start",
        allowed_hosts=FREELANCERMAP_HOSTS,
        transport=httpx.MockTransport(handler),
    )

    assert body == "ok"
    assert error is None
