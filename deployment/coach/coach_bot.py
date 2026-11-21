#!/usr/bin/env python3
"""
Telegram <-> Claude Code bridge for the marathon coach. Replaces OpenClaw.

One process, standard library only:
  * long-polls the Telegram bot and answers messages from the athlete's chat
    by running `claude -p` in this repo, resuming one ongoing conversation;
  * runs the scheduled daily check and weekly report in that same conversation,
    so replying to an alert continues the thread.

Messages from any other chat are ignored. Claude runs in dontAsk mode with a
fixed tool allowlist: it can run the coach scripts, read files and edit the
coach memory, nothing else.

Env vars:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   required
    CLAUDE_CODE_OAUTH_TOKEN                from `claude setup-token` (subscription)
    COACH_MODEL                            default: sonnet
    COACH_TELEGRAM_POLL                    0 disables polling (schedules still run)

Usage:
    python3 coach_bot.py                   run forever
    python3 coach_bot.py --run-job daily   run one job now and send it
    python3 coach_bot.py --run-job weekly --print   run one job, print instead of sending
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get('COACH_REPO', Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / 'scripts'))
from send_telegram import split_message  # noqa: E402

CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'marathon-training-coach'
MEMORY_DIR = CONFIG_DIR / 'coach-memory'
STATE_FILE = MEMORY_DIR / 'bot_state.json'

MODEL = os.environ.get('COACH_MODEL', 'sonnet')
POLL_TIMEOUT = 30          # seconds; also bounds how late a schedule check can be
CLAUDE_TIMEOUT = 15 * 60
CATCH_UP_WINDOW = timedelta(hours=3)   # run a missed job if we wake up within this window

def _abs_rule(path: Path) -> str:
    # Permission rules read "/x" as project-relative; absolute paths need a leading "//".
    return '/' + path.as_posix()


ALLOWED_TOOLS = [
    'Read', 'Glob', 'Grep',
    'Bash(python3 scripts/*)',
    'Bash(date*)',
    f'Edit({_abs_rule(MEMORY_DIR)}/**)',
    f'Write({_abs_rule(MEMORY_DIR)}/**)',
    f'Edit({_abs_rule(CONFIG_DIR / "athlete_context.md")})',
]

TELEGRAM_FORMAT = (
    "You are replying in Telegram. Plain text only, no markdown tables or headings. "
    "Keep it short unless the athlete asks for detail."
)

# Without this, Claude reaches for cat/ls/cd, gets denied, and concludes it has no shell.
TOOL_RULES = (
    "Tool rules for this session (enforced; anything else is denied):\n"
    "- Bash runs ONLY the coach scripts, as one plain command from the current directory: "
    "`python3 scripts/<name>.py [args]`. No cd, pipes, &&, ;, redirects, cat, ls, head or python3 -c.\n"
    "- Use the Read, Grep and Glob tools for files, e.g. "
    f"{CONFIG_DIR.as_posix()}/workout_notes.json or {MEMORY_DIR.as_posix()}/logs/.\n"
    f"- Edit/Write only under {MEMORY_DIR.as_posix()}/ (and athlete_context.md); record data with the scripts.\n"
    "- Fresh Garmin data comes from the scripts (workout_analysis.py, coach_check.py, weekly_report.py, "
    "marathon_status.py). If a script fails, say what failed instead of claiming you have no access.\n"
    "Memory: only COACH.md, MEMORY.md and TOOLS.md are loaded into every conversation; daily logs are not. "
    f"So anything lasting about the athlete (preferences, injuries, how they like feedback, standing "
    f"decisions) goes into {MEMORY_DIR.as_posix()}/MEMORY.md under \"Coach's Personal Notes\" straight away, "
    "updating an existing line rather than adding a duplicate. Day-specific notes go in today's log."
)

# Sent after the Sunday weekly report, in the same conversation; its reply is logged, not sent.
CONSOLIDATE_PROMPT = (
    "[Scheduled: Weekly memory consolidation, not sent to the athlete]\n"
    f"Read the last 7 days of logs in {MEMORY_DIR.as_posix()}/logs/ and this conversation. Fold anything "
    f"lasting into {MEMORY_DIR.as_posix()}/MEMORY.md: update or replace existing lines rather than "
    "duplicating them, refresh Current Status, drop what is stale, keep it concise (under about 120 lines). "
    "A fresh conversation starts after this, so MEMORY.md must carry what matters. "
    "Reply with one line saying what changed."
)

JOBS = {
    'daily': {
        'hour': 21, 'weekday': None,
        'prompt': (
            "[Scheduled: Daily Training Check]\n"
            "Run `python3 scripts/coach_check.py`. Summarise the JSON for the athlete in max 6 short lines, "
            "alerts first then positives. The JSON has no race fields, so never say there is no race on the "
            "calendar: take race context from marathons.json and memory. A low weekly_km means the week is "
            "still in progress, not undertraining. If something is worth remembering, add it to today's log."
        ),
    },
    'weekly': {
        'hour': 21, 'weekday': 6,   # Sunday
        'prompt': (
            "[Scheduled: Weekly Training Report]\n"
            "Run `python3 scripts/weekly_report.py`. Write a concise weekly digest, alerts first then wins. "
            "Compare volume to prior weeks, not to arbitrary ideals; strength target is 2x/week. Take race "
            "context from marathons.json and memory, never say there is no race on the calendar. Note "
            "anything lasting in today's log."
        ),
    },
}

logger = logging.getLogger('coach_bot')


# ----------------------------------------------------------------------------
# State
# ----------------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2), encoding='utf-8')
    tmp.replace(STATE_FILE)


# ----------------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------------

def telegram(method: str, payload: dict, timeout: int = 30) -> dict:
    token = os.environ['TELEGRAM_BOT_TOKEN']
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.load(resp)
    if not result.get('ok'):
        raise RuntimeError(f"Telegram {method} failed: {result.get('description', 'unknown')}")
    return result


def plain_text(text: str) -> str:
    """Drop markdown emphasis and headings; Telegram would show the symbols literally."""
    text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', lambda m: m[1] or m[2], text)
    return re.sub(r'(?m)^#{1,6}\s+', '', text)


def send_text(chat_id: str, text: str) -> None:
    for chunk in split_message(plain_text(text).strip() or '(no reply)'):
        telegram('sendMessage', {'chat_id': chat_id, 'text': chunk})


# ----------------------------------------------------------------------------
# Claude
# ----------------------------------------------------------------------------

def persona_prompt() -> str:
    """Persona and long-term memory, supplied as an appended system prompt."""
    parts = [TELEGRAM_FORMAT, TOOL_RULES]
    for name in ('COACH.md', 'MEMORY.md', 'TOOLS.md'):
        path = MEMORY_DIR / name
        if path.is_file():
            parts.append(path.read_text(encoding='utf-8'))
    parts.append(f"Today is {datetime.now(timezone.utc):%A %Y-%m-%d} (UTC).")
    return '\n\n'.join(parts)


def ask_claude(prompt: str, session_id: str | None) -> tuple[str, str | None]:
    """Run one Claude turn. Returns (reply text, session id to resume next time)."""
    cmd = ['claude', '-p', '--model', MODEL, '--output-format', 'json',
           '--permission-mode', 'dontAsk',
           '--append-system-prompt', persona_prompt()]
    if session_id:
        cmd += ['--resume', session_id]
    cmd += ['--allowedTools', *ALLOWED_TOOLS]
    env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}  # force subscription auth
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8',
                          cwd=REPO, env=env, timeout=CLAUDE_TIMEOUT)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.error("claude exited %s: %s", proc.returncode, proc.stderr.strip()[-500:])
        return "Sorry, I couldn't think that through just now (Claude error). Try again shortly.", session_id
    if result.get('is_error'):
        logger.error("claude returned an error result: %s", str(result.get('result'))[:500])
        if session_id:
            # A stale or missing session is the usual cause: start a new one next time.
            return "Sorry, that failed. I've started a fresh conversation; please resend.", None
        return "Sorry, that failed (Claude error). Try again shortly.", None
    return result.get('result', ''), result.get('session_id') or session_id


# ----------------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------------

def handle_message(state: dict, chat_id: str, text: str) -> None:
    if text.strip().lower() in ('/new', '/reset'):
        state.pop('session_id', None)
        save_state(state)
        send_text(chat_id, "Fresh conversation started. Memory files are kept.")
        return
    if text.strip().lower() == '/start':
        text = "Hi! Quick check-in please: how is my training looking?"
    try:
        telegram('sendChatAction', {'chat_id': chat_id, 'action': 'typing'})
    except (urllib.error.URLError, RuntimeError):
        pass
    reply, state['session_id'] = ask_claude(text, state.get('session_id'))
    save_state(state)
    send_text(chat_id, reply)


def run_job(state: dict, name: str, chat_id: str | None) -> str:
    reply, state['session_id'] = ask_claude(JOBS[name]['prompt'], state.get('session_id'))
    state.setdefault('last_run', {})[name] = datetime.now(timezone.utc).date().isoformat()
    save_state(state)
    if chat_id:
        send_text(chat_id, reply)
    if name == 'weekly':
        consolidate_and_reset(state)
    return reply


def consolidate_and_reset(state: dict) -> None:
    """Fold the week into MEMORY.md, then start a fresh conversation (keeps threads short)."""
    summary, _ = ask_claude(CONSOLIDATE_PROMPT, state.get('session_id'))
    logger.info("Weekly memory consolidation: %s", summary.strip()[:300])
    state.pop('session_id', None)
    save_state(state)


def due_jobs(state: dict, now: datetime) -> list:
    due = []
    for name, job in JOBS.items():
        if job['weekday'] is not None and now.weekday() != job['weekday']:
            continue
        scheduled = now.replace(hour=job['hour'], minute=0, second=0, microsecond=0)
        already = state.get('last_run', {}).get(name) == now.date().isoformat()
        if not already and scheduled <= now < scheduled + CATCH_UP_WINDOW:
            due.append(name)
    return due


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

def serve(chat_id: str) -> None:
    poll = os.environ.get('COACH_TELEGRAM_POLL', '1') != '0'
    state = load_state()
    logger.info("Coach bot started (model=%s, polling=%s)", MODEL, poll)
    while True:
        for name in due_jobs(state, datetime.now(timezone.utc)):
            logger.info("Running scheduled job: %s", name)
            try:
                run_job(state, name, chat_id)
            except Exception:
                logger.exception("Scheduled job %s failed", name)
                state.setdefault('last_run', {})[name] = datetime.now(timezone.utc).date().isoformat()
                save_state(state)

        if not poll:
            time.sleep(POLL_TIMEOUT)
            continue
        try:
            updates = telegram('getUpdates', {'offset': state.get('offset', 0), 'timeout': POLL_TIMEOUT,
                                              'allowed_updates': ['message']},
                               timeout=POLL_TIMEOUT + 10)['result']
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            # 409 means another bot instance (e.g. the old Zeabur OpenClaw) is polling this token.
            logger.warning("Telegram poll failed (%s); retrying", type(e).__name__)
            time.sleep(10)
            continue

        for update in updates:
            state['offset'] = update['update_id'] + 1
            save_state(state)
            message = update.get('message') or {}
            text = message.get('text')
            if str(message.get('chat', {}).get('id')) != chat_id or not text:
                continue   # ignore strangers, groups and non-text messages
            try:
                handle_message(state, chat_id, text)
            except Exception:
                logger.exception("Failed to handle message")
                try:
                    send_text(chat_id, "Something went wrong on my side. Please try again.")
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram bridge for the Claude Code marathon coach")
    parser.add_argument('--run-job', choices=sorted(JOBS), help="run one scheduled job now and exit")
    parser.add_argument('--print', action='store_true', help="with --run-job: print instead of sending")
    parser.add_argument('--whoami', action='store_true',
                        help="list chats that recently messaged the bot (to find TELEGRAM_CHAT_ID)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.whoami:
        # Read-only peek: no offset is sent, so these messages are still delivered to the bot later.
        for update in telegram('getUpdates', {'allowed_updates': ['message']})['result']:
            chat = (update.get('message') or {}).get('chat', {})
            if chat:
                print(f"chat_id={chat.get('id')}  name={chat.get('first_name', '')} {chat.get('username', '')}")
        return 0
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    needs_telegram = not (args.run_job and args.print)
    if needs_telegram and not (chat_id and os.environ.get('TELEGRAM_BOT_TOKEN')):
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set", file=sys.stderr)
        return 2

    if args.run_job:
        if args.print:
            # Dry run: throwaway session, state untouched, so tonight's real job still runs
            print(ask_claude(JOBS[args.run_job]['prompt'], None)[0])
        else:
            run_job(load_state(), args.run_job, chat_id)
        return 0
    serve(chat_id)
    return 0


if __name__ == '__main__':
    sys.exit(main())
