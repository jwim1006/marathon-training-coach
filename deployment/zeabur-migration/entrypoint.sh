#!/bin/sh
set -eu
test -f /home/node/.openclaw/openclaw.json || {
    echo 'Restore the OpenClaw backup before starting.' >&2
    exit 1
}
test -w /home/node/.openclaw || {
    echo 'Run prepare-host.sh on the Linux host to set restored file ownership.' >&2
    exit 1
}
exec "$@"
