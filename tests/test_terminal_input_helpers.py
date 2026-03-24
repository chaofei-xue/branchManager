#!/usr/bin/env python3
"""
终端输入辅助函数测试。
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dreo_branch_manager as bm


class FakeTTYIn:
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 0


class FakeTTYOut(io.StringIO):
    def isatty(self) -> bool:
        return True


class TerminalInputHelpersTest(unittest.TestCase):
    def test_display_width_counts_chinese_as_double_width(self) -> None:
        self.assertEqual(bm.display_width("test"), 4)
        self.assertEqual(bm.display_width("中文"), 4)
        self.assertEqual(bm.display_width("a中b"), 4)

    def test_display_width_ignores_ansi_sequences(self) -> None:
        self.assertEqual(bm.display_width("\033[4;34mtest\033[0m"), 4)
        self.assertEqual(bm.display_width("\033[32m中文\033[0m"), 4)

    def test_wrapped_line_count_expands_for_wide_characters(self) -> None:
        with mock.patch("dreo_branch_manager.shutil.get_terminal_size", return_value=mock.Mock(columns=20)):
            self.assertEqual(bm.wrapped_line_count("  > test"), 1)
            self.assertEqual(bm.wrapped_line_count("  > 中文中文中文中文中文"), 2)

    def test_read_menu_input_recognizes_arrow_up(self) -> None:
        fake_in = FakeTTYIn()
        fake_out = FakeTTYOut()

        with mock.patch.object(bm.sys, "stdin", fake_in), \
             mock.patch.object(bm.sys, "stdout", fake_out), \
             mock.patch("dreo_branch_manager.termios.tcgetattr", return_value=["settings"]), \
             mock.patch("dreo_branch_manager.termios.tcsetattr"), \
             mock.patch("dreo_branch_manager.tty.setraw"), \
             mock.patch("dreo_branch_manager.os.read", side_effect=[b"\x1b", b"[", b"A"]), \
             mock.patch("select.select", side_effect=[([0], [], []), ([0], [], [])]):
            result = bm.read_menu_input()

        self.assertEqual(result, "__UP__")

    def test_read_text_input_ignores_escape_sequences(self) -> None:
        fake_in = FakeTTYIn()
        fake_out = FakeTTYOut()

        with mock.patch.object(bm.sys, "stdin", fake_in), \
             mock.patch.object(bm.sys, "stdout", fake_out), \
             mock.patch("dreo_branch_manager.termios.tcgetattr", return_value=["settings"]), \
             mock.patch("dreo_branch_manager.termios.tcsetattr"), \
             mock.patch("dreo_branch_manager.tty.setraw"), \
             mock.patch(
                 "dreo_branch_manager.os.read",
                 side_effect=[b"a", b"\x1b", b"[", b"A", b"b", b"\r"],
             ), \
             mock.patch(
                 "select.select",
                 side_effect=[([0], [], []), ([0], [], []), ([], [], [])],
             ):
            result = bm.read_text_input("输入测试")

        self.assertEqual(result, "ab")

    def test_has_merge_conflict_detects_unmerged_files(self) -> None:
        with mock.patch("dreo_branch_manager.get_unmerged_files", return_value=["README.md"]):
            self.assertTrue(bm._has_merge_conflict("", ""))

    def test_get_merged_feature_branches_matches_exact_prefix(self) -> None:
        int_branch = "dev_1.0.0_20260319"
        other_branch = "dev_1.0.0_202603190"
        log = "\n".join([
            f"{bm.MERGE_TAG} {int_branch} <- feature_a_{bm.today_str()}",
            f"{bm.MERGE_TAG} {other_branch} <- feature_wrong_{bm.today_str()}",
            "noise line",
        ])
        with mock.patch("dreo_branch_manager.run_git", return_value=(True, log, "")):
            merged = bm.get_merged_feature_branches(int_branch)

        self.assertEqual(merged, [f"feature_a_{bm.today_str()}"])

    def test_check_rerere_auto_enables_without_confirmation(self) -> None:
        calls = []

        def fake_run_git(*args, **kwargs):
            calls.append(args)
            if args == ('config', '--local', 'rerere.enabled'):
                return True, 'false', ''
            if args == ('config', '--local', 'rerere.enabled', 'true'):
                return True, '', ''
            return True, '', ''

        with mock.patch("dreo_branch_manager.run_git", side_effect=fake_run_git), \
             mock.patch("dreo_branch_manager.confirm") as confirm_mock:
            bm.check_rerere()

        self.assertIn(('config', '--local', 'rerere.enabled', 'true'), calls)
        confirm_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
