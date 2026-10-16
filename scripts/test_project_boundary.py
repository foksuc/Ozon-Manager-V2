"""Fresh fail-closed runner checks, stdlib only; never starts a child tool."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import project


class BoundaryTests(unittest.TestCase):
    def test_root_is_script_relative(self):
        self.assertEqual(project.ROOT, Path(project.__file__).absolute().parent.parent)

    def test_runtime_discovery_ignores_path(self):
        local = project.ROOT / '.cache/runtimes/node/node.exe'
        fallback = Path(r'C:\Program Files\nodejs\node.exe')
        with patch.dict(os.environ, {'PATH': r'C:\untrusted', 'NODE': r'C:\untrusted\node.exe'}):
            with patch.object(Path, 'is_file', return_value=True):
                self.assertEqual(project.installed_tool(local, fallback), local)
            with patch.object(Path, 'is_file', return_value=False):
                self.assertEqual(project.installed_tool(local, fallback), fallback)

    def test_external_path_refused(self):
        with self.assertRaises(ValueError):
            project.confined(Path(r"E:\Project\Ozon Manager\cache"))

    def test_dotdot_refused(self):
        with self.assertRaises(ValueError):
            project.confined(project.ROOT / ".." / "Ozon Manager" / "data")

    def test_override_no_process_no_write(self):
        for verb, args in (("install-node", ["--cache=C:\\outside"]), ("arbitrary", [])):
            with patch.object(project.subprocess, "run") as child, patch.object(project, "prepare") as writes:
                with self.assertRaises(ValueError):
                    project.run(verb, args)
                child.assert_not_called()
                writes.assert_not_called()

    def test_wrong_cwd_no_process_no_write(self):
        with patch.object(Path, "cwd", return_value=Path(r"E:\Project\Ozon Manager")), patch.object(project.subprocess, "run") as child, patch.object(project, "prepare") as writes:
            with self.assertRaises(ValueError):
                project.run("install-node", [])
            child.assert_not_called()
            writes.assert_not_called()

    def test_link_no_process_no_write(self):
        for kind in ("is_junction", "is_symlink"):
            with patch.object(Path, kind, return_value=True), patch.object(project.subprocess, "run") as child, patch.object(project, "prepare") as writes:
                with self.assertRaises(ValueError):
                    project.run("setup-python", [])
                child.assert_not_called()
                writes.assert_not_called()

    def test_nested_planted_link_refused(self):
        original = Path.lstat
        class Reparse:
            st_mode = 0
            st_file_attributes = 0x400
        def metadata(path):
            return Reparse() if path.name == "escape" else original(path)
        with patch.object(project.os, "walk", return_value=[(str(project.ROOT / ".cache"), ["escape"], [])]), patch.object(Path, "lstat", autospec=True, side_effect=metadata):
            with self.assertRaises(ValueError):
                project.preflight("install-node", [])

    def test_denied_scan_no_process_no_write(self):
        def denied_walk(path, **options):
            options["onerror"](PermissionError("test scan denied"))
        with patch.object(project.os, "walk", side_effect=denied_walk), patch.object(project.subprocess, "run") as child, patch.object(project, "prepare") as writes:
            with self.assertRaises(PermissionError):
                project.run("install-node", [])
            child.assert_not_called()
            writes.assert_not_called()

    def test_windows_env_case_insensitive(self):
        env = project.environment({"SystemRoot": r"C:\Windows", "ComSpec": r"C:\Windows\system32\cmd.exe"})
        self.assertEqual(env["SYSTEMROOT"], r"C:\Windows")
        self.assertIn("COMSPEC", env)

    def test_project_npmrc_no_process_no_write(self):
        original = Path.exists
        with patch.object(Path, "exists", autospec=True, side_effect=lambda p: True if p.name == ".npmrc" else original(p)), patch.object(project.subprocess, "run") as child, patch.object(project, "prepare") as writes:
            with self.assertRaises(ValueError):
                project.run("install-node", [])
            child.assert_not_called()
            writes.assert_not_called()

    def test_poisoned_env_cannot_override(self):
        env = project.environment({"SystemRoot": r"C:\Windows", "NODE_OPTIONS": "--require C:\\evil.js", "PYTHONPATH": "C:\\evil", "OZON_API_KEY": "test-secret", "npm_config_cache": "C:\\escape", "PIP_TARGET": "C:\\escape", "TEMP": "C:\\escape", "HOME": "C:\\escape"})
        for key in ("NODE_OPTIONS", "PYTHONPATH", "OZON_API_KEY", "PIP_TARGET", "HOME"):
            self.assertNotIn(key, env)
        for key in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PIP_CACHE_DIR", "npm_config_cache", "npm_config_userconfig", "npm_config_globalconfig", "npm_config_prefix", "PLAYWRIGHT_BROWSERS_PATH"):
            self.assertTrue(Path(env[key]).is_relative_to(project.ROOT), key)

    def test_closed_commands_not_shell(self):
        for verb in project.VERBS:
            for argv, cwd in project.commands(verb):
                self.assertIsInstance(argv, list)
                self.assertTrue(cwd.is_relative_to(project.ROOT))
        argv = project.commands("install-node")[0][0]
        self.assertIn("--ignore-scripts", argv)
        argv = project.commands("install-python")[0][0]
        self.assertIn("--only-binary=:all:", argv)

    def test_live_mode_only_closed_verb_no_key_env(self):
        with patch.object(project, "preflight", return_value={}), patch.object(project, "prepare"), patch.object(project.subprocess, "run") as child:
            child.return_value.returncode = 0
            with patch.dict(project.os.environ, {"OZON_V2_MODE": "LIVE_READ", "OZON_API_KEY": "SYNTHETIC-OVERRIDE"}):
                project.run("start", [])
                self.assertEqual(child.call_args.kwargs["env"]["OZON_V2_MODE"], "MOCK")
                self.assertNotIn("OZON_API_KEY", child.call_args.kwargs["env"])
                project.run("start-read", [])
                self.assertEqual(child.call_args.kwargs["env"]["OZON_V2_MODE"], "LIVE_READ")
                self.assertNotIn("OZON_API_KEY", child.call_args.kwargs["env"])
                self.assertIn("--no-access-log", child.call_args.args[0])

    def test_preview_verification_is_fixed_and_has_no_overrides(self):
        argv, cwd = project.commands("verify-preview")[0]
        self.assertEqual(argv[-1], str(project.ROOT / "scripts/verify_preview.py"))
        self.assertEqual(cwd, project.ROOT)
        with self.assertRaises(ValueError):
            project.preflight("verify-preview", ["--output=C:\\outside"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
