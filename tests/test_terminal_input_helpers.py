#!/usr/bin/env python3
"""
终端输入辅助函数测试。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dreo_branch_manager as bm


class TerminalInputHelpersTest(unittest.TestCase):
    def test_display_width_counts_chinese_as_double_width(self) -> None:
        self.assertEqual(bm.display_width("test"), 4)
        self.assertEqual(bm.display_width("中文"), 4)
        self.assertEqual(bm.display_width("a中b"), 4)

    def test_wrapped_line_count_expands_for_wide_characters(self) -> None:
        with mock.patch("dreo_branch_manager.shutil.get_terminal_size", return_value=mock.Mock(columns=20)):
            self.assertEqual(bm.wrapped_line_count("  > test"), 1)
            self.assertEqual(bm.wrapped_line_count("  > 中文中文中文中文中文"), 2)


if __name__ == "__main__":
    unittest.main()
