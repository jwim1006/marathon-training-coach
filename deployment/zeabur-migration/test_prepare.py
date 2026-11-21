"""Offline regression checks for archive confinement and inactive rehearsal state."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import prepare

TMP = Path(__file__).resolve().parents[2] / ".migration-private/offline-tests"
TMP.mkdir(parents=True, exist_ok=True)


class PreparationTests(unittest.TestCase):
    def test_rehearsal_removes_channel_credentials_and_disables_schedules(self):
        original = {"channels": {"telegram": {"botToken": "sensitive"}},
                    "cron": {"enabled": True}, "agents": {"list": [{"id": "a", "heartbeat": {"every": "30m"}}]},
                    "gateway": {"controlUi": {"dangerouslyDisableDeviceAuth": True}}}
        staged = prepare.rehearsal_config(original, "fresh")
        self.assertEqual(staged["channels"], {"telegram": {"enabled": False}})
        self.assertFalse(staged["plugins"]["entries"]["telegram"]["enabled"])
        self.assertFalse(staged["cron"]["enabled"])
        self.assertEqual(staged["agents"]["list"][0]["heartbeat"]["every"], "0m")
        self.assertNotIn("dangerouslyDisableDeviceAuth", staged["gateway"]["controlUi"])
        self.assertEqual(original["channels"]["telegram"]["botToken"], "sensitive")

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


if __name__ == "__main__":
    unittest.main()
