"""Shared configuration and API client setup."""

import base64
import json
import time

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
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
GMAIL_SENDER = "bsimmons@hoperises.org"  # User to impersonate for Gmail send

SA_EMAIL = "hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com"

API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/iam",
]


def get_services():
    """Build authenticated Sheets, Drive, and Gmail API services using ADC.

    Gmail requires domain-wide delegation with a subject (impersonated user).
    ADC impersonation can't set a subject, so we use IAM signBlob to create
    a signed JWT with sub=GMAIL_SENDER, exchange it for an access token,
    and build the Gmail client from that.
    """
    creds, _ = google.auth.default(scopes=API_SCOPES)
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    gmail = _build_delegated_gmail(creds)
    return sheets, drive, gmail


def _build_delegated_gmail(source_creds):
    """Build Gmail client using domain-wide delegation via IAM signBlob.

    Domain-wide delegation requires a JWT with a 'sub' claim set to the
    Workspace user being impersonated. ADC impersonation doesn't support
    setting 'sub', so we manually construct the JWT, sign it via the IAM
    signBlob API, and exchange it for an access token.
    """
    source_creds.refresh(Request())
    iam = build("iam", "v1", credentials=source_creds)

    now = int(time.time())
    payload = {
        "iss": SA_EMAIL,
        "sub": GMAIL_SENDER,
        "scope": "https://www.googleapis.com/auth/gmail.send",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }

    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    signing_input = f"{header_b64}.{payload_b64}"

    sign_result = iam.projects().serviceAccounts().signBlob(
        name=f"projects/-/serviceAccounts/{SA_EMAIL}",
        body={"bytesToSign": base64.b64encode(signing_input.encode()).decode()},
    ).execute()

    signature = sign_result["signature"].rstrip("=").replace("+", "-").replace("/", "_")
    signed_jwt = f"{signing_input}.{signature}"

    # Exchange signed JWT for access token
    import urllib.request
    import urllib.parse
    token_data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": signed_jwt,
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_data)
    resp = urllib.request.urlopen(req)
    access_token = json.loads(resp.read())["access_token"]

    return build("gmail", "v1", credentials=OAuthCredentials(token=access_token))
