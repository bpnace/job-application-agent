"""Deep privacy and secret gate for Git trees and reachable history.

The scanner reads blobs from Git rather than the working tree. This prevents a
clean checkout, ``.gitignore`` or an unrelated local branch from hiding data
that would actually be published. Findings deliberately never print matched
values.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence


SCANNER_PATH = "scripts/check_public_repo.py"
MAX_TEXT_BYTES = 8 * 1024 * 1024

PRIVATE_PATH_PREFIXES = (
    ".job-agent/",
    ".playwright-mcp/",
    ".playwright-cli/",
    "browser-profile/",
    "config/candidate.yaml",
    "runs/",
)
PRIVATE_PATH_NAMES = {
    ".env",
    ".env.local",
    "candidate.yaml",
    "credentials.json",
    "secrets.json",
}
FORBIDDEN_BINARY_SUFFIXES = {
    ".doc",
    ".docx",
    ".heic",
    ".jpeg",
    ".jpg",
    ".key",
    ".numbers",
    ".odt",
    ".pages",
    ".p12",
    ".pdf",
    ".pem",
    ".pfx",
    ".png",
    ".sqlite",
    ".sqlite3",
    ".webp",
}
FORBIDDEN_ARCHIVE_SUFFIXES = {
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
}
REVIEWED_BINARY_ALLOWLIST = {
    # Generic UI taxonomy screenshot from the clean v0.3.0 root. It contains
    # no identity, contact data, account name, URL or secret and is removed at
    # the v0.3.1 tip. The hash keeps that historical review exact.
    "industry-check.png": "f54067362754e8f1cd17c7cf649bf72df98264099fd15571118a2000fd2929d9",
}
REVIEWED_EMAIL_ALLOWLIST = {
    "git@bitbucket.org",
    "git@github.com",
    "git@gitlab.com",
    "raw-thread@outlook.de",
    "sensitive-thread@outlook.de",
}
REVIEWED_PHONE_ALLOWLIST = {"+49 151 234 56789"}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".csv",
    ".env.example",
    ".html",
    ".ini",
    ".j2",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
RESERVED_EMAIL_DOMAINS = {
    "beispiel.de",
    "example.com",
    "example.invalid",
    "example.net",
    "example.org",
    "example.test",
    "localhost",
}

EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,}|localhost)(?![\w.-])"
)
LOCAL_PATH_PATTERNS = (
    re.compile(r"/Users/(?P<user>[^/\s]+)/"),
    re.compile(r"[A-Za-z]:\\Users\\(?P<user>[^\\\s]+)\\"),
    re.compile(r"/home/(?P<user>[^/\s]+)/"),
)
PUBLIC_RUNNER_USERS = {"runner", "runneradmin"}
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
KNOWN_SECRET_PATTERNS = (
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    (
        "JWT",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
    ),
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?[\"']?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)"
    r"[\"']?[ \t]*(?:=|:)[ \t]*(?P<value>[^\s#][^\r\n#]*)"
)
URL_CREDENTIAL_PATTERN = re.compile(
    r"https?://[^\s/:@]+:[^\s/@]+@[^\s/]+", re.IGNORECASE
)
PHONE_LABEL_PATTERN = re.compile(
    r"(?im)^\s*(?:phone|mobile|telefon|telefonnummer|handy)\s*(?:=|:)\s*[\"']?"
    r"(?P<value>\+?[0-9][0-9 ()/.-]{7,}[0-9])"
)


@dataclass(frozen=True)
class Finding:
    location: str
    category: str


@dataclass(frozen=True)
class GitBlob:
    object_id: str
    path: str


def _git(repo: Path, *args: str, text: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return result.stdout


def _resolve_commits(repo: Path, refs: Sequence[str], *, history: bool) -> list[str]:
    commits: set[str] = set()
    for ref in refs:
        if history:
            output = _git(repo, "rev-list", ref, text=True)
            assert isinstance(output, str)
            commits.update(line for line in output.splitlines() if line)
        else:
            output = _git(repo, "rev-parse", f"{ref}^{{commit}}", text=True)
            assert isinstance(output, str)
            commits.add(output.strip())
    return sorted(commits)


def _all_refs(repo: Path) -> list[str]:
    output = _git(repo, "for-each-ref", "--format=%(refname)", text=True)
    assert isinstance(output, str)
    refs = [line for line in output.splitlines() if line and not line.endswith("/HEAD")]
    return refs or ["HEAD"]


def _tree_blobs(repo: Path, commit: str) -> list[GitBlob]:
    output = _git(repo, "ls-tree", "-r", "-z", commit)
    assert isinstance(output, bytes)
    blobs: list[GitBlob] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", maxsplit=1)
        _mode, object_type, object_id = metadata.decode("ascii").split()
        if object_type == "blob":
            blobs.append(
                GitBlob(
                    object_id=object_id, path=raw_path.decode("utf-8", errors="replace")
                )
            )
    return blobs


def _blob_content(repo: Path, object_id: str) -> bytes:
    output = _git(repo, "cat-file", "blob", object_id)
    assert isinstance(output, bytes)
    return output


def _private_terms(
    path: Path | None, *, required: bool
) -> tuple[list[str], list[Finding]]:
    if path is None or not path.is_file():
        if required:
            return [], [
                Finding("private blocklist", "required local blocklist is missing")
            ]
        return [], []
    terms = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()
        if value and not value.startswith("#") and len(value) >= 4:
            terms.append(value.casefold())
    if required and len(set(terms)) < 2:
        return terms, [
            Finding("private blocklist", "at least two private terms are required")
        ]
    return sorted(set(terms), key=len, reverse=True), []


def _is_private_path(path: str) -> bool:
    normalized = path.lstrip("./")
    pure_path = PurePosixPath(normalized)
    if normalized.startswith(PRIVATE_PATH_PREFIXES):
        return True
    if pure_path.name.lower() in PRIVATE_PATH_NAMES:
        return True
    lowered_name = pure_path.name.lower()
    return "humanizer" in lowered_name and pure_path.suffix.lower() in {
        ".md",
        ".yaml",
        ".yml",
    }


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").strip()
    lowered = normalized.casefold()
    if not normalized:
        return True
    if lowered in {"none", "null", "false", "true", "changeme", "dummy", "placeholder"}:
        return True
    if any(
        marker in lowered
        for marker in (
            "example",
            "beispiel",
            "your_",
            "your-",
            "test-",
            "test_",
            "do_not_serialize",
            "<",
            ">",
        )
    ):
        return True
    if normalized.startswith(("${", "$", "{{")):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]{3,}", normalized):
        return True
    return False


def _is_sensitive_key(key: str) -> bool:
    parts = [part for part in re.split(r"[^a-z0-9]+", key.casefold()) if part]
    joined = "_".join(parts)
    if any(
        phrase in joined
        for phrase in (
            "api_key",
            "access_token",
            "auth_token",
            "client_secret",
            "private_key",
        )
    ):
        return True
    return any(part in {"password", "passwd", "secret", "token"} for part in parts)


def _scan_text(location: str, text: str, private_terms: Sequence[str]) -> list[Finding]:
    findings: list[Finding] = []
    folded = text.casefold()
    if any(term in folded for term in private_terms):
        findings.append(Finding(location, "private blocklist match"))
    if any(
        match.group("user").casefold() not in PUBLIC_RUNNER_USERS
        for pattern in LOCAL_PATH_PATTERNS
        for match in pattern.finditer(text)
    ):
        findings.append(Finding(location, "absolute local user path"))
    if PRIVATE_KEY_PATTERN.search(text):
        findings.append(Finding(location, "private key material"))
    if URL_CREDENTIAL_PATTERN.search(text):
        findings.append(Finding(location, "credentials embedded in URL"))
    for local_part, domain in EMAIL_PATTERN.findall(text):
        email = f"{local_part}@{domain}".casefold()
        if (
            domain.casefold() not in RESERVED_EMAIL_DOMAINS
            and email not in REVIEWED_EMAIL_ALLOWLIST
        ):
            findings.append(Finding(location, "email outside reserved example domains"))
            break
    for category, pattern in KNOWN_SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(Finding(location, category))
    for match in SECRET_ASSIGNMENT_PATTERN.finditer(text):
        key = match.group("key")
        value = match.group("value")
        is_source_code = PurePosixPath(location).suffix.casefold() in {
            ".js",
            ".py",
            ".ts",
            ".tsx",
        }
        if not _is_sensitive_key(key):
            continue
        if is_source_code and not value.lstrip().startswith(('"', "'")):
            continue
        if not _looks_like_placeholder(value):
            findings.append(Finding(location, "non-placeholder secret assignment"))
            break
    for match in PHONE_LABEL_PATTERN.finditer(text):
        raw_phone = match.group("value").strip()
        digits = re.sub(r"\D", "", raw_phone)
        if (
            len(digits) >= 8
            and len(set(digits)) > 3
            and "555" not in digits
            and "000000" not in digits
            and raw_phone not in REVIEWED_PHONE_ALLOWLIST
        ):
            findings.append(Finding(location, "non-placeholder phone number"))
            break
    return findings


def _scan_notebook(
    location: str, content: bytes, private_terms: Sequence[str]
) -> list[Finding]:
    try:
        notebook = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [Finding(location, "invalid or unreadable notebook")]
    findings = _scan_text(
        location, json.dumps(notebook, ensure_ascii=False), private_terms
    )
    for cell in notebook.get("cells", []):
        if cell.get("outputs") or cell.get("execution_count") is not None:
            findings.append(Finding(location, "notebook contains executed outputs"))
            break
    return findings


def _scan_blob(
    path: str, content: bytes, private_terms: Sequence[str]
) -> list[Finding]:
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.casefold()
    if _is_private_path(path):
        return [Finding(path, "private runtime path")]
    if suffix in FORBIDDEN_BINARY_SUFFIXES:
        expected_hash = REVIEWED_BINARY_ALLOWLIST.get(pure_path.name)
        if expected_hash and hashlib.sha256(content).hexdigest() == expected_hash:
            return []
        return [Finding(path, "personal or opaque binary file type")]
    if suffix in FORBIDDEN_ARCHIVE_SUFFIXES:
        return [Finding(path, "archive requires manual content review")]
    if suffix == ".ipynb":
        return _scan_notebook(path, content, private_terms)
    compound_suffix = "".join(pure_path.suffixes[-2:]).casefold()
    if suffix not in TEXT_SUFFIXES and compound_suffix not in TEXT_SUFFIXES:
        if b"\0" in content[:8192]:
            return [Finding(path, "unreviewed binary file type")]
    if len(content) > MAX_TEXT_BYTES:
        return [Finding(path, "file exceeds automatic review limit")]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding(path, "unreviewed non-UTF-8 file")]
    if path == SCANNER_PATH or path.endswith(f"/{SCANNER_PATH}"):
        # This source intentionally contains token signatures and local-path
        # patterns. Its behavior is covered by dedicated regression tests.
        return [
            Finding(path, "private blocklist match")
            for term in private_terms
            if term in text.casefold()
        ]
    return _scan_text(path, text, private_terms)


def _scan_commit_metadata(
    repo: Path, commit: str, private_terms: Sequence[str]
) -> list[Finding]:
    output = _git(
        repo,
        "show",
        "-s",
        "--format=%an%n%ae%n%cn%n%ce%n%B",
        commit,
        text=True,
    )
    assert isinstance(output, str)
    return _scan_text(f"commit {commit[:12]} metadata", output, private_terms)


def scan_repository(
    repo: Path,
    *,
    refs: Sequence[str],
    history: bool,
    private_terms: Sequence[str],
) -> list[Finding]:
    commits = _resolve_commits(repo, refs, history=history)
    findings: set[Finding] = set()
    scanned_blobs: set[tuple[str, str]] = set()
    for commit in commits:
        findings.update(_scan_commit_metadata(repo, commit, private_terms))
        for blob in _tree_blobs(repo, commit):
            key = (blob.object_id, blob.path)
            if key in scanned_blobs:
                continue
            scanned_blobs.add(key)
            content = _blob_content(repo, blob.object_id)
            findings.update(_scan_blob(blob.path, content, private_terms))
    return sorted(findings, key=lambda finding: (finding.location, finding.category))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="Git repository to scan."
    )
    parser.add_argument(
        "--ref",
        action="append",
        dest="refs",
        help="Git ref to scan; repeatable. Defaults to HEAD.",
    )
    parser.add_argument(
        "--all-refs", action="store_true", help="Scan every local Git ref."
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Scan all commits reachable from the selected refs.",
    )
    parser.add_argument(
        "--private-blocklist",
        type=Path,
        help="Ignored local file with one private term per line.",
    )
    parser.add_argument(
        "--require-private-blocklist",
        action="store_true",
        help="Fail unless the local blocklist contains at least two terms.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print category counts without file names or commit identifiers.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repo = args.repo.expanduser().resolve()
    blocklist = args.private_blocklist
    if blocklist is None:
        blocklist = repo / ".job-agent" / "privacy" / "blocklist.txt"
    private_terms, findings = _private_terms(
        blocklist.expanduser().resolve(), required=args.require_private_blocklist
    )
    refs = _all_refs(repo) if args.all_refs else (args.refs or ["HEAD"])
    try:
        findings.extend(
            scan_repository(
                repo,
                refs=refs,
                history=args.history,
                private_terms=private_terms,
            )
        )
    except subprocess.CalledProcessError:
        findings.append(
            Finding("Git repository", "could not resolve or inspect selected refs")
        )

    unique_findings = sorted(
        set(findings), key=lambda finding: (finding.location, finding.category)
    )
    if unique_findings:
        print("Public repository privacy gate failed:")
        if args.summary_only:
            counts = Counter(finding.category for finding in unique_findings)
            for category, count in sorted(counts.items()):
                print(f"- {category}: {count}")
        else:
            for finding in unique_findings:
                print(f"- {finding.location}: {finding.category}")
        print("No matched values are printed. Review the named locations locally.")
        return 1

    print(
        f"Public repository privacy gate passed: {len(refs)} ref(s), "
        f"history={'on' if args.history else 'off'}, private_blocklist={'loaded' if private_terms else 'not loaded'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
