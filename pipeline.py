#!/usr/bin/env python3
"""Aegis Non Donor CSV triage pipeline — CLI entry point.

Scans Google Drive "Files to Process" folder for Non Donor CSVs, runs triage,
writes Kill List rows to Google Sheet, uploads cleaned CSVs to Drive.
"""

import os
import sys
import tempfile

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from config import (
    INPUT_FOLDER_ID, PROCESSED_FOLDER_ID, UPLOADED_FOLDER_ID,
    KILL_LIST_SHEET_ID, KILL_LIST_SHEET_NAME,
    MIN_EXPECTED_ROWS, MAX_EXPECTED_ROWS,
    get_services,
)
from triage import (
    run_triage, format_kill_list_sheet_rows,
    write_master_kill_list_csv, write_sf_csv,
    is_empty_csv, read_csv,
)


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
    """Find the absolute last row with data in any column."""
    result = service.spreadsheets().values().get(
        spreadsheetId=KILL_LIST_SHEET_ID,
        range=f"{KILL_LIST_SHEET_NAME}!A:H",
        majorDimension="ROWS",
    ).execute()
    rows = result.get("values", [])
    last = 0
    for i, row in enumerate(rows):
        if any(cell.strip() for cell in row if cell):
            last = i + 1
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


# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_file(file_info, sheets, drive, tmpdir):
    """Process a single Non Donor CSV file through the full pipeline."""
    file_id = file_info["id"]
    filename = file_info["name"]
    print(f"\nProcessing: {filename} (Drive ID: {file_id})")

    if file_exists_in_folder(drive, filename, PROCESSED_FOLDER_ID):
        print(f"  Already processed (output exists in Processed folder) — skipping")
        return None

    local_path = os.path.join(tmpdir, filename)
    download_file(drive, file_id, local_path)

    if is_empty_csv(local_path):
        print(f"  Empty file (header only) — skipping")
        return None

    fieldnames, rows = read_csv(local_path)
    total = len(rows)
    print(f"  Total rows: {total}")

    if total < MIN_EXPECTED_ROWS:
        print(f"  WARNING: Unusually few rows ({total} < {MIN_EXPECTED_ROWS})")
    if total > MAX_EXPECTED_ROWS:
        print(f"  WARNING: Unusually many rows ({total} > {MAX_EXPECTED_ROWS})")

    kill_rows, sf_rows = run_triage(rows)
    print(f"  Kill List rows: {len(kill_rows)}")
    print(f"  Salesforce-bound rows: {len(sf_rows)}")

    sheet_rows = format_kill_list_sheet_rows(kill_rows)
    master_csv_path, master_csv_name = write_master_kill_list_csv(
        kill_rows, fieldnames, filename, tmpdir
    )
    sf_csv_path, sf_csv_name = write_sf_csv(sf_rows, fieldnames, filename, tmpdir)

    if sheet_rows:
        start, end, count = append_to_sheet(sheets, sheet_rows)
        print(f"  Sheet: appended {count} rows (rows {start}–{end})")
    else:
        start, end, count = 0, 0, 0
        print("  Sheet: no Kill List rows to append")

    sf_file_id = None
    if file_exists_in_folder(drive, sf_csv_name, PROCESSED_FOLDER_ID):
        print(f"  SF CSV: {sf_csv_name} already exists in Processed — skipped upload")
    else:
        sf_file_id = upload_csv(drive, sf_csv_path, sf_csv_name, PROCESSED_FOLDER_ID)
        print(f"  SF CSV uploaded → {sf_csv_name} (ID: {sf_file_id})")

    master_file_id = None
    if file_exists_in_folder(drive, master_csv_name, UPLOADED_FOLDER_ID):
        print(f"  Master Kill CSV: {master_csv_name} already exists in Uploaded — skipped upload")
    else:
        master_file_id = upload_csv(drive, master_csv_path, master_csv_name, UPLOADED_FOLDER_ID)
        print(f"  Master Kill CSV uploaded → {master_csv_name} (ID: {master_file_id})")

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
    print("Connecting to Google APIs...")
    sheets, drive, _ = get_services()
    verify_sheet_access(sheets)

    files = list_non_donor_csvs(drive)

    if not files:
        print("No Non Donor files found in Files to Process. Nothing to do.")
        sys.exit(0)

    print(f"\nFound {len(files)} Non Donor file(s):")
    for f in files:
        print(f"  {f['name']} (ID: {f['id']})")

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
