"""Aegis Non Donor triage logic — pure data transformation, no I/O.

This module contains the core triage steps that split Non Donor CSV rows
into Kill List (suppression) and Salesforce-bound sets. No file or API
calls — just data in, data out.
"""

import csv
import os
from datetime import datetime

from config import DONOR_PREFIXES


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


def run_triage(rows):
    """Execute the full triage pipeline (Steps 1-4). Returns (kill_rows, sf_rows)."""
    rows = sort_by_finder(rows)
    rows = reclassify_finders(rows)
    rows = sort_by_finder(rows)
    return split_rows(rows)


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


def read_csv(filepath):
    """Read a CSV file, all columns as strings. Returns (fieldnames, rows)."""
    with open(filepath, "r", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    return fieldnames, rows
