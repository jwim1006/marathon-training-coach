# Marathon coach: Claude Code + the coach scripts + the Telegram bridge. Build from the repo root.
# garminconnect needs Python >= 3.12; Claude Code needs a current Node (Debian's is too old).
FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm

ARG CLAUDE_CODE_VERSION=latest

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && useradd --create-home --uid 1000 coach

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY CLAUDE.md SKILL.md /app/
COPY scripts /app/scripts
COPY references /app/references
COPY deployment/coach/coach_bot.py deployment/coach/sync_data.py /app/deployment/coach/

# Everything Claude Code persists (sessions, settings) lives in the mounted ~/.claude.
ENV COACH_REPO=/app \
    XDG_CONFIG_HOME=/home/coach/.config \
    CLAUDE_CONFIG_DIR=/home/coach/.claude \
    DISABLE_AUTOUPDATER=1 \
    PYTHONUNBUFFERED=1

USER coach
WORKDIR /app
RUN mkdir -p /home/coach/.config/marathon-training-coach /home/coach/.claude

ENTRYPOINT ["tini", "--"]
CMD ["python3", "/app/deployment/coach/coach_bot.py"]
