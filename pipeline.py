#!/usr/bin/env python3
"""Aegis Non Donor CSV triage pipeline — Phase A.

Reads Non Donor CSV files from Google Drive, triages rows into Kill List
(suppression) and Salesforce-bound sets, writes Kill List rows to a Google Sheet,
and uploads cleaned output CSVs back to Drive.
"""

import csv
import os
import sys
import tempfile
from datetime import datetime

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# ── Configuration ──────────────────────────────────────────────────────────────

# Google Drive folder IDs
ROOT_FOLDER_ID = "1siDdLdqDHavOOj7gI_DVitdqBmc_m3X6"
INPUT_FOLDER_ID = "1JCzwnbqZrfhHyruSlIJ_z7AyIHOjr3md"       # Files to Process
DELETE_FOLDER_ID = "1TU-3i7dZI5fiGHJpfylbJcJxgHyvekJa"       # Claude Files to Delete
PROCESSED_FOLDER_ID = "1mq5KGIHvMErycZ6U1RAikzzOu3OaKQrs"    # Claude Processed Non Donor Files
UPLOADED_FOLDER_ID = "1KNUK-mx-6qv_FjnZ4ubwQh0T-OcOqg_j"    # Uploaded NonDonor Files

# TEST SHEET — production ID: 11dM2Pf-E195rJUnF79rMHhN5RUb0L-03fS8nZ4WZw7o
KILL_LIST_SHEET_ID = "1I-LBd6AQO0EhcHX1dqBHzbSBr9w12yNzgIlgN_Jtb3M"
KILL_LIST_SHEET_NAME = "Sheet1"

# FINDER prefixes that indicate a donor constituent ID, not an acquisition finder
DONOR_PREFIXES = ("0", "7", "S")

# Volume sanity check bounds
MIN_EXPECTED_ROWS = 10
MAX_EXPECTED_ROWS = 500

API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ── API clients ────────────────────────────────────────────────────────────────

def get_services():
    """Build authenticated Sheets and Drive API services using ADC."""
    creds, _ = google.auth.default(scopes=API_SCOPES)
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return sheets, drive


# ── Drive helpers ──────────────────────────────────────────────────────────────

def list_non_donor_csvs(drive):
    """Find *Non Donors*.csv files in the Files to Process folder."""
    q = (
        f"'{INPUT_FOLDER_ID}' in parents"
        " and name contains 'Non Donors'"
        " and mimeType='text/csv'"
        " and trashed=false"
    )
    result = drive.files().list(
        q=q,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id, name)",
    ).execute()
    return result.get("files", [])


def file_exists_in_folder(drive, filename, folder_id):
    """Check if a file with the given name already exists in a folder."""
    q = (
        f"'{folder_id}' in parents"
        f" and name = '{filename}'"
        " and trashed=false"
    )
    result = drive.files().list(
        q=q,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id)",
        pageSize=1,
    ).execute()
    return len(result.get("files", [])) > 0


def download_file(drive, file_id, dest_path):
    """Download a Drive file to a local path."""
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def upload_csv(drive, local_path, filename, parent_folder_id):
    """Upload a CSV file to a Drive folder. Returns the new file ID."""
    media = MediaFileUpload(local_path, mimetype="text/csv")
    body = {"name": filename, "parents": [parent_folder_id]}
    result = drive.files().create(
        body=body,
        media_body=media,
        supportsAllDrives=True,
        fields="id, name",
    ).execute()
    return result["id"]


# ── Sheets helpers ─────────────────────────────────────────────────────────────

def verify_sheet_access(service):
    """Read cell A1 to confirm SA has access. Exits on failure."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=KILL_LIST_SHEET_ID,
            range=f"{KILL_LIST_SHEET_NAME}!A1",
        ).execute()
        val = result.get("values", [[""]])[0][0]
        print(f"  Sheet access verified (A1 = '{val}')")
    except Exception as e:
        print(f"ERROR: Cannot access Kill List sheet: {e}")
        print(f"  Share the sheet with the service account and retry.")
        sys.exit(1)


def find_last_row(service):
    """Find the absolute last row with data in any column.

    The existing sheet has sparse gaps from the old CoWork automation,
    so we scan the full extent rather than looking for the first empty cell.
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=KILL_LIST_SHEET_ID,
        range=f"{KILL_LIST_SHEET_NAME}!A:H",
        majorDimension="ROWS",
    ).execute()
    rows = result.get("values", [])
    last = 0
    for i, row in enumerate(rows):
        if any(cell.strip() for cell in row if cell):
            last = i + 1  # 1-indexed
    return last


def append_to_sheet(service, rows):
    """Append rows after the last populated row in the Kill List sheet."""
    last_row = find_last_row(service)
    start_row = last_row + 1
    range_str = f"{KILL_LIST_SHEET_NAME}!A{start_row}"

    body = {"values": rows}
    result = service.spreadsheets().values().update(
        spreadsheetId=KILL_LIST_SHEET_ID,
        range=range_str,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()

    updated = result.get("updatedRows", 0)
    end_row = start_row + updated - 1
    return start_row, end_row, updated


# ── Triage logic (unchanged) ──────────────────────────────────────────────────

def sort_by_finder(rows):
    """Sort rows by FINDER column, empties last."""
    return sorted(rows, key=lambda r: (r["FINDER"].strip() == "", r["FINDER"]))


def reclassify_finders(rows):
    """Move FINDER values that are actually donor IDs into CONSID (Step 2)."""
    for row in rows:
        finder = row["FINDER"].strip()
        if finder and finder[0] in DONOR_PREFIXES:
            row["CONSID"] = finder
            row["FINDER"] = ""
    return rows


def split_rows(rows):
    """Split into Kill List (non-empty FINDER) and SF-bound (empty FINDER)."""
    kill_list = []
    sf_bound = []
    for row in rows:
        if row["FINDER"].strip():
            kill_list.append(row)
        else:
            sf_bound.append(row)
    return kill_list, sf_bound


def format_kill_list_sheet_rows(kill_rows):
    """Format Kill List rows for the 8-column Google Sheet output."""
    today = datetime.now().strftime("%-m/%-d/%y")
    sheet_rows = []
    for row in kill_rows:
        sheet_rows.append([
            row.get("FNAME1", ""),
            row.get("LNAME1", ""),
            row.get("STREET", ""),
            "",  # Street 2
            row.get("CITY", ""),
            row.get("STATE", ""),
            row.get("ZIPCOD", ""),
            today,
        ])
    return sheet_rows


def write_master_kill_list_csv(kill_rows, fieldnames, original_filename, tmpdir):
    """Write Master Kill List CSV to temp dir. Returns (local_path, output_name)."""
    output_fields = []
    for f in fieldnames:
        if f == "SUFFX1":
            continue
        output_fields.append(f)
        if f == "STREET":
            output_fields.append("STREET2")

    base = os.path.splitext(original_filename)[0]
    out_name = f"{base}_Master Kill List.csv"
    out_path = os.path.join(tmpdir, out_name)

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        for row in kill_rows:
            row_copy = dict(row)
            row_copy["STREET2"] = ""
            writer.writerow(row_copy)

    return out_path, out_name


def write_sf_csv(sf_rows, fieldnames, original_filename, tmpdir):
    """Write SF-bound CSV to temp dir. Returns (local_path, filename)."""
    out_path = os.path.join(tmpdir, original_filename)

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sf_rows)

    return out_path, original_filename


def is_empty_csv(filepath):
    """Check if a CSV has zero data rows (header only or all-blank rows)."""
    with open(filepath, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if any(v.strip() for v in row.values() if v):
                return False
    return True


# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_file(file_info, sheets, drive, tmpdir):
    """Process a single Non Donor CSV file through the full pipeline.

    file_info: dict with 'id' and 'name' from Drive API.
    Returns result dict, or None if skipped.
    """
    file_id = file_info["id"]
    filename = file_info["name"]
    print(f"\nProcessing: {filename} (Drive ID: {file_id})")

    # Check if already processed — SF-bound CSV exists in output folder
    if file_exists_in_folder(drive, filename, PROCESSED_FOLDER_ID):
        print(f"  Already processed (output exists in Processed folder) — skipping")
        return None

    # Download to temp
    local_path = os.path.join(tmpdir, filename)
    download_file(drive, file_id, local_path)

    # Check for empty file
    if is_empty_csv(local_path):
        print(f"  Empty file (header only) — skipping")
        return None

    # Read CSV — all columns as strings
    with open(local_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    total = len(rows)
    print(f"  Total rows: {total}")

    if total < MIN_EXPECTED_ROWS:
        print(f"  WARNING: Unusually few rows ({total} < {MIN_EXPECTED_ROWS})")
    if total > MAX_EXPECTED_ROWS:
        print(f"  WARNING: Unusually many rows ({total} > {MAX_EXPECTED_ROWS})")

    # Step 1: Sort by FINDER
    rows = sort_by_finder(rows)

    # Step 2: Reclassify donor FINDERs
    rows = reclassify_finders(rows)

    # Step 3: Re-sort by FINDER
    rows = sort_by_finder(rows)

    # Step 4: Split
    kill_rows, sf_rows = split_rows(rows)
    print(f"  Kill List rows: {len(kill_rows)}")
    print(f"  Salesforce-bound rows: {len(sf_rows)}")

    # Step 5: Kill List outputs
    sheet_rows = format_kill_list_sheet_rows(kill_rows)
    master_csv_path, master_csv_name = write_master_kill_list_csv(
        kill_rows, fieldnames, filename, tmpdir
    )

    # Step 6: SF-bound CSV
    sf_csv_path, sf_csv_name = write_sf_csv(sf_rows, fieldnames, filename, tmpdir)

    # Write Kill List rows to Google Sheet
    if sheet_rows:
        start, end, count = append_to_sheet(sheets, sheet_rows)
        print(f"  Sheet: appended {count} rows (rows {start}–{end})")
    else:
        start, end, count = 0, 0, 0
        print("  Sheet: no Kill List rows to append")

    # Upload SF-bound CSV (with duplicate check)
    sf_file_id = None
    if file_exists_in_folder(drive, sf_csv_name, PROCESSED_FOLDER_ID):
        print(f"  SF CSV: {sf_csv_name} already exists in Processed — skipped upload")
    else:
        sf_file_id = upload_csv(drive, sf_csv_path, sf_csv_name, PROCESSED_FOLDER_ID)
        print(f"  SF CSV uploaded → {sf_csv_name} (ID: {sf_file_id})")

    # Upload Master Kill List CSV (with duplicate check)
    master_file_id = None
    if file_exists_in_folder(drive, master_csv_name, UPLOADED_FOLDER_ID):
        print(f"  Master Kill CSV: {master_csv_name} already exists in Uploaded — skipped upload")
    else:
        master_file_id = upload_csv(drive, master_csv_path, master_csv_name, UPLOADED_FOLDER_ID)
        print(f"  Master Kill CSV uploaded → {master_csv_name} (ID: {master_file_id})")

    # Originals stay in Files to Process — operator clears them manually.
    # SA cannot move/delete files owned by other users via impersonated ADC.

    return {
        "filename": filename,
        "drive_id": file_id,
        "total_rows": total,
        "kill_list_rows": len(kill_rows),
        "sf_rows": len(sf_rows),
        "sheet_start": start,
        "sheet_end": end,
        "sheet_appended": count,
        "sf_csv_id": sf_file_id,
        "sf_csv_name": sf_csv_name,
        "master_csv_id": master_file_id,
        "master_csv_name": master_csv_name,
    }


def main():
    # Authenticate
    print("Connecting to Google APIs...")
    sheets, drive = get_services()
    verify_sheet_access(sheets)

    # Find Non Donor files in the input folder
    files = list_non_donor_csvs(drive)

    if not files:
        print("No Non Donor files found in Files to Process. Nothing to do.")
        sys.exit(0)

    print(f"\nFound {len(files)} Non Donor file(s):")
    for f in files:
        print(f"  {f['name']} (ID: {f['id']})")

    # Process each file using a temp directory for local I/O
    results = []
    skipped = 0
    with tempfile.TemporaryDirectory(prefix="aegis_") as tmpdir:
        for file_info in files:
            try:
                result = process_file(file_info, sheets, drive, tmpdir)
                if result:
                    results.append(result)
                else:
                    skipped += 1
            except Exception as e:
                print(f"\nERROR processing {file_info['name']}: {e}")
                print("  Stopping. File left in Files to Process for retry.")
                sys.exit(1)

    # Summary report
    print("\n" + "=" * 60)
    print("PROCESSING COMPLETE")
    print("=" * 60)
    print(f"Files processed: {len(results)}, skipped: {skipped}")
    total_kill = 0
    total_sf = 0
    for r in results:
        print(f"\n  {r['filename']} (Drive ID: {r['drive_id']}):")
        print(f"    Total rows:      {r['total_rows']}")
        print(f"    Kill List:       {r['kill_list_rows']}")
        print(f"    Salesforce:      {r['sf_rows']}")
        if r["sheet_appended"]:
            print(f"    Sheet rows:      {r['sheet_start']}–{r['sheet_end']} ({r['sheet_appended']} appended)")
        if r["sf_csv_id"]:
            print(f"    SF CSV:          {r['sf_csv_name']} (ID: {r['sf_csv_id']}) → Processed Non Donor Files")
        else:
            print(f"    SF CSV:          {r['sf_csv_name']} (already existed)")
        if r["master_csv_id"]:
            print(f"    Master Kill CSV: {r['master_csv_name']} (ID: {r['master_csv_id']}) → Uploaded NonDonor Files")
        else:
            print(f"    Master Kill CSV: {r['master_csv_name']} (already existed)")
        total_kill += r["kill_list_rows"]
        total_sf += r["sf_rows"]

    print(f"\nTotals: {total_kill} Kill List + {total_sf} Salesforce = {total_kill + total_sf}")
    print("\nNote: Originals remain in Files to Process — clear them manually after review.")


if __name__ == "__main__":
    main()
