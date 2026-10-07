"""One-time Google sign-in for Natro. Run it on the PC: it opens the browser.

    python -m natro_agent.google_signin

Needs google-oauth-client.json at the top of the repo: an OAuth client of type
"Desktop app" from the Google Cloud project, with the Calendar, Tasks and Gmail
APIs enabled and the consent screen "In production" (in "Testing", Google ends
the sign-in after 7 days). Google warns once that the app isn't verified; that's
expected for a personal app.

Writes tokens/google.json. Copy it to the same place on the VPS (readable only by
the natro user) and restart Natro.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from natro_agent.config import ROOT
from natro_agent.google_tools import SCOPES, TOKEN_FILE

CLIENT_FILE = ROOT / "google-oauth-client.json"


def main():
    if not CLIENT_FILE.exists():
        sys.exit(f"Missing {CLIENT_FILE}: download the Desktop app OAuth client's JSON there first.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    # "consent" makes Google return a refresh token even if Natro was approved before.
    credentials = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not credentials.refresh_token:
        sys.exit("Google returned no refresh token; remove Natro's access at myaccount.google.com/permissions "
                 "and try again.")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Signed in. Token saved to {TOKEN_FILE}; copy it to the VPS (same place) and restart Natro.")


if __name__ == "__main__":
    main()
