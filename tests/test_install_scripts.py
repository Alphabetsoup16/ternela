# SPDX-License-Identifier: Apache-2.0
"""Management failures must leave unrelated installs and saved choices untouched."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from expansion_lab.storage import PROJECT


@unittest.skipIf(os.name == "nt", "POSIX entry point; PowerShell has a separate probe")
class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.install = self.root / "managed"
        self.install.mkdir()
        (self.install / "state").mkdir()
        (self.install / ".ternela-install").write_text("0123456789abcdef\n")
        (self.install / ".ternela-engine").write_text("engine-a\n")
        (self.install / ".ternela-storage").write_text("native-v1\n")
        (self.install / "compose.yaml").write_text("services: {}\n")
        (self.install / "docker.env").write_text(
            "TERNELA_INSTALL_ID=0123456789abcdef\nTERNELA_PORT=8765\nTERNELA_UID=1000\nTERNELA_GID=1000\nTERNELA_AUTO_UPDATE=false\n"
        )
        binary = self.root / "bin"
        binary.mkdir()
        script = binary / "docker"
        script.write_text(
            f"#!{sys.executable}\n"
            + """import json,os,sys
from pathlib import Path
args=sys.argv[1:]
context=None
if args[:1]==['--context']: context,args=args[1],args[2:]
with Path(os.environ['TEST_DOCKER_LOG']).open('a') as stream:
 stream.write(json.dumps({'args':args,'context':context,'vars':{k:v for k,v in os.environ.items() if k.startswith('TERNELA_')}})+'\\n')
if args[0]=='info' and '--format' in args: print(os.environ.get('TEST_COLIMA_ENGINE','engine-a') if context=='colima' else os.environ.get('TEST_ENGINE','engine-a'))
if args[:2]==['volume','ls'] and os.environ.get('TEST_VOLUME_OWNER'): print('ternela-0123456789abcdef-state')
if args[:2]==['volume','inspect']: print(os.environ.get('TEST_VOLUME_OWNER',''))
if args[0]=='compose' and 'ps' in args and os.environ.get('TEST_RUNNING'): print('running-id')
if args[0]=='compose' and 'up' in args and os.environ.get('TEST_START_FAILURE'): sys.exit(42)
"""
        )
        script.chmod(0o755)
        self.log = self.root / "docker.log"
        self.env = dict(
            os.environ,
            PATH=str(binary) + os.pathsep + os.environ["PATH"],
            TEST_DOCKER_LOG=str(self.log),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_script(self, *arguments):
        return subprocess.run(
            ["sh", str(PROJECT / "scripts/ternela.sh"), *arguments],
            env=self.env,
            text=True,
            capture_output=True,
        )

    def test_parent_environment_cannot_override_installed_configuration(self):
        self.env.update(
            TERNELA_PORT="9999",
            TERNELA_AUTO_UPDATE="true",
            TERNELA_INSTALL_ID="another",
            TERNELA_STATE="/unexpected",
            TERNELA_CONTAINER="1",
            TERNELA_PUBLIC_PORT="9998",
        )
        result = self.run_script("start", "--dir", str(self.install))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        compose = [r for r in calls if r["args"][0] == "compose" and "up" in r["args"]]
        self.assertEqual(compose[0]["vars"], {})

    def test_saved_context_wins_without_changing_global_context(self):
        (self.install / ".ternela-context").write_text("colima\n")
        (self.install / ".ternela-engine").write_text("engine-colima\n")
        self.env.update(
            DOCKER_CONTEXT="desktop-linux",
            DOCKER_HOST="unix:///unrelated.sock",
            TEST_ENGINE="engine-desktop",
            TEST_COLIMA_ENGINE="engine-colima",
        )
        result = self.run_script("start", "--dir", str(self.install))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(calls)
        self.assertTrue(all(call["context"] == "colima" for call in calls))
        self.assertFalse(any(call["args"][:2] == ["context", "use"] for call in calls))

    def test_symlink_context_marker_is_rejected(self):
        marker = self.install / ".ternela-context"
        marker.symlink_to(self.install / ".ternela-engine")
        result = self.run_script("start", "--dir", str(self.install))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_failed_resume_after_backup_is_reported(self):
        self.env.update(
            TEST_VOLUME_OWNER="0123456789abcdef", TEST_RUNNING="1", TEST_START_FAILURE="1"
        )
        result = self.run_script("backup", "--dir", str(self.install))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list((self.install / "backups").glob("*.tar.gz"))), 1)

    def test_foreign_volume_blocks_start_and_purge(self):
        self.env["TEST_VOLUME_OWNER"] = "someone-else"
        for action in (("start",), ("remove", "--purge")):
            result = self.run_script(*action, "--dir", str(self.install))
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(self.install.exists())
        calls = [json.loads(line)["args"] for line in self.log.read_text().splitlines()]
        self.assertFalse(any("up" in args or "down" in args or "rm" in args for args in calls))

    def test_native_script_refuses_unmigrated_folder(self):
        (self.install / ".ternela-storage").unlink()
        result = self.run_script("start", "--dir", str(self.install))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("migration", result.stderr)

    def test_different_engine_cannot_purge_the_local_folder(self):
        self.env["TEST_ENGINE"] = "engine-b"
        result = self.run_script("remove", "--purge", "--dir", str(self.install))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.install.exists())
        calls = [json.loads(line)["args"] for line in self.log.read_text().splitlines()]
        self.assertFalse(any("down" in args for args in calls))

    def test_trailing_separator_cannot_hide_an_install_symlink(self):
        alias = self.root / "alias"
        alias.symlink_to(self.install, target_is_directory=True)
        result = self.run_script("remove", "--purge", "--dir", str(alias) + "/")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.install.exists())

    def test_foreign_folder_without_marker_is_retained(self):
        (self.install / ".ternela-install").unlink()
        result = self.run_script("remove", "--purge", "--dir", str(self.install))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.install.exists())


if __name__ == "__main__":
    unittest.main()
