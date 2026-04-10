# Claude Code — Project Instructions

## About this project

Aegis Non Donor CSV pipeline — replaces fragile CoWork browser automation with
a Python CLI that triages Aegis (Moore DM Group) Non Donor files, writes
suppression records to a Kill List Google Sheet via API, and produces cleaned
CSVs for Salesforce import.

- **Phase A:** Complete, production. CLI triage → Kill List Sheet + cleaned CSV output
- **Phase B:** Cloud Run `/process` endpoint, Drive-based (no SFTP), daily schedule TBD
- **Phase C (future):** `/push` endpoint, Apps Script menu trigger on staging sheet, SF insert via SObject Collections

**Key systems:** Google Sheets API, Google Drive API, Gmail API, GCP ADC,
Salesforce `npsp__DataImport__c` (Phase C). No browser automation. No SFTP in any phase.

## Authentication

### Phase A (local development)

Authenticate via GCP service account impersonation. The service account used
for local development is:

    hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com

Setup:

    gcloud auth application-default login \
      --impersonate-service-account hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com

Do NOT use personal ADC without impersonation. Do NOT create or download SA key files.

**First-run verification:** Before processing any files, read a cell from the
Kill List sheet to confirm the SA has editor access. If permission error, share
the sheet with the SA email.

### Phase B/C (Cloud Run)

ADC resolves automatically to Cloud Run runtime identity:
`hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com`. The Kill List
sheet must be shared with this SA before Phase B deployment. No auth code
changes between local and deployed.

**GCP project:** `hri-receipt-automation` — same project as all other
SF-connected Cloud Run services (`sync-receipts`, `campaign-scorecard-refresh`,
`sf-data-refresh`, `donor-brief-builder`).

## Stack Learnings (canonical source)

Stack-level learnings live in ONE place:
- Repo: `Hope-Rises-International/hri-template-repository`
- File: `hri-stack-learnings.md`
- Read before any infrastructure, auth, deployment, or tooling work.
- Update directly via GitHub API when you discover a stack-level gotcha. See session-end protocol below.

Do NOT create a local `learnings.md` or `hri-stack-learnings.md` in this repo. If one exists, merge any unique content upstream and delete the local copy.

## Key Resources

- **Kill List Google Sheet:** `11dM2Pf-E195rJUnF79rMHhN5RUb0L-03fS8nZ4WZw7o` (Sheet 1 / gid=0)
- **Drive working folder:** https://drive.google.com/drive/u/0/folders/1siDdLdqDHavOOj7gI_DVitdqBmc_m3X6
  - Root: `1siDdLdqDHavOOj7gI_DVitdqBmc_m3X6` (SF Uploader)
  - Input: `1JCzwnbqZrfhHyruSlIJ_z7AyIHOjr3md` (Files to Process)
  - Archive: `1TU-3i7dZI5fiGHJpfylbJcJxgHyvekJa` (Claude Files to Delete)
  - Output: `1mq5KGIHvMErycZ6U1RAikzzOu3OaKQrs` (Claude Processed Non Donor Files)
  - Kill CSVs: `1KNUK-mx-6qv_FjnZ4ubwQh0T-OcOqg_j` (Uploaded NonDonor Files)
- **Aegis Staging Sheet:** `1qSxi7YBtZ2VsYpDXGKGz0539Gc9bIMKIR1PTNj7ibCU`
  - Tabs: "Non Donor" (SF-bound records), "Household" (future)
  - Columns: Salesforce API field names + Status + Processed Date
  - Shared with Bill, Bekah, and SA
- **Build spec:** `Aegis_NonDonor_Pipeline_Build_Spec.md` in repo root
- **Related system:** `hri-gmail-pdf-deposit` handles Aegis PDF attachments (different channel, no overlap)

## Pipeline Logic Summary

### Input
- Two CSV files from Aegis FTP: one Non Donor, one Household
- Phase A processes **only** Non Donor files (filename contains "Non Donors")
- **All columns read as strings** — no type inference (zip codes, IDs, flags)

### Triage (Steps 1–4)
1. Sort by FINDER (empties last)
2. Reclassify: FINDER starting with `0`, `7`, or `S` → move value to CONSID, clear FINDER
3. Re-sort by FINDER (empties last)
4. Split: non-empty FINDER → Kill List; empty FINDER → Salesforce-bound

### Kill List Output (Step 5)
- 8-column dataset: First Name, Last Name, Street 1, Street 2 (empty), City, State, Zip, Suppress Date (`m/d/yy`)
- Master Kill List CSV: all original columns minus SUFFX1, plus empty STREET2 after STREET
- Append to Google Sheet after last populated row (**scan full sheet** — sparse data, gaps from old CoWork automation)

### Salesforce Output (Step 6)
- Cleaned CSV with all original columns, original filename

### File I/O (Google Drive)
All file I/O uses Drive API — no local filesystem dependency.
```
SF Uploader (Drive root)/
├── Files to Process/                            ← drop Non Donor CSVs here
├── Claude Files to Delete/                      ← originals archived (LAST step)
├── Claude Processed Non Donor Files/            ← SF-bound CSVs
│   └── Uploaded NonDonor Files/                 ← Master Kill List CSVs (validation artifact)
```

**Critical:** If any step fails, stop immediately and leave files in place.

**Duplicate prevention:** Before uploading, checks if a file with that name
already exists in the target folder. Before processing, checks if an output
already exists in the Processed folder (skip-if-already-processed).

**Originals stay in Files to Process** — operator clears them manually after review.

### Expected Volume
Typical Non Donor files: 50–200 rows, rarely over 500. Flag anomalies.

## Salesforce Mapping (Phase C reference)

Target object: `npsp__DataImport__c` (Insert). Batch name format:
`ALMMMDDYYYY Non Donors` / `ALMMMDDYYYY Households`. Full field mapping in build spec.

## Architecture

### Code structure
- `config.py` — shared config (folder IDs, sheet IDs, API scopes, `get_services()`)
- `triage.py` — pure triage logic (sort, reclassify, split, CSV I/O). No API calls.
- `pipeline.py` — CLI entry point for manual runs (Phase A)
- `app.py` — Flask Cloud Run service with `/process` and `/health` endpoints (Phase B)
- `Dockerfile` — Cloud Run deployment container

### Phase B: Cloud Run `/process` endpoint
- Scans Drive "Files to Process" folder for new Non Donor CSVs
- Runs triage (identical logic to Phase A CLI)
- Writes Kill List rows to production sheet
- Writes SF-bound rows to staging sheet "Non Donor" tab with status "pending"
- Sends notification email to Bekah via Gmail API
- No SFTP — Bekah manually drops files into Drive folder

### Phase C: `/push` endpoint (future)
- Apps Script custom menu button on staging sheet triggers Cloud Run `/push`
- Reads approved rows, inserts to `npsp__DataImport__c` via SObject Collections
- Sends confirmation email, marks rows as uploaded
- Bekah triggers BDI manually — do NOT automate

### Cloud Scheduler (not yet created)
```bash
gcloud scheduler jobs create http aegis-non-donor-process \
  --project=hri-receipt-automation \
  --location=us-east1 \
  --schedule="0 8 * * 1-5" \
  --uri="https://CLOUD_RUN_URL/process" \
  --http-method=POST \
  --oidc-service-account-email=hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com \
  --oidc-token-audience="https://CLOUD_RUN_URL"
```
Schedule TBD — waiting on Bekah for typical file arrival time.

### Post-Deploy Checklist
After deploying to hri-receipt-automation, verify these existing services still work:
- sync-receipts, campaign-scorecard-refresh, sf-data-refresh, donor-brief-builder
Force-run all Cloud Scheduler jobs and confirm invocation in Cloud Run logs.

### Known Limitations
- ADC impersonation cannot move or delete files owned by other users in Drive
- Originals stay in Files to Process — operator clears manually
- SA Drive storage quota exceeded — new sheets/files must be created inside shared folders (not SA root)

## Project knowledge

### Session 1 — 2026-04-09: Phase A build + Drive refactor
- Built Phase A triage pipeline (`pipeline.py`) and verified with sample data
- Refactored from local filesystem to Google Drive API for all file I/O
- SA (`hri-sfdc-sync@hri-receipt-automation`) shared on Drive folder and Kill List sheet
- Actual CSV columns differ from spec: file has `CGDT`, `Donation Gift Source`, `Batch Name`, `Donation Donor` instead of `DNRAMT`, `DNRDDT`, `TRFLAG`, `TRACK`, `TRCHK#`, `TRPTYP`, `TRMBID`. Pipeline works with whatever columns exist.
- Sample data: 113 rows → 77 kill list (prefix `3`), 2 reclassified (`S`, `0` → CONSID), 36 SF-bound
- Kill List sheet had ~14,073 existing rows from CoWork automation, with sparse gaps

### Session 2 — 2026-04-09: I/O cleanup + test sheet
- Removed PROCESSED_ rename/move-to-archive approach — originals stay in Files to Process, operator clears manually
- Added skip-if-already-processed: checks for existing output in Processed folder before re-processing
- Added duplicate prevention: checks target folder before uploading any CSV
- Switched Kill List sheet ID to test sheet (`1I-LBd6AQO0EhcHX1dqBHzbSBr9w12yNzgIlgN_Jtb3M`) — production ID preserved in code comment
- Empty file handling: skip cleanly (no archive attempt), Household files ignored by query

### Session 3 — 2026-04-10: Phase B build
- Created Aegis Staging sheet (`1qSxi7YBtZ2VsYpDXGKGz0539Gc9bIMKIR1PTNj7ibCU`) with Non Donor and Household tabs
- Refactored: extracted `config.py` (shared config), `triage.py` (pure logic), kept `pipeline.py` as CLI
- Built `app.py` — Flask Cloud Run service with `/process` endpoint
- `/process` endpoint: triage + Kill List sheet + staging sheet write + Gmail notification to Bekah
- SA can't create top-level Drive files (quota exceeded) — must create inside shared folders
- No SFTP in any phase — Bekah drops files manually into Drive "Files to Process"
- Staging sheet columns use Salesforce API field names + Status + Processed Date

---

## Session Start

**The full protocol lives in one place:** `session-start-protocol.md` in `hri-template-repository`.

At session start, fetch and follow it:

```bash
gh api /repos/Hope-Rises-International/hri-template-repository/contents/session-start-protocol.md \
  --jq '.content' | base64 -d > /tmp/session-start-protocol.md
```

Then read `/tmp/session-start-protocol.md` and execute all steps.

---

## Session-End Protocol

**The full protocol lives in one place:** `session-end-protocol.md` in `hri-template-repository`.

At session close, fetch and follow it:

```bash
gh api /repos/Hope-Rises-International/hri-template-repository/contents/session-end-protocol.md \
  --jq '.content' | base64 -d > /tmp/session-end-protocol.md
```

Then read `/tmp/session-end-protocol.md` and execute all steps.

This ensures every repo uses the latest protocol without needing per-repo updates.
