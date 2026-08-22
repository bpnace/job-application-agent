from __future__ import annotations

import os

from agents import Agent, ModelSettings, RunConfig, Runner

from .models import JobListing, JobScorecard


DEFAULT_MODEL = "gpt-5.4-mini"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def build_discovery_agent() -> Agent:
    return Agent(
        name="Job Discovery Agent",
        model=os.getenv("AGENT_MODEL", DEFAULT_MODEL),
        model_settings=ModelSettings(max_tokens=_env_int("AGENT_MAX_OUTPUT_TOKENS", 1800), temperature=0.1, verbosity="low"),
        instructions=(
            "Normalize public job page evidence into the JobListing schema. Do not invent missing facts. "
            "Only claim that an application was submitted when an explicit browser-submit mode returned success."
        ),
        output_type=JobListing,
    )


def build_scoring_agent() -> Agent:
    return Agent(
        name="Fit Scoring Agent",
        model=os.getenv("AGENT_MODEL", DEFAULT_MODEL),
        model_settings=ModelSettings(max_tokens=_env_int("AGENT_MAX_OUTPUT_TOKENS", 1800), temperature=0.1, verbosity="low"),
        instructions=(
            "Score jobs only against the configured candidate profile. Apply only its "
            "configured exclusions and keep scores explainable."
        ),
        output_type=JobScorecard,
    )


def build_cover_letter_agent() -> Agent:
    return Agent(
        name="Cover Letter Agent",
        model=os.getenv("AGENT_MODEL", DEFAULT_MODEL),
        model_settings=ModelSettings(max_tokens=_env_int("AGENT_MAX_OUTPUT_TOKENS", 1800), temperature=0.2, verbosity="low"),
        instructions=(
            "Write draft-only cover letters in the job posting language. Use only provided CV facts, GitHub and LinkedIn. "
            "Avoid generic AI application prose. Submission requires an explicit browser-submit command outside this writer."
        ),
    )


async def run_optional_agent(agent: Agent, prompt: str):
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing. Deterministic local pipeline remains available.")
    return await Runner.run(
        agent,
        prompt,
        run_config=RunConfig(tracing_disabled=os.getenv("AGENT_TRACING_DISABLED", "true").lower() != "false"),
    )
