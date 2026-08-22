from pathlib import Path

import yaml

from job_application_agent.config import default_agent_home, default_runs_dir


ROOT = Path(__file__).resolve().parents[1]


def test_bundled_search_profile_is_a_pii_free_unconfigured_starter():
    config = yaml.safe_load((ROOT / "config" / "search_profile.yaml").read_text())

    assert config["search"]["profile_configured"] is False
    assert config["search"]["target_roles"] == []
    assert config["search"]["keywords"] == []
    assert config["sources"]["public_search"]["queries"] == []
    assert config["sources"]["arbeitsagentur"].get("api_key") is None


def test_default_runs_dir_uses_machine_local_environment_setting(monkeypatch, tmp_path):
    configured = tmp_path / "local-runs"
    monkeypatch.setenv("JOB_AGENT_RUNS_DIR", str(configured))

    assert default_runs_dir() == configured.resolve()

    monkeypatch.delenv("JOB_AGENT_RUNS_DIR")
    monkeypatch.setenv("JOB_AGENT_HOME", str(tmp_path / "agent-home"))
    assert default_runs_dir() == default_agent_home() / "runs"
