from __future__ import annotations

from urllib.parse import quote

from job_application_agent.browser_apply import probe_public_form_read_only


def test_read_only_probe_detects_fields_and_aborts_non_get_requests():
    html = """<form><label for='name'>Name</label><input id='name' name='name'></form>
    <script>fetch('https://example.test/application', {method: 'POST', body: 'blocked'}).catch(() => {});</script>"""

    result = probe_public_form_read_only(f"data:text/html,{quote(html)}")

    assert result["read_only"] is True
    assert result["reachable"] is True
    assert result["field_count"] == 1
    assert result["filled_fields"] == 0
    assert result["uploads"] == 0
    assert result["submit_attempted"] is False
    assert result["blocked_non_get_requests"]
    assert result["blocked_non_get_requests"][0]["method"] == "POST"
