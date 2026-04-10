# Aegis Non Donor Pipeline — Build Specification

**Owner:** Bill Simmons, CEO — Hope Rises International
**Target:** Claude Code build
**Date:** April 9, 2026 (revised from March 13, 2026)
**Status:** Phase A — Complete, Production. Phase B — In Build.

---

## Purpose

Replace the current CoWork browser-automation workflow for processing Aegis (Moore DM Group) Non Donor CSV files. The CoWork version navigates Chrome to paste data into Google Sheets — fragile and slow. This pipeline runs as Python, writes to Google Sheets via API, and produces clean output files with zero browser dependency.

---

## Pipeline Overview (All Phases)

```
Phase A (complete):
  Drive folder CSVs → Python triage → Kill List Google Sheet (API write)
                                     → Cleaned Non Donor CSV (Drive output)

Phase B (in build):
  Cloud Scheduler → Cloud Run → Scan Drive "Files to Process" folder
                               → Phase A logic runs automatically
                               → SF-bound rows → Staging Google Sheet
                               → Email notification to Bekah

Phase C (future):
  Bekah reviews staging Google Sheet → Approves
                                     → Cloud Run pushes to Salesforce via REST API
                                     → Confirmation email
```

---

## Prerequisites for Phase A

Before Claude Code begins the build:

1. **Sample Non Donor CSV files** — ✅ Bekah has recent files in a Google Drive folder. CC should download them as the first step and validate column headers match this spec.
2. **Google Sheets API access** — ✅ GCP service account setup is complete. CC should verify the impersonated SA has editor access on the Kill List sheet by reading a cell as its first step. If permission error, share the sheet with the service account email being used for local development.
3. **Service account impersonation configured** — ✅ Complete. The developer running CC must have run `gcloud auth application-default login --impersonate-service-account hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com`.

### Expected Data Volume

Non Donor files typically contain 50–200 rows per file, rarely over 500. CC should use this range as a sanity check on the output report. If a file contains significantly more or fewer rows, flag it rather than silently processing.

---

## Phase A: Detailed Specification

### Input

Two CSV files downloaded from the Aegis FTP site. Both are the same column format:

| Column | Description |
|--------|-------------|
| CONSID | Constituent ID (donor identifier in Salesforce) |
| FINDER | Finder number (matchback/acquisition identifier) |
| FNAME1 | First name (primary) |
| LNAME1 | Last name (primary) |
| FNAME2 | First name (secondary/spouse) |
| LNAME2 | Last name (secondary/spouse) |
| TITLE1 | Salutation (primary) |
| TITLE2 | Salutation (secondary) |
| SUFFX1 | Suffix (primary) |
| SUFFX2 | Suffix (secondary) |
| STREET | Street address |
| CITY | City |
| STATE | State |
| ZIPCOD | Zip code |
| COUNTRY | Country |
| PHONE | Phone number |
| EMAIL | Email address |
| SRCCDE | Appeal/source code |
| DNRAMT | Donation amount |
| DNRDDT | Deposit date |
| CONDEC | Deceased flags |
| CONMAI | Mail preference flags |
| CONOPT | Opt-out flags |
| TRFLAG | Transaction flags |
| TRACK | Acknowledgement preference |
| TRCHK# | Check reference number |
| TRPTYP | Payment method |
| TRMBID | Cager record note |

**File naming convention:** Files containing "Non Donors" in the filename are Non Donor files. The other file is the Household (donation) file. Phase A processes **only** Non Donor files.

### Processing Logic

**This is the core triage.** All CSV columns should be read as strings (no type inference).

#### Step 1: Sort by FINDER

Sort the entire dataframe by FINDER column, empty values last.

#### Step 2: Reclassify known donor FINDERs

Some FINDER values are actually donor constituent IDs that belong in the CONSID column. Identify them by prefix:

- FINDER starts with `0` → move to CONSID
- FINDER starts with `7` → move to CONSID
- FINDER starts with `S` → move to CONSID

For matching rows: copy FINDER value to CONSID, then clear FINDER.

#### Step 3: Re-sort by FINDER

Sort again by FINDER. Empty FINDER values go to the bottom.

#### Step 4: Split into two sets

- **Kill List rows:** FINDER is non-empty after Steps 1-3. These are acquisition contacts requesting mail suppression.
- **Salesforce-bound rows:** FINDER is empty. These are existing donors with data updates (deceased, address change, opt-out, comments, white mail).

#### Step 5: Format Kill List output

From Kill List rows, produce a dataset with these columns in this order:

| Output Column | Source Column |
|---------------|--------------|
| First Name | FNAME1 |
| Last Name | LNAME1 |
| Street 1 | STREET |
| Street 2 | *(empty — new column)* |
| City | CITY |
| State | STATE |
| Zip | ZIPCOD |
| Suppress Date | Today's date as `m/d/yy` |

Also produce a full Kill List CSV preserving all original columns **except** SUFFX1 (drop it), and **adding** a STREET2 column (empty) after STREET. Save as `{original_filename}_Master Kill List.csv`.

**Note:** The Master Kill List CSV is a validation artifact — it exists so Bill can compare the CSV output row-for-row against what was written to the Google Sheet, confirming the triage and Sheets write are correct. Once validated across a few runs, this CSV and the `Uploaded NonDonor Files/` subfolder will be removed from the pipeline. The Google Sheet becomes the sole Kill List record.

#### Step 6: Format Salesforce-bound output

Save the Salesforce-bound rows as a cleaned CSV with all original columns intact. Use the original filename.

### Google Sheets Integration

**Kill List Google Sheet ID:** `11dM2Pf-E195rJUnF79rMHhN5RUb0L-03fS8nZ4WZw7o`

**Sheet name:** First sheet (gid=0)

**Columns (A through H):**
| A | B | C | D | E | F | G | H |
|---|---|---|---|---|---|---|---|
| First Name | Last Name | Street 1 | Street 2 | City | State | Zip | Suppress Date |

**Write method:** Append rows after the last populated row. Use the Google Sheets API (`googleapiclient`). Do NOT use browser navigation.

**Important — sparse data hazard:** The existing Kill List sheet has gaps (empty rows and cells scattered throughout) caused by the CoWork browser automation not reliably finding the true end of data. Do NOT use "find first empty cell in column A" as the append point — that will insert rows into the middle of existing data. Instead, scan the full extent of the sheet to find the absolute last row containing any data in any column, then append starting at the next row.

### File I/O (Google Drive)

All file I/O uses Drive API — no local filesystem dependency.

#### Drive folder structure
```
SF Uploader (root: 1siDdLdqDHavOOj7gI_DVitdqBmc_m3X6)/
├── Files to Process/          (1JCzwnbqZrfhHyruSlIJ_z7AyIHOjr3md) ← input drop zone
├── Claude Files to Delete/    (1TU-3i7dZI5fiGHJpfylbJcJxgHyvekJa) ← archived originals
├── Claude Processed Non Donor Files/ (1mq5KGIHvMErycZ6U1RAikzzOu3OaKQrs) ← SF-bound CSVs
│   └── Uploaded NonDonor Files/      (1KNUK-mx-6qv_FjnZ4ubwQh0T-OcOqg_j) ← Master Kill List CSVs
```

**Critical:** Original file only moves to "Files to Delete" after ALL steps succeed. If any step fails, stop immediately and leave files in place.

### Output Report

After processing, print a summary:
- Number of files processed
- Per file: rows to Kill List vs. rows kept for Salesforce
- Google Sheet: total rows appended, starting and ending row numbers
- Drive file IDs and folder locations for all outputs

---

## Salesforce Field Mapping (Reference for Phase C)

Both Household and Non Donor files target the same Salesforce object: `npsp__DataImport__c` (NPSP Data Import). Operation: **Insert**.

| CSV Column | Salesforce API Field |
|------------|---------------------|
| Batch Name | `Batch_Name__c` |
| CITY | `Constituent_City__c` |
| CONDEC | `DM_Deceased_Flags__c` |
| CONMAI | `DM_Mail_Preference_Flags__c` |
| CONOPT | `DM_Opt_Out_Flags__c` |
| CONSID | `Inbound_Constituent_Id__c` |
| COUNTRY | `Constituent_Country__c` |
| DNRAMT | `npsp__Donation_Amount__c` |
| DNRDDT | `DM_Deposit_Date__c` |
| Donation Donor | `npsp__Donation_Donor__c` |
| Donation Gift Source | `Donation_Gift_Source__c` |
| EMAIL | `npsp__Contact1_Personal_Email__c` |
| FINDER | `Finder_Number__c` |
| FNAME1 | `npsp__Contact1_Firstname__c` |
| FNAME2 | `npsp__Contact2_Firstname__c` |
| LNAME1 | `npsp__Contact1_Lastname__c` |
| LNAME2 | `npsp__Contact2_Lastname__c` |
| PHONE | `npsp__Contact1_Home_Phone__c` |
| SRCCDE | `Appeal_Code__c` |
| STATE | `Constituent_State__c` |
| STREET | `Constituent_Street__c` |
| SUFFX1 | `Contact1_Suffix__c` |
| SUFFX2 | `Contact2_Suffix__c` |
| TITLE1 | `npsp__Contact1_Salutation__c` |
| TITLE2 | `npsp__Contact2_Salutation__c` |
| TRACK | `DM_Acknowledgement_Preference__c` |
| TRCHK# | `npsp__Payment_Check_Reference_Number__c` |
| TRFLAG | `DM_Transaction_Flags__c` |
| TRMBID | `Cager_Record_Note__c` |
| TRPTYP | `npsp__Payment_Method__c` |
| ZIPCOD | `Constituent_Postal_Code__c` |

**Source:** `Aegis_Import_Mapping.sdl` (Data Loader mapping file, dated November 16, 2023)

---

## Phase B: Cloud Run Automation

**Objective:** Automate daily processing. Cloud Run scans Drive folder, runs triage, writes to sheets, sends notification email.

### Architecture
- **Runtime:** Cloud Run service on `hri-receipt-automation` GCP project
- **Endpoints:** `/process` (triggered by Cloud Scheduler daily)
- **Schedule:** Cloud Scheduler triggers `/process` daily (time TBD based on Bekah's workflow)
- **Service account:** Runs as `hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com`
- **Repository:** `hri-sf-uploader` under `Hope-Rises-International` GitHub org
- **No SFTP** — Bekah manually drops files into the Drive "Files to Process" folder
- **Flow:** Scan Drive folder → download CSVs → run triage → write Kill List to Sheet → write SF-bound rows to staging Sheet → send email notification to Bekah

### Staging Sheet
A Google Sheet where Bekah reviews Non Donor records before they go to Salesforce. Two tabs:
- "Non Donor" — SF-bound records with Status (pending/approved/uploaded/error) and Processed Date columns
- "Household" — same structure for future Household file processing

### Notification Email
Sent to Bekah (bschwanbeck@hoperises.org) after processing. Contains:
- File date and record counts
- Link to Kill List sheet (for awareness, already written)
- Link to staging sheet (requires her review and approval)

---

## Phase C: Salesforce Push (Future Build)

**Objective:** Replace Data Loader entirely. Approved records go to Salesforce via REST API.

### Architecture
- **Trigger:** Bekah approves in staging sheet (custom menu button, Apps Script calls Cloud Run)
- **Endpoint:** Cloud Run `/push` endpoint on same service as Phase B
- **Operation:** Insert records to `npsp__DataImport__c` using Salesforce REST API SObject Collections endpoint (up to 200 records per request)
- **Do NOT use Bulk API 2.0** — overkill for this volume
- **Confirmation:** Email to Bekah with insert results
- **Cleanup:** Mark staging sheet rows as uploaded with timestamp

### BDI Processing Remains Manual
Bekah triggers BDI manually in Salesforce after API insert. Do NOT automate BDI.

### Batch Naming Convention
- Non Donor files: `ALMMMDDYYYY Non Donors` (e.g., `ALM04102026 Non Donors`)
- Household files: `ALMMMDDYYYY Households` (e.g., `ALM04102026 Households`)

---

## Technical Notes

- **All CSV columns read as strings.** No type inference.
- **Auth (local):** ADC via service account impersonation of `hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com`.
- **Auth (Cloud Run):** ADC resolves automatically to runtime SA. No code changes between local and deployed.
- **No browser automation.** The entire point of this build is to eliminate the CoWork Chrome navigation pattern.
- **Date format for Kill List:** `m/d/yy` format. Google Sheets auto-conversion is expected.
- **Related automation:** `hri-gmail-pdf-deposit` handles Aegis PDF attachments (different channel, no overlap).

---

## Context: Record Types in Non Donor File

1. **Existing donors with updates** — Have a CONSID (or reclassified FINDER). Route to Salesforce.
2. **Acquisition suppressions** — Have a FINDER starting with `3` or other acquisition prefixes. Route to Kill List.
3. **White mail** — No scannable reply device. Route to Salesforce (FINDER empty after triage). BDI matches by address.
