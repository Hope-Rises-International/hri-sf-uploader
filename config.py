"""Shared configuration and API client setup."""

import google.auth
from googleapiclient.discovery import build

# Google Drive folder IDs
ROOT_FOLDER_ID = "1siDdLdqDHavOOj7gI_DVitdqBmc_m3X6"
INPUT_FOLDER_ID = "1JCzwnbqZrfhHyruSlIJ_z7AyIHOjr3md"       # Files to Process
DELETE_FOLDER_ID = "1TU-3i7dZI5fiGHJpfylbJcJxgHyvekJa"       # Claude Files to Delete
PROCESSED_FOLDER_ID = "1mq5KGIHvMErycZ6U1RAikzzOu3OaKQrs"    # Claude Processed Non Donor Files
UPLOADED_FOLDER_ID = "1KNUK-mx-6qv_FjnZ4ubwQh0T-OcOqg_j"    # Uploaded NonDonor Files

# Production Kill List sheet
KILL_LIST_SHEET_ID = "11dM2Pf-E195rJUnF79rMHhN5RUb0L-03fS8nZ4WZw7o"
KILL_LIST_SHEET_NAME = "Sheet1"

# Aegis Staging sheet
STAGING_SHEET_ID = "1qSxi7YBtZ2VsYpDXGKGz0539Gc9bIMKIR1PTNj7ibCU"
STAGING_NON_DONOR_TAB = "Non Donor"
STAGING_HOUSEHOLD_TAB = "Household"

# FINDER prefixes that indicate a donor constituent ID, not an acquisition finder
DONOR_PREFIXES = ("0", "7", "S")

# Volume sanity check bounds
MIN_EXPECTED_ROWS = 10
MAX_EXPECTED_ROWS = 500

# Notification
NOTIFY_EMAIL = "bschwanbeck@hoperises.org"

API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_services():
    """Build authenticated Sheets, Drive, and Gmail API services using ADC."""
    creds, _ = google.auth.default(scopes=API_SCOPES)
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    gmail = build("gmail", "v1", credentials=creds)
    return sheets, drive, gmail
