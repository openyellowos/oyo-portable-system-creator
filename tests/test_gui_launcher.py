import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
LAUNCHER = REPOSITORY / "src/bin/oyo-portable-system-creator"


class GuiLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin_dir = self.root / "mock-bin"
        self.bin_dir.mkdir()
        self.python3 = shutil.which("python3")
        self.assertIsNotNone(self.python3)
        untrusted_module = self.root / "untrusted-pythonpath/src/gui"
        untrusted_module.mkdir(parents=True)
        (self.root / "untrusted-pythonpath/src/__init__.py").write_text("")
        (untrusted_module / "__init__.py").write_text("")
        (untrusted_module / "main_window.py").write_text(
            "def run_gui():\n    return 88\n"
        )
        self._write_executable(
            self.bin_dir / "id",
            '#!/bin/sh\n[ "$1" = "-u" ] || exit 2\nprintf "%s\\n" "${MOCK_UID:-1000}"\n',
        )
        self._write_executable(
            self.bin_dir / "pkexec",
            '#!/bin/sh\n: > "$PKEXEC_MARKER"\nexec "$@"\n',
        )
    def tearDown(self):
        self.temp.cleanup()

    def _write_executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def _make_app(self, app_root, marker, source_checkout=False):
        src = app_root / "src"
        (src / "gui").mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "gui/__init__.py").write_text("")
        (src / "gui/main_window.py").write_text(
            f"MARKER = {marker!r}\n"
            "def run_gui():\n"
            "    import inspect, json, os\n"
            "    import src.gui.main_window as module\n"
            "    print(json.dumps({\n"
            "        'module': inspect.getfile(module), 'marker': MARKER,\n"
            "        'pythonpath': os.getenv('PYTHONPATH'),\n"
            "        'display': os.getenv('DISPLAY'), 'xauthority': os.getenv('XAUTHORITY'),\n"
            "        'lang': os.getenv('LANG'), 'language': os.getenv('LANGUAGE'),\n"
            "        'lc_all': os.getenv('LC_ALL'), 'lc_messages': os.getenv('LC_MESSAGES'),\n"
            "        'pythonhome': os.getenv('PYTHONHOME'),\n"
            "        'no_user_site': os.getenv('PYTHONNOUSERSITE'),\n"
            "    }))\n"
            "    return int(os.getenv('GUI_EXIT_CODE', '0'))\n"
        )
        if source_checkout:
            (app_root / "debian").mkdir()
            (app_root / "debian/control").write_text("Source: oyo-portable-system-creator\n")
            (app_root / "debian/install").write_text("src usr/share/oyo-portable-system-creator/\n")

    def _make_source(self, name="checkout"):
        checkout = self.root / name
        app_root = checkout
        self._make_app(app_root, f"{name}-gui", source_checkout=True)
        script = checkout / "src/bin/oyo-portable-system-creator"
        script.parent.mkdir(parents=True)
        shutil.copy2(LAUNCHER, script)
        script.chmod(0o755)
        return checkout, script

    def _make_package(self, name="package"):
        prefix = self.root / name
        app_root = prefix / "usr/share/oyo-portable-system-creator"
        self._make_app(app_root, f"{name}-gui")
        script = prefix / "usr/bin/oyo-portable-system-creator"
        script.parent.mkdir(parents=True)
        shutil.copy2(LAUNCHER, script)
        script.chmod(0o755)
        return prefix, app_root, script

    def _run_launcher(self, script, cwd=None, script_arg=None, **extra_env):
        env = os.environ.copy()
        env.update(
            {
                "PATH": os.pathsep.join(
                    [str(self.bin_dir), str(Path(self.python3).parent), os.defpath]
                ),
                "MOCK_UID": "1000",
                "PKEXEC_MARKER": str(self.root / "pkexec-used"),
                "PYTHONPATH": str(self.root / "untrusted-pythonpath"),
                "PYTHONHOME": str(self.root / "untrusted-pythonhome"),
            }
        )
        env.update(extra_env)
        return subprocess.run(
            [script_arg or str(script)], cwd=cwd or self.root, env=env, text=True, capture_output=True
        )

    def _probe(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_source_checkout_imports_its_module_from_an_unrelated_cwd(self):
        checkout, script = self._make_source()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        shadow = elsewhere / "src/gui"
        shadow.mkdir(parents=True)
        (elsewhere / "src/__init__.py").write_text("")
        (shadow / "__init__.py").write_text("")
        (shadow / "main_window.py").write_text("def run_gui():\n    return 99\n")
        _, old_app, _ = self._make_package("old-installed-version")

        result = self._run_launcher(script, cwd=elsewhere)

        probe = self._probe(result)
        self.assertEqual(Path(probe["module"]).resolve(), (checkout / "src/gui/main_window.py").resolve())
        self.assertEqual(probe["pythonpath"], str(checkout))
        self.assertNotEqual(Path(probe["module"]).resolve(), (old_app / "src/gui/main_window.py").resolve())
        self.assertTrue((self.root / "pkexec-used").exists())

    def test_source_checkout_relative_command_uses_the_current_checkout(self):
        checkout, script = self._make_source()

        result = self._run_launcher(
            script,
            cwd=checkout,
            script_arg="./src/bin/oyo-portable-system-creator",
        )

        probe = self._probe(result)
        self.assertEqual(Path(probe["module"]).resolve(), (checkout / "src/gui/main_window.py").resolve())

    def test_root_source_path_overwrites_inherited_python_paths(self):
        checkout, script = self._make_source()

        result = self._run_launcher(
            script,
            MOCK_UID="0",
            DISPLAY=":71",
            XAUTHORITY="/tmp/test-xauthority",
            LANG="ja_JP.UTF-8",
            LANGUAGE="ja:en",
            LC_ALL="ja_JP.UTF-8",
            LC_MESSAGES="ja_JP.UTF-8",
        )

        probe = self._probe(result)
        self.assertEqual(Path(probe["module"]).resolve(), (checkout / "src/gui/main_window.py").resolve())
        self.assertEqual(probe["pythonpath"], str(checkout))
        self.assertIsNone(probe["pythonhome"])
        self.assertEqual(probe["no_user_site"], "1")
        self.assertEqual(probe["display"], ":71")
        self.assertEqual(probe["xauthority"], "/tmp/test-xauthority")
        self.assertEqual(probe["lang"], "ja_JP.UTF-8")
        self.assertEqual(probe["language"], "ja:en")
        self.assertEqual(probe["lc_all"], "ja_JP.UTF-8")
        self.assertEqual(probe["lc_messages"], "ja_JP.UTF-8")
        self.assertFalse((self.root / "pkexec-used").exists())

    def test_pkexec_receives_display_auth_locale_and_only_the_selected_import_root(self):
        checkout, script = self._make_source()

        result = self._run_launcher(
            script,
            DISPLAY=":72",
            XAUTHORITY="/tmp/explicit-xauthority",
            LANG="ja_JP.UTF-8",
            LANGUAGE="ja:en",
            LC_ALL="C.UTF-8",
            LC_MESSAGES="ja_JP.UTF-8",
        )

        probe = self._probe(result)
        self.assertEqual(Path(probe["module"]).resolve(), (checkout / "src/gui/main_window.py").resolve())
        self.assertEqual(probe["pythonpath"], str(checkout))
        self.assertIsNone(probe["pythonhome"])
        self.assertEqual(probe["no_user_site"], "1")
        self.assertEqual(probe["display"], ":72")
        self.assertEqual(probe["xauthority"], "/tmp/explicit-xauthority")
        self.assertEqual(probe["lang"], "ja_JP.UTF-8")
        self.assertEqual(probe["language"], "ja:en")
        self.assertEqual(probe["lc_all"], "C.UTF-8")
        self.assertEqual(probe["lc_messages"], "ja_JP.UTF-8")
        self.assertTrue((self.root / "pkexec-used").exists())

    def test_xauthority_keeps_the_previous_home_fallback(self):
        _, script = self._make_source()

        result = self._run_launcher(
            script, HOME="/tmp/nonroot-home", XAUTHORITY=""
        )

        self.assertEqual(self._probe(result)["xauthority"], "/tmp/nonroot-home/.Xauthority")

    def test_run_gui_exit_code_is_propagated_by_the_real_entrypoint(self):
        _, script = self._make_source()

        result = self._run_launcher(script, GUI_EXIT_CODE="37")

        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertTrue(result.stdout.strip())

    def test_package_launcher_prefers_installed_code_when_checkout_is_present(self):
        checkout, _ = self._make_source()
        _, app_root, script = self._make_package()

        result = self._run_launcher(script)

        probe = self._probe(result)
        self.assertEqual(Path(probe["module"]).resolve(), (app_root / "src/gui/main_window.py").resolve())
        self.assertEqual(probe["pythonpath"], str(app_root))
        self.assertNotEqual(Path(probe["module"]).resolve(), (checkout / "src/gui/main_window.py").resolve())

    def test_malformed_source_checkout_fails_instead_of_falling_back_to_old_package(self):
        checkout, script = self._make_source()
        (checkout / "src/gui/main_window.py").unlink()
        self._make_package("old-installed-version")

        result = self._run_launcher(script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a valid source checkout", result.stderr)
        self.assertFalse((self.root / "pkexec-used").exists())

    def test_package_missing_module_fails_even_when_source_checkout_exists(self):
        self._make_source()
        _, app_root, script = self._make_package()
        (app_root / "src/gui/main_window.py").unlink()

        result = self._run_launcher(script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("src.gui.main_window is missing", result.stderr)
        self.assertFalse((self.root / "pkexec-used").exists())

    def test_package_module_symlink_is_rejected(self):
        _, old_app, _ = self._make_package("old-installed-version")
        _, app_root, script = self._make_package()
        module = app_root / "src/gui/main_window.py"
        module.unlink()
        module.symlink_to(old_app / "src/gui/main_window.py")

        result = self._run_launcher(script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("src.gui.main_window is missing", result.stderr)
        self.assertFalse((self.root / "pkexec-used").exists())

    def test_source_root_without_checkout_markers_is_rejected(self):
        checkout, script = self._make_source()
        (checkout / "debian/install").unlink()
        self._make_package("old-installed-version")

        result = self._run_launcher(script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a valid source checkout", result.stderr)

    def test_launcher_in_unrecognized_location_fails_closed(self):
        _, _, script = self._make_package()
        misplaced = self.root / "misc/oyo-portable-system-creator"
        misplaced.parent.mkdir()
        shutil.copy2(script, misplaced)

        result = self._run_launcher(misplaced)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported launcher location", result.stderr)

    def test_symbolic_link_launcher_is_rejected(self):
        _, script = self._make_source()
        link = self.root / "linked-launcher"
        link.symlink_to(script)

        result = self._run_launcher(link)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to start through a symbolic link", result.stderr)


if __name__ == "__main__":
    unittest.main()
