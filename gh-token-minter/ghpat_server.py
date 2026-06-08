"""Mint GitHub App installation tokens — as a CLI or HTTP server.

CLI:   python ghpat_server.py              # prints token to stdout
Server: python ghpat_server.py serve       # starts HTTP server on LISTEN_PORT
"""

import json
import os
import sys
import time

import jwt  # pip install PyJWT cryptography
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

APP_ID = os.environ["GH_APP_ID"]
PEM_FILE_PATH = os.environ["GH_APP_PEM_FILE_PATH"]
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))


def mint_token():
    """Generate a GitHub App installation token."""
    private_key_contents = open(PEM_FILE_PATH, "r").read()
    payload = {
        "iat": int(time.time()),
        "exp": int(time.time()) + (10 * 30),  # 30 mins
        "iss": APP_ID,
    }
    encoded_jwt = jwt.encode(payload, private_key_contents, algorithm="RS256")
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    resp = requests.get("https://api.github.com/app/installations", headers=headers)
    resp.raise_for_status()
    inst_id = resp.json()[0]["id"]

    resp = requests.post(
        f"https://api.github.com/app/installations/{inst_id}/access_tokens",
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["token"], data.get("expires_at", "")


class TokenHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/token":
            try:
                token, expires_at = mint_token()
                body = json.dumps({"token": token, "expires_at": expires_at})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())
            except Exception as e:
                body = json.dumps({"error": str(e)})
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())
        elif self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[ghpat] {format % args}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        server = HTTPServer(("0.0.0.0", LISTEN_PORT), TokenHandler)
        print(f"[ghpat] serving on :{LISTEN_PORT}")
        server.serve_forever()
    else:
        token, _ = mint_token()
        print(token)
