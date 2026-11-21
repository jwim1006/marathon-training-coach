#!/usr/bin/env python3
"""
Send a message to the athlete's Telegram chat.

Used by scheduled coach jobs to deliver summaries. Reads the message from the
command line or stdin; long messages are split to fit Telegram's limit.

Env vars (loaded from .env if present):
    TELEGRAM_BOT_TOKEN  Bot token from @BotFather
    TELEGRAM_CHAT_ID    Destination chat ID

Usage:
    python3 scripts/send_telegram.py "Daily check: all good"
    python3 scripts/coach_check.py | python3 scripts/send_telegram.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

TELEGRAM_LIMIT = 4096
REQUEST_TIMEOUT = 30


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list:
    """Split text into chunks under limit, preferring newline boundaries."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip('\n')
    if text:
        chunks.append(text)
    return chunks


def send(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(text):
        body = json.dumps({'chat_id': chat_id, 'text': chunk}).encode()
        req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            result = json.load(resp)
        if not result.get('ok'):
            raise RuntimeError(f"Telegram API error: {result.get('description', 'unknown')}")


def main() -> int:
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set", file=sys.stderr)
        return 2

    text = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    text = text.strip()
    if not text:
        print("Error: empty message", file=sys.stderr)
        return 2

    try:
        send(text, token, chat_id)
    except (urllib.error.URLError, RuntimeError) as e:
        # Never echo the URL: it contains the bot token
        print(f"Error: failed to send Telegram message ({type(e).__name__})", file=sys.stderr)
        return 1
    print("sent")
    return 0


if __name__ == '__main__':
    sys.exit(main())
