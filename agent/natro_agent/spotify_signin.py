"""One-time Spotify sign-in for Natro. Run it on the PC: it opens the browser.

    python -m natro_agent.spotify_signin

Needs SPOTIFY_CLIENT_ID in .env: the Client ID of a Spotify developer app
(developer.spotify.com/dashboard, made by the Premium account owner, "Web API"
ticked) with the redirect URI http://127.0.0.1:8899/callback. It uses PKCE, so
there is no client secret to keep anywhere.

Writes tokens/spotify.json (with the Client ID in it). Copy it to the same place
on the VPS (readable only by the natro user) and restart Natro. Spotify ends a
sign-in after six months: then run this again.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

from natro_agent.config import load_env
from natro_agent.spotify import SCOPES, TOKEN_FILE, TOKEN_URL

PORT = 8899
REDIRECT_URI = f"http://127.0.0.1:{PORT}/callback"


def wait_for_code(state):
    """Serve the redirect once; return the authorization code."""
    answer = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state") != [state]:
                self.send_response(400)
                self.end_headers()
                return
            answer.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Natro: done, you can close this tab.".encode())

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", PORT), Callback) as server:
        while not answer:
            server.handle_request()
    if "code" not in answer:
        sys.exit(f"Spotify didn't sign in: {answer.get('error', 'no code')}")
    return answer["code"]


def main():
    load_env()
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    if not client_id:
        sys.exit("Set SPOTIFY_CLIENT_ID in .env first (the Client ID of your Spotify developer app).")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI, "scope": " ".join(SCOPES),
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    print(f"Opening the browser to sign in to Spotify. If it doesn't open, go to:\n{url}")
    webbrowser.open(url)
    code = wait_for_code(state)
    request = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI, "client_id": client_id,
        "code_verifier": verifier}).encode(), method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        tokens = json.loads(response.read())
    granted = set(tokens.get("scope", "").split())
    if missing := set(SCOPES) - granted:
        print(f"Note: Spotify didn't grant {', '.join(sorted(missing))}.")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "client_id": client_id, "access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"],
        "expires_at": 0, "signed_in": date.today().isoformat()}, indent=1), encoding="utf-8")
    print(f"Signed in. Token saved to {TOKEN_FILE}; copy it to the VPS (same place) and restart Natro.")


if __name__ == "__main__":
    main()
