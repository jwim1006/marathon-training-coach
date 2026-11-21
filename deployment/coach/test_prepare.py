"""Offline regression checks for archive confinement, memory conversion and the bot schedule."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
import coach_bot
import prepare
import sync_data

TMP = Path(__file__).resolve().parents[2] / ".migration-private/offline-tests"
TMP.mkdir(parents=True, exist_ok=True)


class PreparationTests(unittest.TestCase):
    def test_env_reference_resolution_and_cycles(self):
        self.assertEqual(prepare.resolve_env({"A": "${B}", "B": "literal$#\\value"})["A"], "literal$#\\value")
        with self.assertRaises(ValueError):
            prepare.resolve_env({"A": "${B}", "B": "${A}"})
        with self.assertRaises(ValueError):
            prepare.resolve_env({"A": "${MISSING}"})

    def test_raw_env_preserves_literal_special_characters(self):
        with tempfile.TemporaryDirectory(dir=TMP) as root:
            path = Path(root) / "env"
            prepare.write_env(path, {"TOKEN": "a$#\\b"})
            self.assertEqual(path.read_text(), "TOKEN=a$#\\b\n")
            with self.assertRaises(ValueError):
                prepare.write_env(path, {"TOKEN": "a\nb"})

    def test_skill_dotenv_strips_comments_and_blanks(self):
        with tempfile.TemporaryDirectory(dir=TMP) as root:
            path = Path(root) / ".env"
            path.write_text("# comment\nMAX_WEEKLY_MILEAGE_JUMP=30      # 5-100%\nVERBOSE=\nDATA_PROVIDER=garmin\n")
            self.assertEqual(prepare.read_dotenv(path), {"MAX_WEEKLY_MILEAGE_JUMP": "30", "DATA_PROVIDER": "garmin"})

    def archive(self, root, members):
        path = Path(root) / "backup.tar"
        with tarfile.open(path, "w") as archive:
            for name, value, link in members:
                member = tarfile.TarInfo(name)
                if link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = value
                    archive.addfile(member)
                else:
                    payload = value.encode()
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
        return path

    def test_restore_never_follows_archive_links(self):
        with tempfile.TemporaryDirectory(dir=TMP) as root:
            path = self.archive(root, [("data/data/ok.json", "{}", False),
                                      ("data/data/escape", "/etc/passwd", True)])
            output = Path(root) / "home"
            result = prepare.restore_home(path, output)
            self.assertEqual(json.loads((output / "ok.json").read_text()), {})
            self.assertFalse((output / "escape").exists())
            self.assertEqual(result["skipped_links"], ["escape"])

    def test_restore_rejects_traversal_absolute_and_windows_paths(self):
        for name in ["data/data/../../escape", "/etc/passwd", "data/data/C:stream", "data/data/a\\b"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=TMP) as root:
                archive = self.archive(root, [(name, "x", False)])
                with self.assertRaises(ValueError):
                    prepare.restore_home(archive, Path(root) / "home")

    def test_restore_rejects_case_collisions_without_overwrite(self):
        with tempfile.TemporaryDirectory(dir=TMP) as root:
            archive = self.archive(root, [("data/data/A", "first", False), ("data/data/a", "second", False)])
            with self.assertRaises(ValueError):
                prepare.restore_home(archive, Path(root) / "home")
            self.assertEqual((Path(root) / "home/A").read_text(), "first")

    def test_export_parser_does_not_execute_config_scripts(self):
        with tempfile.TemporaryDirectory(dir=TMP) as root:
            path = Path(root) / "project.yaml"
            path.write_text('        - name: bot\n            source:\n                image: ghcr.io/example/bot:latest\n'
                            '            env:\n                TOKEN:\n                    default: "a$#b"\n'
                            '                    expose: false\n            configs:\n                template: |\n'
                            '                    do-not-run-this\n')
            services = prepare.export_services(path)
            self.assertEqual(services[0]["env"], {"TOKEN": "a$#b"})

    def test_memory_conversion_drops_openclaw_rules_and_repaths(self):
        with tempfile.TemporaryDirectory(dir=TMP) as root:
            ws = Path(root) / "workspace"
            (ws / "memory").mkdir(parents=True)
            for name in ("SOUL.md", "IDENTITY.md", "USER.md"):
                (ws / name).write_text(f"# {name}\nbody\n")
            (ws / "MEMORY.md").write_text(
                "# Memory\n## Status\nNext race: Valencia\nLogs: `memory/YYYY-MM-DD.md`\n"
                "## Operational Rules\n### Heartbeats\nevery 4h\n### Silent Replies\nNO_REPLY\n"
                "## Coach's Notes\nwatch load\n")
            (ws / "TOOLS.md").write_text("# Tools\n## Running Data\nPrimary: `/home/node/.config/x.json`\n"
                                         "## What Goes Here\nboilerplate\n")
            (ws / "memory/2026-10-18.md").write_text("race day")
            (ws / "memory/heartbeat-state.json").write_text("{}")
            out = Path(root) / "coach-memory"
            self.assertEqual(prepare.convert_memory(ws, out), {"daily_logs": 1})
            memory = (out / "MEMORY.md").read_text()
            self.assertNotIn("Heartbeats", memory)
            self.assertNotIn("NO_REPLY", memory)
            self.assertIn("watch load", memory)
            self.assertIn("`coach-memory/logs/YYYY-MM-DD.md`", memory)
            tools = (out / "TOOLS.md").read_text()
            self.assertIn("~/.config/x.json", tools)
            self.assertNotIn("boilerplate", tools)
            self.assertIn("# SOUL.md", (out / "COACH.md").read_text())
            self.assertEqual([p.name for p in (out / "logs").iterdir()], ["2026-10-18.md"])

    def test_chat_id_must_be_single_telegram_target(self):
        job = lambda to: {"delivery": {"channel": "telegram", "to": to}}
        self.assertEqual(prepare.telegram_chat_id({"jobs": [job("123"), job("123")]}), "123")
        with self.assertRaises(ValueError):
            prepare.telegram_chat_id({"jobs": [job("123"), job("456")]})
        with self.assertRaises(ValueError):
            prepare.telegram_chat_id({"jobs": []})


class ScheduleTests(unittest.TestCase):
    def at(self, *args):
        return datetime(*args, tzinfo=timezone.utc)

    def test_daily_due_at_21_utc_once(self):
        self.assertEqual(coach_bot.due_jobs({}, self.at(2026, 11, 3, 20, 59)), [])
        self.assertEqual(coach_bot.due_jobs({}, self.at(2026, 11, 3, 21, 0)), ["daily"])
        done = {"last_run": {"daily": "2026-11-03"}}
        self.assertEqual(coach_bot.due_jobs(done, self.at(2026, 11, 3, 21, 30)), [])

    def test_weekly_only_on_sunday(self):
        self.assertEqual(coach_bot.due_jobs({}, self.at(2026, 11, 8, 21, 5)), ["daily", "weekly"])  # Sunday

    def test_plain_text_strips_markdown_for_telegram(self):
        self.assertEqual(coach_bot.plain_text("## Week\n**Next race:** Valencia 4:23/km*"),
                         "Week\nNext race: Valencia 4:23/km*")

    def test_missed_job_catches_up_within_window_only(self):
        self.assertEqual(coach_bot.due_jobs({}, self.at(2026, 11, 3, 23, 30)), ["daily"])
        self.assertEqual(coach_bot.due_jobs({}, self.at(2026, 11, 4, 0, 30)), [])


class SyncTests(unittest.TestCase):
    def tree(self, root, files):
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")

    def test_three_way_merge_keeps_local_secrets_and_new_work(self):
        note = lambda d: {"date": d, "type": "easy"}
        with tempfile.TemporaryDirectory(dir=TMP) as root:
            base, incoming, live = (Path(root) / n for n in ("base", "incoming", "live"))
            self.tree(base, {"athlete_config.json": "{}", "marathons.json": "old", "athlete_context.md": "v1",
                             "workout_notes.json": json.dumps([note("2026-10-01")]),
                             "coach-memory/MEMORY.md": "m1", "garmin/garmin_tokens.json": "zeabur-old"})
            self.tree(incoming, {"athlete_config.json": "{}", "marathons.json": "new", "athlete_context.md": "v2-zeabur",
                                 "workout_notes.json": json.dumps([note("2026-10-01"), note("2026-11-05")]),
                                 "coach-memory/MEMORY.md": "m1", "coach-memory/logs/2026-11-05.md": "z",
                                 "coach-memory/logs/2026-11-01.md": "zeabur day",
                                 "garmin/garmin_tokens.json": "zeabur-new", "coach-memory/bot_state.json": "x",
                                 "activities_cache_garmin.json": "[]", "coach.log": "log"})
            self.tree(live, {"athlete_config.json": "{}", "marathons.json": "old", "athlete_context.md": "v2-local",
                             "workout_notes.json": json.dumps([note("2026-10-01"), note("2026-11-02")]),
                             "coach-memory/MEMORY.md": "m2-local", "coach-memory/logs/2026-11-01.md": "local day",
                             "garmin/garmin_tokens.json": "mine"})
            actions = {rel: action for action, rel, _ in sync_data.plan(base, incoming, live)}
            self.assertEqual(actions, {"marathons.json": "take-zeabur", "athlete_context.md.zeabur": "conflict",
                                       "workout_notes.json": "union", "coach-memory/logs/2026-11-05.md": "take-zeabur",
                                       "coach-memory/logs/2026-11-01.md": "merge-log"})
            sync_data.apply(live, sync_data.plan(base, incoming, live))
            self.assertEqual((live / "garmin/garmin_tokens.json").read_text(), "mine")
            self.assertEqual((live / "athlete_context.md").read_text(), "v2-local")
            self.assertEqual((live / "coach-memory/MEMORY.md").read_text(), "m2-local")
            dates = [n["date"] for n in json.loads((live / "workout_notes.json").read_text())]
            self.assertEqual(dates, ["2026-10-01", "2026-11-02", "2026-11-05"])
            log = (live / "coach-memory/logs/2026-11-01.md").read_text()
            self.assertIn("local day", log)
            self.assertIn("zeabur day", log)
            # Re-running is idempotent: only the unresolved conflict copy is rewritten.
            self.assertEqual(sync_data.plan(base, incoming, live),
                             [("conflict", "athlete_context.md.zeabur", b"v2-zeabur")])


if __name__ == "__main__":
    unittest.main()
