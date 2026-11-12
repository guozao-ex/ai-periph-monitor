"""Lean entry tests: child-role routing, the selftest report and the window title."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import lite_main

APP = Path(__file__).resolve().parent
# Modules that spawn sibling processes with the same executable. my_devices.py and
# app.py also call build_subcommand("--debugger"), but neither is bundled in the
# lean build, so they are not part of this contract.
SPAWNERS = ('tray_common.py', 'tray_app.py', 'desktop_widget.py', 'win_toast.py')


def run_entry(*args, timeout=180):
    # The child prints through the console code page unless told otherwise.
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    return subprocess.run([sys.executable, str(APP/'lite_main.py'), *args],
                          cwd=str(APP), capture_output=True, text=True, env=env,
                          encoding='utf-8', errors='replace', timeout=timeout)


class RoutingTest(unittest.TestCase):
    def test_child_argv_drops_only_its_own_flag(self):
        argv = ['lite.exe', '--tray', '--debug', '--widget', '--ui', '--toast']
        self.assertEqual(lite_main.child_argv(argv, '--tray'),
                         ['lite.exe', '--debug', '--widget', '--ui', '--toast'])
        self.assertEqual(lite_main.child_argv(argv, '--toast'),
                         ['lite.exe', '--tray', '--debug', '--widget', '--ui'])

    def test_child_argv_keeps_flag_like_values(self):
        """A message that happens to read "--ui" is a value, not a route flag."""
        argv = ['lite.exe', '--toast', '--message', '--ui']
        self.assertEqual(lite_main.child_argv(argv, '--toast'),
                         ['lite.exe', '--message', '--ui'])

    def test_tray_status_survives_routing(self):
        """The frozen build runs [exe, --tray]; argparse must not reject the flag."""
        done = run_entry('--tray', '--status')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('托盘是否在运行', done.stdout)
        self.assertNotIn('unrecognized arguments', done.stderr)

    def test_every_spawned_flag_is_routed(self):
        """A new child flag in a bundled spawner must be dispatched here."""
        pattern = re.compile(r'(?:build_subcommand\(|\[sys\.executable,\s*)["\'](--[a-z]+)["\']')
        flags = set()
        for name in SPAWNERS:
            flags |= set(pattern.findall((APP/name).read_text(encoding='utf-8')))
        self.assertTrue(flags, 'no spawn sites found - did the spawners change?')
        self.assertEqual(flags - set(lite_main.ROUTE_FLAGS), set())

    def test_activate_existing_ignores_unknown_title(self):
        self.assertFalse(lite_main.activate_existing('精简版测试-不存在的窗口标题'))


class TrayIntegrationTest(unittest.TestCase):
    """The tray must find the lean window, not only the full edition's."""

    def test_ui_titles_cover_both_editions(self):
        import tray_common as tc
        self.assertIn(tc.UI_TITLE, tc.UI_TITLES)
        self.assertIn(lite_main.WINDOW_TITLE, tc.UI_TITLES)

    def test_full_ui_lookup_ignores_the_lean_window(self):
        import tray_common as tc
        # The launcher owns the full edition only; a lean window must not stand in
        # for it, or double clicking the full exe would focus the wrong window.
        self.assertEqual(tc.full_ui_hwnd(), tc._find_window(tc.UI_TITLE))

    def test_focus_window_rejects_zero(self):
        import tray_common as tc
        self.assertFalse(tc.focus_window(0))


class SelftestReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='lite_selftest_')
        cls.report_path = Path(cls.tmp.name)/'report.json'
        cls.done = run_entry('--selftest', str(cls.report_path))
        cls.report = json.loads(cls.report_path.read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exit_code_and_shape(self):
        self.assertEqual(self.done.returncode, 0, self.done.stderr)
        for key in ('frozen', 'python', 'tk', 'local_modules', 'unexpected_modules',
                    'missing_modules', 'heavy_modules', 'heavy_modules_after_window',
                    'window_built', 'window_title', 'dpi_awareness', 'tk_scaling',
                    'tray_font', 'child_roles', 'ok'):
            self.assertIn(key, self.report)
        self.assertTrue(self.report['ok'], self.report)

    def test_startup_footprint_is_measured_not_whitelisted(self):
        self.assertEqual(self.report['missing_modules'], [])
        self.assertEqual(self.report['unexpected_modules'], [])
        self.assertEqual(self.report['heavy_modules'], [])
        self.assertEqual(self.report['heavy_modules_after_window'], [])

    def test_window_builds_with_the_shared_title(self):
        self.assertTrue(self.report['window_built'], self.report.get('window_error'))
        self.assertEqual(self.report['window_title'], lite_main.WINDOW_TITLE)

    def test_process_is_dpi_aware(self):
        """0 = unaware: Windows bitmap-stretches the window on a scaled display."""
        self.assertNotEqual(self.report['dpi_awareness'], 0)
        self.assertGreaterEqual(self.report['tk_scaling'], 1.0)

    def test_child_roles_importable(self):
        self.assertEqual(self.report['child_roles'],
                         {'tray_app': True, 'desktop_widget': True, 'win_toast': True})

    def test_selftest_ignores_route_flags_as_path(self):
        """`--selftest --ui` must not treat the flag as a report path."""
        done = run_entry('--selftest', '--ui')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('"ok": true', done.stdout)
        self.assertFalse((APP/'--ui').exists())

    def test_bundled_font_renders(self):
        self.assertTrue(self.report['tray_font'])


if __name__ == '__main__':
    unittest.main()
