# Marathon coach on Claude Code (replaces OpenClaw + z.ai)

One container: `coach_bot.py` long-polls your Telegram bot, answers you by running `claude -p` (Sonnet,
your Claude subscription) in this repo, and posts the daily check (21:00 UTC) and the Sunday weekly report
into the same conversation, so you can reply to an alert directly. The same `compose.yaml` runs on
this PC (Docker Desktop; only while the PC is awake) or on a VPS (24/7).

Telegram commands: `/new` starts a fresh conversation (memory files are kept).

Memory: `coach-memory/COACH.md`, `MEMORY.md` and `TOOLS.md` are loaded into every conversation; daily
logs (`coach-memory/logs/`) are read only when needed. The coach writes lasting preferences straight
into `MEMORY.md`. After each Sunday weekly report it folds the week's logs into `MEMORY.md` and starts a
fresh conversation, so threads stay short and nothing lasting depends on chat history.

## 1. Build the private bundle

```bash
python -I deployment/coach/prepare.py \
  --openclaw-backup ~/Desktop/openclaw-backup-<id>.tar \
  --openclaw-env ~/Desktop/openclaw.env.txt \
  --portfolio-project ~/Desktop/portfolio-monitor-project.yaml \
  --output .migration-private/coach
```

It contains your credentials and personal data and is gitignored. Telegram polling starts **off**
(`COACH_TELEGRAM_POLL=0`), because only one process can poll a bot token while Zeabur is still running.

## 2. Log in to Claude (once per host)

On any machine with a browser, run `claude setup-token` and append the token it prints to the bundle's `coach.env`:

```
CLAUDE_CODE_OAUTH_TOKEN=<token>
```

Use a separate token for the PC and for the VPS so you can revoke either one on its own. Don't set
`ANTHROPIC_API_KEY`, because it would take precedence over the token.

## 3. Rehearse (Zeabur still live)

```bash
cd deployment/coach
docker compose build
docker compose --profile seed run --rm seed     # copy bundle data into the coach-data volume (once)
docker compose up -d
docker compose exec coach python3 scripts/coach_check.py                    # live Garmin data
docker compose exec coach python3 deployment/coach/coach_bot.py --run-job daily --print   # Sonnet summary, not sent
```

Run `docker compose stop` when you finish. Polling off only stops the bot reading Telegram: the 21:00
schedule still runs and would send a second report alongside Zeabur's.

## 4. Cut over

The bot is already live with its own Telegram bot, Claude token and Garmin login. Cutover only brings
over what Rhino wrote on Zeabur since the first seed. **Don't re-seed and don't replace `coach.env`:**
a fresh bundle carries the old bot token, the old chat ID and Zeabur's Garmin tokens.

1. In Zeabur, stop the OpenClaw service (so it writes nothing more), then download a fresh volume backup
   and env export.
2. Build a second bundle beside the live one (the live `.migration-private/coach/` must stay untouched;
   it's the merge base):
   ```bash
   python -I deployment/coach/prepare.py --openclaw-backup <new tar> --openclaw-env <new env> \
     --output .migration-private/coach-final
   ```
3. From `deployment/coach`, preview the merge, then apply it:
   ```bash
   docker compose build
   SYNC_FROM=../../.migration-private/coach-final docker compose --profile sync run --rm sync
   docker compose stop coach
   SYNC_FROM=../../.migration-private/coach-final docker compose --profile sync run --rm sync --apply
   docker compose up -d
   ```
   Files changed only on Zeabur are taken; workout notes are unioned; daily logs edited on both sides
   keep both texts. Anything else changed on both sides keeps the live copy and leaves `<name>.zeabur`
   beside it: ask the coach to compare and fold it in. Garmin/Strava tokens, caches, logs and the bot's
   Telegram state are never touched.
4. Message the bot ("what did we do last week?") to check the merged memory.
5. Revoke the z.ai and Zeabur AI Hub keys and the old Telegram bot token, and change the Garmin password
   (Zeabur incident), then rerun `scripts/garmin_login.py` yourself (see "Garmin login" below).
6. Portfolio bot: stop it on Zeabur, run `docker login ghcr.io`, then `docker compose --env-file <bundle>/images.env --profile portfolio up -d`.
7. Delete `.migration-private/coach-final`, `.migration-private/rehearsal` and the downloads.

## Garmin login

The bot only uses saved tokens; it never has your password. When Garmin rejects them (password change,
long gap), run this yourself in a real terminal from `deployment/coach`, once:

```bash
docker compose run --rm -it coach python3 scripts/garmin_login.py
```

Each run is a password login. If Garmin answers 429, wait a few hours rather than retrying.

## VPS later

Copy the repo and the bundle to the server, run `sh deployment/coach/prepare-host.sh <bundle>`, then
`COACH_BUNDLE=<bundle> docker compose up -d --build`. Stop the PC copy first, because only one copy can poll Telegram.

## Security

- The bot answers only `TELEGRAM_CHAT_ID`.
- Claude runs in `dontAsk` mode, limited to the `python3 scripts/*` commands, reading files, and editing `coach-memory/`.
- The container has no open ports.
- On a VPS, `prepare-host.sh` sets up key-only SSH, a firewall that only allows SSH in, and automatic security updates.
- If the server is ever compromised, revoke its Claude token at claude.ai.
