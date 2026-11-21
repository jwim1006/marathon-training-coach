"""Merge Zeabur's final coaching data into the live coach-data volume. Standard library only.

Three-way merge against BASE, the bundle the volume was originally seeded from:
    BASE      what both sides started with (.migration-private/coach/data)
    INCOMING  a fresh bundle's data/ built from the final Zeabur backup
    LIVE      the running volume (~/.config/marathon-training-coach)

For each file: changed only on Zeabur -> take it; changed only here -> keep it; changed on both ->
workout notes are unioned by entry, daily logs keep both texts, anything else keeps the live copy and
writes Zeabur's next to it as <name>.zeabur for review. Tokens, caches, logs and bot state are never
touched, so the new bot, Claude login and Garmin tokens survive.

Dry run by default; pass --apply to write. Run it through compose (see README, "Cut over").
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

# Owned by this host: provider tokens, script caches/state, logs and the bot's Telegram state.
LOCAL_ONLY_DIRS = ("garmin",)
LOCAL_ONLY_FILES = ("strava_tokens.json", "coach_state.json", "coach-memory/bot_state.json")
LIST_FILES = ("workout_notes.json", "coach-memory/workout-log.json")


def local_only(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (rel in LOCAL_ONLY_FILES or rel.split("/", 1)[0] in LOCAL_ONLY_DIRS
            or name.endswith((".log", ".zeabur")) or "cache" in name)


def files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} if root.is_dir() else set()


def read(root: Path, rel: str) -> bytes | None:
    path = root / rel
    return path.read_bytes() if path.is_file() else None


def union_entries(live: bytes, incoming: bytes) -> bytes | None:
    """Live entries plus Zeabur entries not already present, ordered by date. None if not JSON lists."""
    try:
        ours, theirs = json.loads(live), json.loads(incoming)
    except ValueError:
        return None
    if not (isinstance(ours, list) and isinstance(theirs, list)):
        return None
    seen = {json.dumps(e, sort_keys=True) for e in ours}
    extra = [e for e in theirs if json.dumps(e, sort_keys=True) not in seen]
    if not extra:
        return live
    merged = ours + extra
    merged.sort(key=lambda e: str(e.get("date", "")) if isinstance(e, dict) else "")
    return (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def merge_log(live: bytes, incoming: bytes) -> bytes:
    a, b = live.decode("utf-8").rstrip(), incoming.decode("utf-8").rstrip()
    if a in b:
        return incoming
    if b in a:
        return live
    return f"{a}\n\n## Merged from Zeabur\n\n{b}\n".encode("utf-8")


def plan(base: Path, incoming: Path, live: Path) -> list[tuple[str, str, bytes | None]]:
    """(action, relative path, new content) for every file that needs a write."""
    actions = []
    for rel in sorted(files(incoming)):
        if local_only(rel):
            continue
        b, i, l = read(base, rel), read(incoming, rel), read(live, rel)
        if i == l or i == b:
            continue  # same already, or unchanged on Zeabur
        if l is None or l == b:
            actions.append(("take-zeabur", rel, i))
        elif rel in LIST_FILES and (merged := union_entries(l, i)) is not None:
            if merged != l:
                actions.append(("union", rel, merged))
        elif rel.startswith("coach-memory/logs/") and rel.endswith(".md"):
            if (merged := merge_log(l, i)) != l:
                actions.append(("merge-log", rel, merged))
        else:
            actions.append(("conflict", rel + ".zeabur", i))
    return actions


def apply(live: Path, actions) -> None:
    for _, rel, content in actions:
        target = live / rel
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path)
    parser.add_argument("incoming", type=Path)
    parser.add_argument("live", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write the changes (default: dry run)")
    args = parser.parse_args()
    for root in (args.base, args.incoming, args.live):
        if not (root / "athlete_config.json").is_file():
            parser.error(f"{root} doesn't look like a coach data directory")
    actions = plan(args.base, args.incoming, args.live)
    for action, rel, _ in actions:
        print(f"{action:12} {rel}")
    if not actions:
        print("Nothing to merge: live data already has everything from Zeabur.")
    elif args.apply:
        apply(args.live, actions)
        print(f"Applied {len(actions)} change(s).")
    else:
        print("Dry run. Re-run with --apply to write.")
    if any(a == "conflict" for a, _, _ in actions):
        print("Conflicts: live copy kept; compare each *.zeabur file and ask the coach to fold in what matters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
