# Application Safety Notes

This repository contains reusable operational rules only. Store candidate facts,
documents, portal sessions, approvals, and portal-specific observations in the
local, ignored `$JOB_AGENT_HOME` directory.

## Mandatory pre-application checks

- Re-score every listing against the active local search policy before packaging
  or applying. A hand-curated shortlist never bypasses exclusions.
- Bind approval to the listing, document, research, and form-plan fingerprints.
  Stop before browser navigation if any fingerprint changed.
- Fill only facts present in the approved local candidate profile. New legal,
  financial, health, demographic, salary, availability, location, consent, or
  credential fields require human review.
- Login, account creation, magic links, MFA, CAPTCHA, and unrecognised portal
  states are blocking conditions. Record `needs_completion`; do not bypass them.
- A listing is `applied` only with final page evidence or a verified successful
  application request. An upload, a first step, a button click, or an
  intermediate request is not proof of submission.

## Cover letters and research

- Address the named contact from public research, otherwise the verified
  employer. Never address a portal or job board.
- Use only company facts and contact details that are stored in the approved
  research artifact and linked to their public source URLs.
- A cover letter may use only the local profile summary, skills, and proof
  points. Never invent experience, credentials, or an answer to a screening
  question.
- Run the local Humanizer policy before finalizing a letter. The policy is a
  private local file and is not committed to this repository.
- Keep filenames neutral and short. Do not include the candidate's real name,
  full employer name, portal name, or personal information.

## Portal navigation

- Treat search boxes, cookie controls, newsletter fields, and login forms as
  non-application fields until the actual application form is visible.
- Inspect all reachable form steps before selecting a document strategy. Upload
  CV and cover letter separately when separate fields exist. The combined-PDF
  fallback requires explicit verification that no later cover-letter field
  exists.
- Keep browser sessions isolated. A user-supplied authenticated session is
  valid only in the browser context that will continue the application.
- Save a local audit trail for each decision: package, form plan, research,
  approval manifest, blockers, and final submit evidence.

## Known general portal patterns

- Job-board pages can lead to an employer ATS, a login wall, or a multi-step
  downstream flow. Resolve and inspect the downstream route before creating a
  form plan.
- Personio-style applications require either a final thank-you state or a
  confirmed successful application request. Validation errors and duplicate
  errors are `needs_completion`, never `applied`.
- JOIN-style applications are multi-step. Treat the first document upload and
  `Continue` action as intermediate. Only the final confirmation after the last
  consent/submit action is completion evidence.
- If a portal requires a named document type such as a certificate or reference
  and no matching approved document exists, stop for review. Do not upload an
  unrelated document merely to satisfy a required field.
- Do not continue post-application upsells, marketing options, talent pools, or
  education offers unless they are explicitly approved for that listing.

## Maintaining these notes

- Add a rule only when it is reusable and contains no candidate, employer
  contact, job, salary, address, demographic, session, or submission data.
- Keep observations tied to a real person or a specific application only in the
  local tracker and run artifacts; both must remain outside Git.
