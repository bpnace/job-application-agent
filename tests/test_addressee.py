from job_application_agent.addressee import (
    extract_company_from_listing,
    is_portal_name,
)
from job_application_agent.models import JobListing


def test_extract_company_from_listing_uses_company_named_in_portal_title():
    listing = JobListing(
        source="remote_rocketship",
        source_url="https://remoterocketship.com/jobs/ai-automation-engineer",
        title="AI Automation Engineer bei Mann GmbH",
        company="remoterocketship.com",
        description="Remote n8n, React and TypeScript automation role.",
    )

    assert is_portal_name(listing.company)
    assert extract_company_from_listing(listing) == "Mann GmbH"


def test_extract_company_from_listing_uses_join_company_slug_without_legal_suffix():
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/caya/jobs/customer-success-manager",
        apply_url="https://join.com/companies/caya/jobs/customer-success-manager/apply",
        title="Customer Success Manager",
        company="join.com",
        description="SaaS startup role with customer onboarding.",
    )

    assert extract_company_from_listing(listing) == "Caya"


def test_extract_company_from_stepstone_listing_with_partgmbb_suffix():
    listing = JobListing(
        source="stepstone_public",
        source_url="https://www.stepstone.de/jobs/ai-automation-engineer/in-deutschland",
        apply_url="https://www.stepstone.de/stellenangebote--AI-Automation-Engineer-m-w-d-in-Muenchen-Berlin-Homeoffice-Muenchen-Berlin-Becker-Buettner-Held-Rechtsanwaelte-Steuerberater-Unternehmensberater-PartGmbB--14222117-inline.html",
        title="AI & Automation Engineer (m/w/d) in München, Berlin, Homeoffice",
        company="StepStone",
        description=(
            "AI & Automation Engineer (m/w/d) in München, Berlin, Homeoffice "
            "StepStone München, Berlin AI & Automation Engineer (m/w/d) "
            "Becker Büttner Held Rechtsanwälte Steuerberater "
            "Unternehmensberater PartGmbB München, Berlin"
        ),
    )

    assert (
        extract_company_from_listing(listing)
        == "Becker Büttner Held Rechtsanwälte Steuerberater Unternehmensberater PartGmbB"
    )
