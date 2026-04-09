# Claude Code — Project Instructions

## About this project

<!-- Replace this section with a brief description of what this project is,
     how it's deployed, and what systems it connects to. Keep it to 5-10 lines.
     This is the first thing a new session reads — make it count. -->

## Authentication

This project authenticates via GCP service account impersonation. All API calls
(Sheets, Cloud Run, Salesforce, Secret Manager) go through:

    hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com

Developers authenticate with their own @hoperises.org account and impersonate
the service account. Setup:

    gcloud auth application-default login \
      --impersonate-service-account hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com

Do NOT use personal ADC (`gcloud auth application-default login` without
impersonation). Do NOT create or download service account key files.

## Stack Learnings (canonical source)

Stack-level learnings live in ONE place:
- Repo: `Hope-Rises-International/hri-template-repository`
- File: `hri-stack-learnings.md`
- Read before any infrastructure, auth, deployment, or tooling work.
- Update directly via GitHub API when you discover a stack-level gotcha. See session-end protocol below.

Do NOT create a local `learnings.md` or `hri-stack-learnings.md` in this repo. If one exists, merge any unique content upstream and delete the local copy.

## Project knowledge

<!-- This section grows over time. Every session that makes meaningful changes
     should append what it learned. This is where compound value accrues.

     Good entries answer: What would the NEXT session need to know?
     - Decisions made and WHY (not just what changed — git log has that)
     - Things that are fragile or non-obvious
     - What was tried and didn't work (so nobody tries it again)
     - Patterns discovered in the data or the APIs
     - Gotchas that aren't obvious from reading the code

     Bad entries: "Updated foo.py" (that's a commit message, not knowledge) -->

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
