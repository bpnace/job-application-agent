from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import mimetypes
from pathlib import Path
import re
import shutil

from .cover_letter import display_role_title
from .document_names import cover_letter_filename
from .models import CandidateProfile, JobListing


EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class MailDraftResult:
    markdown_path: Path
    eml_path: Path
    apple_mail_script_path: Path | None = None


def discover_recipient(listing: JobListing, profile: CandidateProfile) -> str:
    haystack = "\n".join(
        [
            listing.apply_url,
            listing.application_method_note,
            listing.description,
            listing.raw_excerpt,
        ]
    )
    for candidate in EMAIL_RE.findall(haystack):
        if candidate.lower() != profile.email.lower():
            return candidate
    return ""


def default_email_subject(listing: JobListing) -> str:
    role = display_role_title(listing.title)
    if listing.language == "de":
        return f"Bewerbung als {role}"
    return f"Application for {role}"


def default_email_body(profile: CandidateProfile, listing: JobListing) -> str:
    role = display_role_title(listing.title)
    if listing.language == "de":
        return f"""Hallo {listing.company}-Team,

ich bewerbe mich auf die Stelle als {role}. Mein Lebenslauf und mein Anschreiben sind angehängt.

GitHub {profile.github}
LinkedIn {profile.linkedin}

Ich erzähle Ihnen gern persönlich mehr dazu.

Viele Grüße
{profile.name}
"""
    return f"""Hello {listing.company} team,

I am applying for the {role} role. My CV and cover letter are attached.

GitHub {profile.github}
LinkedIn {profile.linkedin}

I would be glad to share more in person.

Best regards
{profile.name}
"""


def write_mail_draft(
    package_dir: Path,
    profile: CandidateProfile,
    listing: JobListing,
    *,
    to: str = "",
    subject: str = "",
    body: str = "",
    attachments: list[Path] | None = None,
    write_apple_mail_script: bool = False,
) -> MailDraftResult:
    package_dir = package_dir.expanduser().resolve()
    package_dir.mkdir(parents=True, exist_ok=True)
    recipient = to or discover_recipient(listing, profile)
    if not recipient:
        raise ValueError("No recipient email found. Pass --to for this package.")
    subject = subject or default_email_subject(listing)
    body = body or default_email_body(profile, listing)
    attachment_paths = [path.expanduser().resolve() for path in attachments or []]
    for path in attachment_paths:
        if not path.exists():
            raise FileNotFoundError(f"Attachment does not exist: {path}")
    used_filenames: set[str] = set()
    attachment_items = [
        _stage_attachment(
            package_dir,
            path,
            _dedupe_filename(
                _attachment_filename(profile, listing, path), used_filenames
            ),
        )
        for path in attachment_paths
    ]

    markdown_path = package_dir / "email_draft.md"
    eml_path = package_dir / "email_draft_NOT_SENT.eml"
    markdown_path.write_text(
        _format_markdown_draft(profile, recipient, subject, body, attachment_items),
        encoding="utf-8",
    )
    _write_eml(
        eml_path,
        profile=profile,
        to=recipient,
        subject=subject,
        body=body,
        attachments=attachment_items,
    )
    apple_mail_script_path = None
    if write_apple_mail_script:
        apple_mail_script_path = package_dir / "apple_mail_draft_NOT_SENT.applescript"
        apple_mail_script_path.write_text(
            _apple_mail_script(profile, recipient, subject, body, attachment_items),
            encoding="utf-8",
        )
    return MailDraftResult(
        markdown_path=markdown_path,
        eml_path=eml_path,
        apple_mail_script_path=apple_mail_script_path,
    )


def _format_markdown_draft(
    profile: CandidateProfile,
    recipient: str,
    subject: str,
    body: str,
    attachments: list[tuple[Path, str]],
) -> str:
    attachment_lines = (
        "\n".join(f"- {filename}: {path}" for path, filename in attachments) or "- None"
    )
    return f"""# Email Draft Dry Run

Status: NOT SENT

From: {profile.name} <{profile.email}>
To: {recipient}
Subject: {subject}

## Attachments
{attachment_lines}

## Body

{body.rstrip()}
"""


def _write_eml(
    path: Path,
    *,
    profile: CandidateProfile,
    to: str,
    subject: str,
    body: str,
    attachments: list[tuple[Path, str]],
) -> None:
    message = EmailMessage()
    message["From"] = f"{profile.name} <{profile.email}>"
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="job-application-agent.local")
    message["X-Job-Agent-Dry-Run"] = "true"
    message.set_content(body.rstrip() + "\n")
    for attachment, filename in attachments:
        content_type, _encoding = mimetypes.guess_type(str(attachment))
        maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
        message.add_attachment(
            attachment.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )
    path.write_bytes(message.as_bytes())


def _apple_mail_script(
    profile: CandidateProfile,
    recipient: str,
    subject: str,
    body: str,
    attachments: list[tuple[Path, str]],
) -> str:
    attachment_lines = "\n".join(
        f'        make new attachment with properties {{file name:POSIX file "{_escape_applescript(str(path))}"}} at after the last paragraph'
        for path, _filename in attachments
    )
    return f"""-- Creates a visible Apple Mail draft only. It does not send the message.
tell application "Mail"
    activate
    set draftMessage to make new outgoing message with properties {{subject:"{_escape_applescript(subject)}", content:"{_escape_applescript(body.rstrip() + chr(10))}", visible:true, sender:"{_escape_applescript(profile.email)}"}}
    tell draftMessage
        make new to recipient at end of to recipients with properties {{address:"{_escape_applescript(recipient)}"}}
{attachment_lines}
    end tell
end tell
"""


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _attachment_filename(
    profile: CandidateProfile, listing: JobListing, path: Path
) -> str:
    if path.name == "cover_letter.pdf":
        return cover_letter_filename(profile, listing)
    if "cv" in path.stem.lower() or "lebenslauf" in path.stem.lower():
        return f"{_filename_part(profile.name)}_Lebenslauf.pdf"
    return _safe_filename(path.name)


def _filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß]+", "_", value).strip("_")
    return cleaned or "Dokument"


def _safe_filename(filename: str) -> str:
    path = Path(filename)
    stem = _filename_part(path.stem)
    suffix = re.sub(r"[^A-Za-z0-9.]", "", path.suffix)
    return f"{stem}{suffix}" if suffix else stem


def _dedupe_filename(filename: str, used_filenames: set[str]) -> str:
    path = Path(filename)
    stem = path.stem
    suffix = path.suffix
    candidate = filename
    index = 2
    while candidate.lower() in used_filenames:
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used_filenames.add(candidate.lower())
    return candidate


def _stage_attachment(
    package_dir: Path, source: Path, filename: str
) -> tuple[Path, str]:
    staged = package_dir / filename
    if source.resolve() != staged.resolve():
        shutil.copy2(source, staged)
    return staged, filename
