ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:2026.4.9
FROM python:3.12-slim-bookworm AS coach-python
FROM ${OPENCLAW_BASE_IMAGE}

USER root
COPY --from=coach-python /usr/local/ /usr/local/
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
       libbz2-1.0 libexpat1 libffi8 libgdbm6 liblzma5 libncursesw6 \
       libreadline8 libsqlite3-0 libssl3 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && ldconfig \
    && python3 -c 'import sys; assert sys.version_info[:2] == (3, 12)' \
    && python3 -m venv /opt/coach-venv
COPY requirements.txt /opt/coach-requirements.txt
RUN /opt/coach-venv/bin/pip install --no-cache-dir -r /opt/coach-requirements.txt
COPY entrypoint.sh /usr/local/bin/coach-entrypoint
RUN chmod 755 /usr/local/bin/coach-entrypoint \
    && test -f /app/openclaw.mjs \
    && test -x /usr/local/bin/openclaw
ENV PATH="/opt/coach-venv/bin:/usr/local/bin:/usr/bin:/bin"
WORKDIR /app
USER node
ENTRYPOINT ["/usr/local/bin/coach-entrypoint"]
