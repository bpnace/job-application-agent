"""Fail CI when files likely to contain private candidate data are tracked."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


PRIVATE_PATHS = (
    ".job-agent/",
    "config/candidate.yaml",
)
PRIVATE_DOCUMENT_SUFFIXES = {".docx", ".odt", ".pages", ".pdf"}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".j2",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])"
)
LOCAL_PATH_PATTERNS = (
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    re.compile(r"/home/[^/\s]+/"),
)
RESERVED_EMAIL_DOMAINS = {
    "beispiel.de",
    "example.com",
    "example.invalid",
    "example.net",
    "example.org",
}
SYNTHETIC_EMAIL_PREFIXES = (
    "applicant",
    "candidate",
    "max",
    "jobs",
    "raw-thread",
    "recruiter",
    "reply-",
    "secret.sender",
    "sender",
    "sensitive-thread",
    "talent",
    "test",
    "user",
)


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [Path(raw.decode()) for raw in result.stdout.split(b"\0") if raw]


def is_private_path(path: Path) -> bool:
    normalized = path.as_posix()
    if normalized.startswith(PRIVATE_PATHS) or normalized == "config/candidate.yaml":
        return True
    name = path.name.lower()
    return "humanizer" in name and path.suffix.lower() in {".md", ".yaml", ".yml"}


def is_allowed_email(local_part: str, domain: str) -> bool:
    normalized_local = local_part.lower()
    normalized_domain = domain.lower()
    return normalized_domain in RESERVED_EMAIL_DOMAINS or normalized_local.startswith(
        SYNTHETIC_EMAIL_PREFIXES
    )


def scan_text(path: Path, text: str) -> list[str]:
    findings: list[str] = []
    for pattern in LOCAL_PATH_PATTERNS:
        if pattern.search(text):
            findings.append("absolute local user path")
            break
    for local_part, domain in EMAIL_PATTERN.findall(text):
        if not is_allowed_email(local_part, domain):
            findings.append("non-synthetic email address")
            break
    return findings


def main() -> int:
    findings: list[str] = []
    for path in tracked_paths():
        if is_private_path(path):
            findings.append(f"{path}: private runtime path")
            continue
        if path.suffix.lower() in PRIVATE_DOCUMENT_SUFFIXES:
            findings.append(f"{path}: personal document type")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(f"{path}: {finding}" for finding in scan_text(path, text))

    if findings:
        print("Public repository guard failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("Public repository guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
