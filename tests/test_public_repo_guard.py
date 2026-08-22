from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "check_public_repo.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Example Maintainer")
    _git(repo, "config", "user.email", "maintainer@example.invalid")
    return repo


def _commit(repo: Path, message: str = "test fixture") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _scan(
    repo: Path,
    *,
    history: bool = True,
    blocklist: Path | None = None,
    require_blocklist: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCANNER), "--repo", str(repo)]
    if history:
        command.append("--history")
    if blocklist:
        command.extend(["--private-blocklist", str(blocklist)])
    if require_blocklist:
        command.append("--require-private-blocklist")
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_clean_reserved_examples_pass(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text(
        "Contact: maintainer@example.invalid\nTOKEN=${TOKEN}\n", encoding="utf-8"
    )
    _commit(repo)

    result = _scan(repo)

    assert result.returncode == 0
    assert "privacy gate passed" in result.stdout


def test_standard_git_ssh_remote_is_not_treated_as_personal_email(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text(
        "Clone with git@github.com:example/repository.git\n", encoding="utf-8"
    )
    _commit(repo)

    result = _scan(repo)

    assert result.returncode == 0


@pytest.mark.parametrize(
    ("path", "content", "category"),
    [
        ("config/candidate.yaml", "profile: {}\n", "private runtime path"),
        ("documents/resume.pdf", "%PDF-test", "personal or opaque binary file type"),
        (
            "screenshots/page.png",
            "not really an image",
            "personal or opaque binary file type",
        ),
        ("backup.zip", "archive", "archive requires manual content review"),
    ],
)
def test_private_and_opaque_paths_fail(
    tmp_path: Path, path: str, content: str, category: str
) -> None:
    repo = _repo(tmp_path)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _commit(repo)

    result = _scan(repo)

    assert result.returncode == 1
    assert category in result.stdout


@pytest.mark.parametrize(
    ("content", "category"),
    [
        ("Contact: " + "person@" + "mail.de", "email outside reserved example domains"),
        ("Path: " + "/Users/" + "private-account/file", "absolute local user path"),
        (
            "API_" + "TOKEN=" + "a9c8b7d6e5f4g3h2i1j0-private-value",
            "non-placeholder secret assignment",
        ),
        (
            "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key",
            "private key material",
        ),
        (
            "Authorization: Bearer "
            + "eyJabcdefgh"
            + "ijklmno.abcdefgh"
            + "ijklmnop.abcdefghijklmnop",
            "JWT",
        ),
    ],
)
def test_sensitive_content_fails_without_printing_value(
    tmp_path: Path, content: str, category: str
) -> None:
    repo = _repo(tmp_path)
    (repo / "example.txt").write_text(content, encoding="utf-8")
    _commit(repo)

    result = _scan(repo)

    assert result.returncode == 1
    assert category in result.stdout
    assert content not in result.stdout


def test_notebook_outputs_fail(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": ["ok"]}
                ],
                "source": ["print('ok')"],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (repo / "analysis.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    _commit(repo)

    result = _scan(repo)

    assert result.returncode == 1
    assert "notebook contains executed outputs" in result.stdout


def test_history_scan_finds_deleted_leak(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    leaked_value = "historic-person@" + "mail.de"
    leak = repo / "old.txt"
    leak.write_text(leaked_value, encoding="utf-8")
    _commit(repo, "first")
    leak.unlink()
    (repo / "README.md").write_text("clean tip\n", encoding="utf-8")
    _commit(repo, "second")

    tip_only = _scan(repo, history=False)
    full_history = _scan(repo, history=True)

    assert tip_only.returncode == 0
    assert full_history.returncode == 1
    assert "email outside reserved example domains" in full_history.stdout
    assert leaked_value not in full_history.stdout


def test_private_blocklist_is_required_and_redacted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    private_term = "Unique" + "PrivateIdentity"
    (repo / "README.md").write_text(f"Maintained by {private_term}\n", encoding="utf-8")
    _commit(repo)
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text(f"{private_term}\nsecond-private-term\n", encoding="utf-8")

    result = _scan(repo, blocklist=blocklist, require_blocklist=True)

    assert result.returncode == 1
    assert "private blocklist match" in result.stdout
    assert private_term not in result.stdout


def test_missing_required_private_blocklist_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    _commit(repo)

    result = _scan(repo, require_blocklist=True)

    assert result.returncode == 1
    assert "required local blocklist is missing" in result.stdout
