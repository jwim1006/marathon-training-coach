#!/bin/sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
test -f "$bundle_dir/inventory.json"
test -f "$bundle_dir/runtime/home/.openclaw/openclaw.json"
test ! -L "$bundle_dir/runtime"
test ! -L "$bundle_dir/runtime/home"
sudo chown -R 1000:1000 "$bundle_dir/runtime/home"
sudo chmod 700 "$bundle_dir/runtime/home" "$bundle_dir/runtime/home/.openclaw"
find "$bundle_dir/secrets" -type d -exec chmod 700 {} +
find "$bundle_dir/secrets" -type f -exec chmod 600 {} +
echo 'Restored home ownership set for the non-root OpenClaw container.'
