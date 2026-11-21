#!/usr/bin/env python3
"""
One-off interactive Garmin Connect login. Saves OAuth tokens only.

Prompts for email, password and (if enabled) the MFA code in your terminal; the
password is never written to disk or the environment. Afterwards the scripts
reuse the saved tokens and never touch Garmin's password endpoint.

Run it yourself, once, with a real terminal attached:
    docker compose run --rm -it coach python3 scripts/garmin_login.py

Every run is a password login: don't loop it. If Garmin answers 429 (rate
limited), wait a few hours before trying again.
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from garmin_fetcher import TOKEN_DIR  # noqa: E402


def main() -> int:
    if not sys.stdin.isatty():
        print("Run this in an interactive terminal (docker compose run --rm -it ...).", file=sys.stderr)
        return 2
    try:
        from garminconnect import Garmin
    except ImportError:
        print("garminconnect is not installed: pip install -r requirements.txt", file=sys.stderr)
        return 2

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")
    client = Garmin(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    try:
        client.login()
    except Exception as e:
        # Message only: never echo credentials.
        print(f"Login failed: {type(e).__name__}: {e}", file=sys.stderr)
        if '429' in str(e):
            print("Garmin rate-limited this IP. Wait a few hours before retrying.", file=sys.stderr)
        return 1
    finally:
        password = None

    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
    client.client.dump(TOKEN_DIR)
    for name in os.listdir(TOKEN_DIR):
        os.chmod(os.path.join(TOKEN_DIR, name), 0o600)
    print(f"Logged in. Tokens saved to {TOKEN_DIR}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
