"""Run the mandatory local and anonymous-remote publication audit."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Sequence

from check_public_repo import Finding, _private_terms, _scan_blob, _scan_text


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "check_public_repo.py"


def _run(
    command: Sequence[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> None:
    print(f"[audit] {command[0]} {command[1] if len(command) > 1 else ''}".rstrip())
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _blocklist_path(value: Path | None) -> Path:
    return (
        (value or ROOT / ".job-agent" / "privacy" / "blocklist.txt")
        .expanduser()
        .resolve()
    )


def _guard_command(repo: Path, blocklist: Path, *, all_refs: bool) -> list[str]:
    command = [
        sys.executable,
        str(SCANNER),
        "--repo",
        str(repo),
        "--history",
        "--private-blocklist",
        str(blocklist),
        "--require-private-blocklist",
    ]
    command.append("--all-refs" if all_refs else "--ref")
    if not all_refs:
        command.append("HEAD")
    return command


def _ensure_clean_tree() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        raise SystemExit("Publication audit requires a committed, clean working tree.")


def _scan_archive(archive: Path, private_terms: Sequence[str]) -> list[Finding]:
    findings: list[Finding] = []
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                if member.flag_bits & 0x1:
                    findings.append(
                        Finding(member.filename, "encrypted archive member")
                    )
                    continue
                findings.extend(
                    _scan_blob(member.filename, bundle.read(member), private_terms)
                )
        return findings
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    findings.append(Finding(member.name, "non-regular archive member"))
                    continue
                extracted = bundle.extractfile(member)
                if extracted is None:
                    findings.append(Finding(member.name, "unreadable archive member"))
                    continue
                findings.extend(
                    _scan_blob(member.name, extracted.read(), private_terms)
                )
        return findings
    return [Finding(archive.name, "unknown release artifact format")]


def _audit_release_artifacts(directory: Path, blocklist: Path) -> None:
    private_terms, blocklist_findings = _private_terms(blocklist, required=True)
    findings = list(blocklist_findings)
    generated_files = sorted(path for path in directory.iterdir() if path.is_file())
    artifacts = [
        path
        for path in generated_files
        if path.suffix == ".whl" or path.name.endswith(".tar.gz")
    ]
    unexpected = [
        path
        for path in generated_files
        if path not in artifacts and path.name != ".gitignore"
    ]
    findings.extend(
        Finding(path.name, "unexpected build output") for path in unexpected
    )
    if not artifacts:
        findings.append(Finding("release artifacts", "build produced no files"))
    for artifact in artifacts:
        findings.extend(_scan_archive(artifact, private_terms))
    if findings:
        print("Release artifact privacy gate failed:")
        for finding in sorted(
            set(findings), key=lambda item: (item.location, item.category)
        ):
            print(f"- {finding.location}: {finding.category}")
        print("No matched values are printed. Review the named locations locally.")
        raise SystemExit(1)
    print(f"Release artifact privacy gate passed: {len(artifacts)} artifact(s).")


def _local_audit(blocklist: Path, *, all_refs: bool) -> None:
    _ensure_clean_tree()
    _run(_guard_command(ROOT, blocklist, all_refs=all_refs))
    _run(["git", "diff", "--check"])
    _run(["uv", "run", "ruff", "check", "."])
    _run(["uv", "run", "python", "-m", "pyright"])
    _run(["uv", "run", "python", "-m", "pytest"])
    with tempfile.TemporaryDirectory(prefix="job-agent-build-") as raw_directory:
        directory = Path(raw_directory)
        _run(["uv", "build", "--out-dir", str(directory)])
        _audit_release_artifacts(directory, blocklist)


def _anonymous_remote_audit(remote_url: str, blocklist: Path) -> None:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    with tempfile.TemporaryDirectory(
        prefix="job-agent-public-mirror-"
    ) as raw_directory:
        mirror = Path(raw_directory) / "repository.git"
        _run(
            [
                "git",
                "-c",
                "credential.helper=",
                "clone",
                "--mirror",
                remote_url,
                str(mirror),
            ],
            env=environment,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(mirror),
                "-c",
                "credential.helper=",
                "fetch",
                "--quiet",
                remote_url,
                "+refs/pull/*:refs/pull/*",
            ],
            env=environment,
            check=False,
        )
        _run(_guard_command(mirror, blocklist, all_refs=True), env=environment)
    print(
        "Anonymous remote mirror audit passed, including advertised refs and pull refs."
    )


def _gh_api(endpoint: str, *, binary: bool = False, paginate: bool = False) -> bytes:
    command = ["gh", "api"]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    command.append(endpoint)
    if binary:
        command.extend(["-H", "Accept: application/octet-stream"])
    result = subprocess.run(command, check=True, capture_output=True)
    return result.stdout


def _github_surface_audit(repository: str, blocklist: Path) -> None:
    private_terms, findings = _private_terms(blocklist, required=True)
    text_endpoints = (
        f"repos/{repository}",
        f"repos/{repository}/issues?state=all&per_page=100",
        f"repos/{repository}/issues/comments?per_page=100",
        f"repos/{repository}/pulls/comments?per_page=100",
        f"repos/{repository}/comments?per_page=100",
        f"repos/{repository}/releases?per_page=100",
    )
    for endpoint in text_endpoints:
        payload = _gh_api(endpoint, paginate="per_page" in endpoint)
        findings.extend(
            _scan_text(
                f"GitHub API {endpoint}",
                payload.decode("utf-8", errors="replace"),
                private_terms,
            )
        )

    runs_payload = json.loads(
        _gh_api(f"repos/{repository}/actions/runs?per_page=100").decode("utf-8")
    )
    for run in runs_payload.get("workflow_runs", []):
        run_id = int(run["id"])
        result = subprocess.run(
            ["gh", "run", "view", str(run_id), "--repo", repository, "--log"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            findings.extend(
                _scan_text(f"GitHub Actions run {run_id}", result.stdout, private_terms)
            )

    artifacts_payload = json.loads(
        _gh_api(f"repos/{repository}/actions/artifacts?per_page=100").decode("utf-8")
    )
    release_pages = json.loads(
        _gh_api(f"repos/{repository}/releases?per_page=100", paginate=True).decode(
            "utf-8"
        )
    )
    releases = [release for page in release_pages for release in page]
    with tempfile.TemporaryDirectory(
        prefix="job-agent-github-assets-"
    ) as raw_directory:
        directory = Path(raw_directory)
        downloadable_assets = [
            (f"actions/artifacts/{int(artifact['id'])}/zip", artifact["name"] + ".zip")
            for artifact in artifacts_payload.get("artifacts", [])
            if not artifact.get("expired")
        ]
        downloadable_assets.extend(
            (
                f"releases/assets/{int(asset['id'])}",
                str(asset["name"]),
            )
            for release in releases
            for asset in release.get("assets", [])
        )
        for endpoint, name in downloadable_assets:
            safe_name = Path(name).name
            payload = _gh_api(f"repos/{repository}/{endpoint}", binary=True)
            target = directory / safe_name
            target.write_bytes(payload)
            if zipfile.is_zipfile(target) or tarfile.is_tarfile(target):
                findings.extend(_scan_archive(target, private_terms))
            else:
                findings.extend(_scan_blob(safe_name, payload, private_terms))

    if findings:
        print("GitHub surface privacy gate failed:")
        for finding in sorted(
            set(findings), key=lambda item: (item.location, item.category)
        ):
            print(f"- {finding.location}: {finding.category}")
        print("No matched values are printed. Review the named GitHub surface locally.")
        raise SystemExit(1)
    print(
        "GitHub surface audit passed: metadata, issues, comments, pull reviews, "
        "Actions logs/artifacts and release assets."
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-blocklist", type=Path)
    parser.add_argument(
        "--all-local-refs",
        action="store_true",
        help="Scan every local ref. Required before a push that publishes more than HEAD.",
    )
    parser.add_argument(
        "--remote-url",
        help="After publication, anonymously mirror and scan every public ref.",
    )
    parser.add_argument(
        "--remote-only",
        action="store_true",
        help="Skip local quality checks and run only the anonymous remote audit.",
    )
    parser.add_argument(
        "--github-repo",
        help="Also audit GitHub metadata and downloadable surfaces as OWNER/REPOSITORY.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    blocklist = _blocklist_path(args.private_blocklist)
    if not args.remote_only:
        _local_audit(blocklist, all_refs=args.all_local_refs)
    if args.remote_only and not (args.remote_url or args.github_repo):
        raise SystemExit("--remote-only requires --remote-url or --github-repo.")
    if args.remote_url:
        _anonymous_remote_audit(args.remote_url, blocklist)
    if args.github_repo:
        _github_surface_audit(args.github_repo, blocklist)
    print("Publication audit completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
