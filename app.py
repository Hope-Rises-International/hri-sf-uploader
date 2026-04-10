"""Aegis Non Donor pipeline — Cloud Run service.

Endpoints:
  POST /process  — Scan Drive for new Non Donor CSVs, triage, write to sheets, notify.
"""

import base64
import csv
import io
import json
import logging
import os
import tempfile
from datetime import datetime
from email.mime.text import MIMEText

from flask import Flask, jsonify, request
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from config import (
    INPUT_FOLDER_ID, PROCESSED_FOLDER_ID, UPLOADED_FOLDER_ID,
    KILL_LIST_SHEET_ID, KILL_LIST_SHEET_NAME,
    STAGING_SHEET_ID, STAGING_NON_DONOR_TAB,
    MIN_EXPECTED_ROWS, MAX_EXPECTED_ROWS,
    NOTIFY_EMAIL,
    get_services,
)
from triage import (
    run_triage, format_kill_list_sheet_rows,
    write_master_kill_list_csv, write_sf_csv,
    is_empty_csv, read_csv,
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# CSV-to-Salesforce field mapping for staging sheet
CSV_TO_SF = {
    "Batch Name": "Batch_Name__c",
    "CONSID": "Inbound_Constituent_Id__c",
    "FINDER": "Finder_Number__c",
    "TITLE1": "npsp__Contact1_Salutation__c",
    "FNAME1": "npsp__Contact1_Firstname__c",
    "LNAME1": "npsp__Contact1_Lastname__c",
    "SUFFX1": "Contact1_Suffix__c",
    "TITLE2": "npsp__Contact2_Salutation__c",
    "FNAME2": "npsp__Contact2_Firstname__c",
    "LNAME2": "npsp__Contact2_Lastname__c",
    "SUFFX2": "Contact2_Suffix__c",
    "STREET": "Constituent_Street__c",
    "CITY": "Constituent_City__c",
    "STATE": "Constituent_State__c",
    "ZIPCOD": "Constituent_Postal_Code__c",
    "COUNTRY": "Constituent_Country__c",
    "PHONE": "npsp__Contact1_Home_Phone__c",
    "EMAIL": "npsp__Contact1_Personal_Email__c",
    "SRCCDE": "Appeal_Code__c",
    "DNRAMT": "npsp__Donation_Amount__c",
    "DNRDDT": "DM_Deposit_Date__c",
    "Donation Donor": "npsp__Donation_Donor__c",
    "Donation Gift Source": "Donation_Gift_Source__c",
    "CONDEC": "DM_Deceased_Flags__c",
    "CONMAI": "DM_Mail_Preference_Flags__c",
    "CONOPT": "DM_Opt_Out_Flags__c",
    "TRFLAG": "DM_Transaction_Flags__c",
    "TRACK": "DM_Acknowledgement_Preference__c",
    "TRCHK#": "npsp__Payment_Check_Reference_Number__c",
    "TRPTYP": "npsp__Payment_Method__c",
    "TRMBID": "Cager_Record_Note__c",
    "CGDT": None,  # Not mapped — processing date, not a SF field
}

# Staging sheet header order (must match the sheet)
STAGING_HEADERS = [
    "Batch_Name__c", "Inbound_Constituent_Id__c", "Finder_Number__c",
    "npsp__Contact1_Salutation__c", "npsp__Contact1_Firstname__c",
    "npsp__Contact1_Lastname__c", "Contact1_Suffix__c",
    "npsp__Contact2_Salutation__c", "npsp__Contact2_Firstname__c",
    "npsp__Contact2_Lastname__c", "Contact2_Suffix__c",
    "Constituent_Street__c", "Constituent_City__c", "Constituent_State__c",
    "Constituent_Postal_Code__c", "Constituent_Country__c",
    "npsp__Contact1_Home_Phone__c", "npsp__Contact1_Personal_Email__c",
    "Appeal_Code__c", "npsp__Donation_Amount__c", "DM_Deposit_Date__c",
    "npsp__Donation_Donor__c", "Donation_Gift_Source__c",
    "DM_Deceased_Flags__c", "DM_Mail_Preference_Flags__c",
    "DM_Opt_Out_Flags__c", "DM_Transaction_Flags__c",
    "DM_Acknowledgement_Preference__c",
    "npsp__Payment_Check_Reference_Number__c", "npsp__Payment_Method__c",
    "Cager_Record_Note__c", "Status", "Processed Date",
]


# ── Drive helpers ──────────────────────────────────────────────────────────────

def list_non_donor_csvs(drive):
    q = (
        f"'{INPUT_FOLDER_ID}' in parents"
        " and name contains 'Non Donors'"
        " and mimeType='text/csv'"
        " and trashed=false"
    )
    result = drive.files().list(
        q=q, supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id, name)",
    ).execute()
    return result.get("files", [])


def file_exists_in_folder(drive, filename, folder_id):
    q = (
        f"'{folder_id}' in parents"
        f" and name = '{filename}'"
        " and trashed=false"
    )
    result = drive.files().list(
        q=q, supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id)", pageSize=1,
    ).execute()
    return len(result.get("files", [])) > 0


def download_file(drive, file_id, dest_path):
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def upload_csv(drive, local_path, filename, parent_folder_id):
    media = MediaFileUpload(local_path, mimetype="text/csv")
    body = {"name": filename, "parents": [parent_folder_id]}
    result = drive.files().create(
        body=body, media_body=media, supportsAllDrives=True,
        fields="id, name",
    ).execute()
    return result["id"]


# ── Sheets helpers ─────────────────────────────────────────────────────────────

def find_last_row(sheets, sheet_id, sheet_name, col_range="A:H"):
    """Find absolute last row with data in any column."""
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!{col_range}",
        majorDimension="ROWS",
    ).execute()
    rows = result.get("values", [])
    last = 0
    for i, row in enumerate(rows):
        if any(cell.strip() for cell in row if cell):
            last = i + 1
    return last


def append_to_kill_list(sheets, rows):
    """Append rows to the Kill List sheet."""
    last_row = find_last_row(sheets, KILL_LIST_SHEET_ID, KILL_LIST_SHEET_NAME)
    start_row = last_row + 1
    result = sheets.spreadsheets().values().update(
        spreadsheetId=KILL_LIST_SHEET_ID,
        range=f"{KILL_LIST_SHEET_NAME}!A{start_row}",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    updated = result.get("updatedRows", 0)
    return start_row, start_row + updated - 1, updated


def append_to_staging(sheets, sf_rows, batch_name):
    """Write SF-bound rows to the staging sheet Non Donor tab."""
    today = datetime.now().strftime("%-m/%-d/%y")
    staging_rows = []
    for row in sf_rows:
        mapped = {}
        for csv_col, sf_col in CSV_TO_SF.items():
            if sf_col and csv_col in row:
                mapped[sf_col] = row[csv_col]
        mapped["Batch_Name__c"] = batch_name
        # Build row in header order
        staging_row = [mapped.get(h, "") for h in STAGING_HEADERS[:-2]]
        staging_row.append("pending")  # Status
        staging_row.append(today)       # Processed Date
        staging_rows.append(staging_row)

    if not staging_rows:
        return 0

    last_row = find_last_row(
        sheets, STAGING_SHEET_ID, STAGING_NON_DONOR_TAB,
        f"A:{chr(64 + len(STAGING_HEADERS))}"
    )
    start_row = last_row + 1
    sheets.spreadsheets().values().update(
        spreadsheetId=STAGING_SHEET_ID,
        range=f"'{STAGING_NON_DONOR_TAB}'!A{start_row}",
        valueInputOption="USER_ENTERED",
        body={"values": staging_rows},
    ).execute()
    return len(staging_rows)


# ── Email notification ─────────────────────────────────────────────────────────

def send_notification(gmail, results):
    """Send processing summary email to Bekah."""
    total_kill = sum(r["kill_list_rows"] for r in results)
    total_sf = sum(r["sf_rows"] for r in results)

    kill_list_url = f"https://docs.google.com/spreadsheets/d/{KILL_LIST_SHEET_ID}"
    staging_url = f"https://docs.google.com/spreadsheets/d/{STAGING_SHEET_ID}"

    lines = [
        "Hi Bekah,",
        "",
        f"The Aegis Non Donor pipeline processed {len(results)} file(s).",
        "",
    ]
    for r in results:
        lines.append(f"  {r['filename']}:")
        lines.append(f"    Total rows: {r['total_rows']}")
        lines.append(f"    Kill List: {r['kill_list_rows']}")
        lines.append(f"    Salesforce staging: {r['sf_rows']}")
        lines.append("")

    lines.extend([
        f"Totals: {total_kill} Kill List + {total_sf} Salesforce",
        "",
        f"Kill List sheet: {kill_list_url}",
        f"Staging sheet (review required): {staging_url}",
        "",
        "The Kill List rows have already been written. Please review the staging",
        "sheet and approve records for Salesforce upload when ready.",
        "",
        "— Aegis Pipeline (automated)",
    ])

    body = "\n".join(lines)
    message = MIMEText(body)
    message["to"] = NOTIFY_EMAIL
    message["subject"] = f"Aegis Non Donor Processing Complete — {datetime.now().strftime('%m/%d/%Y')}"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    gmail.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()
    log.info(f"Notification email sent to {NOTIFY_EMAIL}")


# ── Process endpoint ───────────────────────────────────────────────────────────

def process_files():
    """Core processing logic shared by endpoint and CLI."""
    sheets, drive, gmail = get_services()

    # Find files
    files = list_non_donor_csvs(drive)
    if not files:
        return {"status": "ok", "message": "No Non Donor files found. Nothing to do.", "processed": 0}

    results = []
    skipped = 0

    with tempfile.TemporaryDirectory(prefix="aegis_") as tmpdir:
        for file_info in files:
            file_id = file_info["id"]
            filename = file_info["name"]
            log.info(f"Processing: {filename} (ID: {file_id})")

            # Skip if already processed
            if file_exists_in_folder(drive, filename, PROCESSED_FOLDER_ID):
                log.info(f"  Already processed — skipping")
                skipped += 1
                continue

            # Download
            local_path = os.path.join(tmpdir, filename)
            download_file(drive, file_id, local_path)

            # Empty check
            if is_empty_csv(local_path):
                log.info(f"  Empty file — skipping")
                skipped += 1
                continue

            fieldnames, rows = read_csv(local_path)
            total = len(rows)
            log.info(f"  Total rows: {total}")

            if total < MIN_EXPECTED_ROWS:
                log.warning(f"  Unusually few rows ({total} < {MIN_EXPECTED_ROWS})")
            if total > MAX_EXPECTED_ROWS:
                log.warning(f"  Unusually many rows ({total} > {MAX_EXPECTED_ROWS})")

            # Triage
            kill_rows, sf_rows = run_triage(rows)
            log.info(f"  Kill List: {len(kill_rows)}, SF-bound: {len(sf_rows)}")

            # Kill List sheet
            sheet_rows = format_kill_list_sheet_rows(kill_rows)
            sheet_start, sheet_end, sheet_count = 0, 0, 0
            if sheet_rows:
                sheet_start, sheet_end, sheet_count = append_to_kill_list(sheets, sheet_rows)
                log.info(f"  Kill List sheet: rows {sheet_start}–{sheet_end}")

            # Staging sheet
            batch_name = os.path.splitext(filename)[0]
            staging_count = append_to_staging(sheets, sf_rows, batch_name)
            log.info(f"  Staging sheet: {staging_count} rows written")

            # Upload output CSVs to Drive
            master_csv_path, master_csv_name = write_master_kill_list_csv(
                kill_rows, fieldnames, filename, tmpdir
            )
            sf_csv_path, sf_csv_name = write_sf_csv(sf_rows, fieldnames, filename, tmpdir)

            sf_file_id = None
            if not file_exists_in_folder(drive, sf_csv_name, PROCESSED_FOLDER_ID):
                sf_file_id = upload_csv(drive, sf_csv_path, sf_csv_name, PROCESSED_FOLDER_ID)

            master_file_id = None
            if not file_exists_in_folder(drive, master_csv_name, UPLOADED_FOLDER_ID):
                master_file_id = upload_csv(drive, master_csv_path, master_csv_name, UPLOADED_FOLDER_ID)

            results.append({
                "filename": filename,
                "drive_id": file_id,
                "total_rows": total,
                "kill_list_rows": len(kill_rows),
                "sf_rows": len(sf_rows),
                "staging_rows": staging_count,
                "sheet_start": sheet_start,
                "sheet_end": sheet_end,
                "sheet_appended": sheet_count,
            })

    # Send notification if we processed anything
    if results:
        try:
            send_notification(gmail, results)
        except Exception as e:
            log.error(f"Failed to send notification email: {e}")

    return {
        "status": "ok",
        "processed": len(results),
        "skipped": skipped,
        "results": results,
    }


@app.route("/process", methods=["POST"])
def process_endpoint():
    """Cloud Scheduler hits this endpoint to trigger processing."""
    try:
        result = process_files()
        return jsonify(result), 200
    except Exception as e:
        log.exception("Processing failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
