"""
Test script: Authenticates with Google Sheets API and confirms read access
to the bet_tracker spreadsheet.

Run once to generate token.json, then auth is automatic on subsequent runs.
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# If you change these scopes, delete token.json and re-authenticate
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

SPREADSHEET_ID = "19kX5kwwut8FAjNinI2YJjfm7LihruhkDYIXf8qD0f5M"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")


def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return creds


def main():
    print("Authenticating with Google...")
    creds = get_credentials()
    print("Authentication successful.")

    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()

    print(f"\nFetching spreadsheet metadata for ID: {SPREADSHEET_ID}")
    result = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()

    title = result.get("properties", {}).get("title", "Unknown")
    sheets = [s["properties"]["title"] for s in result.get("sheets", [])]

    print(f"\nSpreadsheet title : {title}")
    print(f"Sheets found      : {sheets}")
    print("\nAccess confirmed. Google Sheets API is working correctly.")


if __name__ == "__main__":
    main()
