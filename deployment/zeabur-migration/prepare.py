"""Stage Zeabur exports offline. No Docker, network access or third-party Python packages.

Only the scalar subset used by these Zeabur exports is read, never their scripts.
Unsupported YAML forms fail closed instead of guessing how to interpret secrets.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import tarfile

HERE = Path(__file__).resolve().parent
PORTFOLIO_KEYS = {"ALCHEMY_API_KEY", "GOOGLE_SHEET_URL", "OPERATOR_IDS", "ORDER_NOTIF_GROUP_ID", "TG_BOT_TOKEN"}
TEMPLATES = ("compose.json", "Dockerfile", ".dockerignore", "entrypoint.sh", "prepare-host.sh", "offline_smoke.py", "README.md")


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


def restore_home(archive: Path, destination: Path) -> dict:
    """Write regular files only. Never materialize archive links or execute contents."""
    # npm caches contain deep paths; avoid Windows' legacy MAX_PATH limit.
    if os.name == "nt":
        destination = Path("\\\\?\\" + str(destination.resolve()))
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


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o600)


def write_env(path: Path, values: dict[str, str]) -> None:
    # Compose's format: raw preserves dollars, hashes and backslashes literally.
    if any(any(c in v for c in "\r\n\x00") for v in values.values()):
        raise ValueError("Multiline environment value")
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(values.items())), encoding="utf-8", newline="\n")
    path.chmod(0o600)


def rehearsal_config(original: dict, token: str) -> dict:
    cfg = copy.deepcopy(original)
    gateway = cfg.setdefault("gateway", {})
    gateway.update({"mode": "local", "bind": "lan", "port": 18789})
    gateway["auth"] = {"mode": "token", "token": token}
    gateway.pop("trustedProxies", None)
    gateway["controlUi"] = {"allowedOrigins": ["http://localhost:18789", "http://127.0.0.1:18789"]}
    # Remove channel credentials from the active rehearsal config entirely.
    cfg["channels"] = {name: {"enabled": False} for name in cfg.get("channels", {})}
    for name in cfg["channels"]:
        cfg.setdefault("plugins", {}).setdefault("entries", {}).setdefault(name, {})["enabled"] = False
    cfg.setdefault("cron", {})["enabled"] = False
    agents = cfg.setdefault("agents", {})
    agents.setdefault("defaults", {}).setdefault("heartbeat", {})["every"] = "0m"
    for agent in agents.get("list", []):
        agent.setdefault("heartbeat", {})["every"] = "0m"
    return cfg


def validate(bundle: Path) -> dict:
    cfg = json.loads((bundle / "runtime/home/.openclaw/openclaw.json").read_text(encoding="utf-8"))
    assert cfg["cron"]["enabled"] is False, "Rehearsal cron must be disabled"
    assert all(c.get("enabled") is False for c in cfg["channels"].values()), "Rehearsal channels must be disabled"
    assert cfg["agents"]["defaults"]["heartbeat"]["every"] == "0m"
    assert all(a.get("heartbeat", {}).get("every") == "0m" for a in cfg["agents"].get("list", []))
    compose = json.loads((bundle / "compose.json").read_text(encoding="utf-8"))
    assert compose["services"]["portfolio"]["profiles"] == ["cutover"]
    assert compose["services"]["openclaw"]["ports"] == ["127.0.0.1:18789:18789"]
    coach = bundle / "runtime/home/.config/marathon-training-coach"
    for filename in ("athlete_config.json", "marathons.json", "workout_notes.json", "strava_tokens.json", "garmin/garmin_tokens.json"):
        json.loads((coach / filename).read_text(encoding="utf-8"))
    skill = bundle / "runtime/home/.openclaw/workspace/skills/strava-training-coach"
    assert (skill / "SKILL.md").is_file()
    for name in ("coach_check.py", "weekly_report.py", "marathon_status.py", "garmin_fetcher.py"):
        assert (skill / "scripts" / name).is_file()
    return {"offline_validation": "passed", "runtime_test": "not performed by this offline validator"}


def prepare(args) -> dict:
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a fresh directory")
    services = export_services(args.openclaw_project)
    gateway = [s for s in services if s["image"].startswith("ghcr.io/openclaw/openclaw:")]
    portfolio = export_services(args.portfolio_project)
    if len(gateway) != 1 or len(portfolio) != 1 or set(portfolio[0]["env"]) != PORTFOLIO_KEYS:
        raise ValueError("Unexpected service inventory")
    environment = read_env(args.openclaw_env)
    # This unused Zeabur variable references itself in the exported environment.
    environment.pop("ZEABUR_AI_HUB_API_KEY", None)
    environment.pop("PUBLIC_DOMAIN", None)
    environment = resolve_env(environment)
    for key in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "ZAI_API_KEY", "TELEGRAM_BOT_TOKEN"):
        if not environment.get(key):
            raise ValueError("Missing required environment variable: " + key)
    output.mkdir(parents=True, mode=0o700)
    private = output / "secrets"
    private.mkdir(mode=0o700)
    (output / "runtime").mkdir()
    for filename in TEMPLATES:
        shutil.copyfile(HERE / filename, output / filename)
    inventory = restore_home(args.openclaw_backup, output / "runtime/home")
    inventory["devbox"] = restore_home(args.devbox_backup, private / "devbox-home")
    home = output / "runtime/home"
    config_path = home / ".openclaw/openclaw.json"
    original = json.loads(config_path.read_text(encoding="utf-8"))
    if original.get("agents", {}).get("defaults", {}).get("workspace") != "/home/node/.openclaw/workspace":
        raise ValueError("Unexpected workspace path")
    if set(original.get("models", {}).get("providers", {})) != {"zai"}:
        raise ValueError("Model provider needs review before removing Zeabur variables")
    write_json(private / "original-openclaw.json", original)
    write_env(private / "original-openclaw.env", environment)
    token = secrets.token_urlsafe(32)
    write_json(config_path, rehearsal_config(original, token))
    environment.pop("TELEGRAM_BOT_TOKEN", None)
    environment.pop("PASSWORD", None)
    environment["OPENCLAW_GATEWAY_TOKEN"] = token
    write_env(private / "openclaw.env", environment)
    write_env(private / "portfolio.env", resolve_env(portfolio[0]["env"]))
    cron_path = home / ".openclaw/cron/jobs.json"
    jobs = json.loads(cron_path.read_text(encoding="utf-8"))
    write_json(private / "original-jobs.json", jobs)
    for job in jobs.get("jobs", []):
        job["enabled"] = False
    write_json(cron_path, jobs)
    requirements = home / ".openclaw/workspace/skills/strava-training-coach/requirements.txt"
    shutil.copyfile(requirements, output / "requirements.txt")
    write_env(output / "runtime.env", {"OPENCLAW_BASE_IMAGE": gateway[0]["image"], "PORTFOLIO_IMAGE": portfolio[0]["image"]})
    source_paths = (args.openclaw_backup, args.devbox_backup, args.openclaw_env, args.openclaw_project, args.portfolio_project)
    inventory.update({"mode": "rehearsal", "source_sha256": {p.name: digest(p) for p in source_paths},
                      "images": {"openclaw": gateway[0]["image"], "portfolio": portfolio[0]["image"]},
                      "scheduled_jobs": len(jobs.get("jobs", [])),
                      "notes": ["Archive links were omitted: cached npm launchers and obsolete Zeabur backup/restore links.",
                                "Portfolio latest tag must be resolved to an image digest before cutover.",
                                "Original source files were not modified; original configuration is under secrets/."]})
    write_json(output / "inventory.json", inventory)
    result = validate(output)
    return {"bundle": str(output), "restored_regular_files": inventory["regular_files"], **result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", type=Path, help="Validate an existing rehearsal bundle without Docker")
    for flag in ("openclaw-backup", "devbox-backup", "openclaw-env", "openclaw-project", "portfolio-project", "output"):
        parser.add_argument("--" + flag, type=Path)
    args = parser.parse_args()
    if args.validate:
        print(json.dumps(validate(args.validate), indent=2))
        return
    if any(getattr(args, key) is None for key in ("openclaw_backup", "devbox_backup", "openclaw_env", "openclaw_project", "portfolio_project", "output")):
        parser.error("Supply all five source paths and --output")
    try:
        print(json.dumps(prepare(args), indent=2))
    except (ValueError, AssertionError, OSError, tarfile.TarError, json.JSONDecodeError) as error:
        # Avoid exposing source values or credential-bearing parser errors.
        parser.exit(1, f"Preparation failed ({type(error).__name__}). Sources are unchanged; inspect paths/formats privately and use a fresh output directory.\n")


if __name__ == "__main__":
    main()
