"""One-time script to authenticate with Google Drive and save token.json."""
import os
import sys

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> None:
    credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    token_path = os.getenv("GOOGLE_TOKEN_PATH", "token.json")

    if not os.path.exists(credentials_path):
        print(f"ERROR: credentials file not found at '{credentials_path}'")
        print(
            "Download it from Google Cloud Console → APIs & Services → "
            "Credentials → OAuth 2.0 Client IDs → Download JSON"
        )
        sys.exit(1)

    print("Opening browser for Google OAuth consent…")
    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_path, "w") as fh:
        fh.write(creds.to_json())

    print(f"\n✅ Authentication successful. Token saved to '{token_path}'.")
    print("You can now run: python bot.py")


if __name__ == "__main__":
    main()
