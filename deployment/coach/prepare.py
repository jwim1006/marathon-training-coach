"""Turn a Zeabur OpenClaw backup into a private coach bundle. No Docker, network or third-party packages.

Bundle layout (keep it out of Git; it holds credentials and personal data):
    data/          -> mounted at ~/.config/marathon-training-coach (includes coach-memory/)
    claude/        -> mounted at ~/.claude (Claude Code sessions)
    coach.env      -> bot and provider credentials; Telegram polling starts disabled
    portfolio.env  -> only with --portfolio-project
    images.env     -> portfolio image reference, only with --portfolio-project

Only the scalar subset used by the Zeabur exports is read, never their scripts.
Unsupported YAML forms fail closed instead of guessing how to interpret secrets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile

PORTFOLIO_KEYS = {"ALCHEMY_API_KEY", "GOOGLE_SHEET_URL", "OPERATOR_IDS", "ORDER_NOTIF_GROUP_ID", "TG_BOT_TOKEN"}
# Carried over from the OpenClaw environment; z.ai, Zeabur and gateway variables are dropped.
# Garmin email/password are deliberately NOT carried: without them the bot can only use saved
# tokens and can never hit Garmin's password endpoint (repeated logins get the IP rate-limited).
# Tokens come from the backup or from a manual `scripts/garmin_login.py` run.
COACH_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET")
# OpenClaw-only sections of MEMORY.md (heartbeats, NO_REPLY, cron, git-pull flow, GLM notes).
OPENCLAW_ONLY_SECTIONS = ("Operational Rules",)
WORKSPACE = PurePosixPath(".openclaw/workspace")
COACH_DATA = PurePosixPath(".config/marathon-training-coach")


def scalar(value: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        result = json.loads(value)
    elif value.startswith("'") and value.endswith("'"):
        result = value[1:-1].replace("''", "'")
    elif not value or value in {"|", "|-", ">", ">-", "null", "~"}:
        raise ValueError("Missing or unsupported exported scalar")
    elif " #" in value or value.startswith(("[", "{", "&", "*", "!")):
        raise ValueError("Unsupported YAML scalar; use a direct environment export")
    else:
        result = value
    if not isinstance(result, str) or any(c in result for c in "\r\n\x00"):
        raise ValueError("Environment values must be single-line strings")
    return result


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match or match[1] in values:
            raise ValueError("Invalid or duplicate environment assignment")
        values[match[1]] = scalar(match[2])
    return values


def read_dotenv(path: Path) -> dict[str, str]:
    """The skill's own .env: plain KEY=value lines with optional trailing comments, no secrets."""
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*([^#\s]*)\s*(?:#.*)?", line)
        if match and match[2]:
            values[match[1]] = match[2]
    return values


def resolve_env(values: dict[str, str]) -> dict[str, str]:
    def resolve(key: str, seen: set[str]) -> str:
        if key in seen or key not in values:
            raise ValueError("Unresolved environment reference: " + key)
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                      lambda m: resolve(m[1], seen | {key}), values[key])
    return {key: resolve(key, set()) for key in values}


def export_services(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    chunks = re.split(r"(?m)^ {8}- name: ", text)[1:]
    if not chunks:
        raise ValueError("Unsupported Zeabur service layout")
    services = []
    for chunk in chunks:
        image = re.search(r"(?m)^ {16}image: (.+)$", chunk)
        if not image:
            raise ValueError("Expected a Docker image source")
        image_value = scalar(image[1])
        if not re.fullmatch(r"[a-zA-Z0-9_./:@-]+", image_value):
            raise ValueError("Unsupported image reference")
        env_section = re.search(r"(?ms)^ {12}env:\s*\n(.*?)(?=^ {12}\S|\Z)", chunk)
        env = {}
        if env_section:
            for key, value in re.findall(r"(?m)^ {16}([A-Z][A-Z0-9_]*):\s*\n {20}default: (.+)$", env_section[1]):
                env[key] = scalar(value)
        services.append({"name": scalar(chunk.splitlines()[0]), "image": image_value, "env": env})
    return services


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def long_path(path: Path) -> Path:
    # npm caches in the backup contain deep paths; avoid Windows' legacy MAX_PATH limit.
    return Path("\\\\?\\" + str(path.resolve())) if os.name == "nt" else path


def restore_home(archive: Path, destination: Path) -> dict:
    """Write regular files only. Never materialize archive links or execute contents."""
    destination = long_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    count = 0
    skipped_links = []
    seen = set()
    with tarfile.open(archive, "r:*") as source:
        for member in source:
            raw = member.name.rstrip("/")
            path = PurePosixPath(raw)
            if path.is_absolute() or ".." in path.parts or "\\" in raw or ":" in raw:
                raise ValueError("Unsafe archive member path")
            if raw == "data/_manifest.json" or raw in {"data", "data/data"}:
                continue
            if not raw.startswith("data/data/"):
                raise ValueError("Unexpected archive root")
            relative = PurePosixPath(raw.removeprefix("data/data/"))
            # Case-fold too: staging may run on a case-insensitive Windows filesystem.
            folded = str(relative).casefold()
            if folded in seen:
                raise ValueError("Duplicate archive member")
            seen.add(folded)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(member.mode & 0o777)
                count += 1
            elif member.issym() or member.islnk():
                skipped_links.append(str(relative))
            else:
                raise ValueError("Unsupported special archive member")
    return {"regular_files": count, "skipped_links": skipped_links}


def _make_writable_and_retry(func, path, _exc) -> None:
    # Restored git objects are read-only, which Windows refuses to delete.
    os.chmod(path, 0o700)
    func(path)


def strip_sections(text: str, titles: tuple[str, ...]) -> str:
    """Drop markdown sections (heading through the next heading of the same or higher level)."""
    out, skip_level = [], None
    for line in text.splitlines():
        heading = re.match(r"(#+)\s+(.*)", line)
        if heading:
            level = len(heading[1])
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and heading[2].strip() in titles:
                skip_level = level
        if skip_level is None:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"


def keep_sections(text: str, titles: tuple[str, ...]) -> str:
    """Keep only the named level-2 sections (plus the document title)."""
    out, keeping = [], True
    for line in text.splitlines():
        heading = re.match(r"(#+)\s+(.*)", line)
        if heading and len(heading[1]) <= 2:
            keeping = len(heading[1]) == 1 or heading[2].strip() in titles
        if keeping:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"


def repath(text: str) -> str:
    """Rewrite OpenClaw container paths to the new layout."""
    for old, new in (
        ("~/.openclaw/workspace/skills/strava-training-coach/", "(repo) "),
        ("skills/strava-training-coach/", "(repo) "),
        ("/home/node/", "~/"),
        ("`memory/YYYY-MM-DD.md`", "`coach-memory/logs/YYYY-MM-DD.md`"),
        ("`memory/workout-log.json`", "`coach-memory/workout-log.json`"),
    ):
        text = text.replace(old, new)
    return text


def convert_memory(workspace: Path, memory_dir: Path) -> dict:
    """OpenClaw workspace -> coach-memory/ (persona, lean memory index, dated logs)."""
    memory_dir.mkdir(parents=True)
    read = lambda name: (workspace / name).read_text(encoding="utf-8")
    persona = ["# Coach persona", "",
               "Who you are and who you coach. Carried over from the OpenClaw workspace.", ""]
    for name in ("SOUL.md", "IDENTITY.md", "USER.md"):
        persona += [read(name).rstrip(), ""]
    (memory_dir / "COACH.md").write_text("\n".join(persona), encoding="utf-8", newline="\n")
    (memory_dir / "MEMORY.md").write_text(repath(strip_sections(read("MEMORY.md"), OPENCLAW_ONLY_SECTIONS)),
                                          encoding="utf-8", newline="\n")
    (memory_dir / "TOOLS.md").write_text(repath(keep_sections(read("TOOLS.md"), ("Running Data",))),
                                         encoding="utf-8", newline="\n")
    logs = memory_dir / "logs"
    logs.mkdir()
    count = 0
    for log in sorted((workspace / "memory").glob("????-??-??.md")):
        shutil.copyfile(log, logs / log.name)
        count += 1
    if (workspace / "memory/workout-log.json").is_file():
        shutil.copyfile(workspace / "memory/workout-log.json", memory_dir / "workout-log.json")
    return {"daily_logs": count}


def write_env(path: Path, values: dict[str, str]) -> None:
    # Compose's raw format preserves dollars, hashes and backslashes literally.
    if any(any(c in v for c in "\r\n\x00") for v in values.values()):
        raise ValueError("Multiline environment value")
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(values.items())), encoding="utf-8", newline="\n")
    path.chmod(0o600)


def telegram_chat_id(jobs: dict) -> str:
    targets = {j.get("delivery", {}).get("to") for j in jobs.get("jobs", [])
               if j.get("delivery", {}).get("channel") == "telegram"}
    targets.discard(None)
    if len(targets) != 1 or not re.fullmatch(r"-?\d+", str(next(iter(targets)))):
        raise ValueError("Expected exactly one Telegram delivery chat in the cron jobs")
    return str(targets.pop())


def validate(bundle: Path) -> dict:
    data = bundle / "data"
    for filename in ("athlete_config.json", "marathons.json", "workout_notes.json", "garmin/garmin_tokens.json"):
        json.loads((data / filename).read_text(encoding="utf-8"))
    for name in ("COACH.md", "MEMORY.md", "TOOLS.md"):
        assert (data / "coach-memory" / name).is_file(), "Missing coach-memory/" + name
    memory = (data / "coach-memory/MEMORY.md").read_text(encoding="utf-8")
    assert "Heartbeat" not in memory and "NO_REPLY" not in memory, "OpenClaw-only rules left in MEMORY.md"
    env = read_env(bundle / "coach.env")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DATA_PROVIDER"):
        assert env.get(key), "Missing " + key
    assert not any(k.startswith(("ZAI", "ZEABUR", "OPENCLAW")) for k in env), "OpenClaw/z.ai variables left in coach.env"
    assert "GARMIN_PASSWORD" not in env, "Garmin password must not be in coach.env (tokens only)"
    assert env.get("COACH_TELEGRAM_POLL") in {"0", "1"}, "COACH_TELEGRAM_POLL must be 0 or 1"
    return {"offline_validation": "passed"}


def prepare(args) -> dict:
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a fresh directory")
    environment = read_env(args.openclaw_env)
    # This unused Zeabur variable references itself in the exported environment.
    environment.pop("ZEABUR_AI_HUB_API_KEY", None)
    environment = resolve_env(environment)
    output.mkdir(parents=True, mode=0o700)
    staging = output / "_staging"
    inventory = restore_home(args.openclaw_backup, staging)
    home = long_path(staging)
    try:
        shutil.copytree(home / COACH_DATA, output / "data")
        inventory.update(convert_memory(home / WORKSPACE, output / "data/coach-memory"))
        jobs = json.loads((home / ".openclaw/cron/jobs.json").read_text(encoding="utf-8"))
        skill_env = home / WORKSPACE / "skills/strava-training-coach/.env"
        coach_env = read_dotenv(skill_env) if skill_env.is_file() else {}
    finally:
        shutil.rmtree(home, onexc=_make_writable_and_retry)
    coach_env.update({k: environment[k] for k in COACH_ENV_KEYS if environment.get(k)})
    coach_env.setdefault("DATA_PROVIDER", "garmin")
    coach_env["TELEGRAM_CHAT_ID"] = telegram_chat_id(jobs)
    # Only one process may poll a bot token: keep polling off until Zeabur is stopped.
    coach_env["COACH_TELEGRAM_POLL"] = "0"
    write_env(output / "coach.env", coach_env)
    (output / "claude").mkdir(mode=0o700)
    sources = [args.openclaw_backup, args.openclaw_env]
    if args.portfolio_project:
        portfolio = export_services(args.portfolio_project)
        if len(portfolio) != 1 or set(portfolio[0]["env"]) != PORTFOLIO_KEYS:
            raise ValueError("Unexpected portfolio service inventory")
        write_env(output / "portfolio.env", resolve_env(portfolio[0]["env"]))
        write_env(output / "images.env", {"PORTFOLIO_IMAGE": portfolio[0]["image"]})
        sources.append(args.portfolio_project)
    inventory["source_sha256"] = {p.name: digest(p) for p in sources}
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    return {"bundle": str(output), "daily_logs": inventory["daily_logs"], **validate(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validate", type=Path, help="Validate an existing bundle")
    parser.add_argument("--openclaw-backup", type=Path, help="Zeabur volume backup .tar of the OpenClaw service")
    parser.add_argument("--openclaw-env", type=Path, help="Exported OpenClaw environment variables")
    parser.add_argument("--portfolio-project", type=Path, help="Optional: exported portfolio-monitor project YAML")
    parser.add_argument("--output", type=Path, help="Fresh bundle directory to create")
    args = parser.parse_args()
    if args.validate:
        print(json.dumps(validate(args.validate), indent=2))
        return
    if not (args.openclaw_backup and args.openclaw_env and args.output):
        parser.error("Supply --openclaw-backup, --openclaw-env and --output")
    try:
        print(json.dumps(prepare(args), indent=2))
    except (ValueError, AssertionError, OSError, tarfile.TarError, json.JSONDecodeError) as error:
        # Avoid exposing source values or credential-bearing parser errors.
        parser.exit(1, f"Preparation failed ({type(error).__name__}: {error if isinstance(error, (ValueError, AssertionError)) else 'see paths'}). "
                       "Sources are unchanged; use a fresh output directory.\n")


if __name__ == "__main__":
    main()
