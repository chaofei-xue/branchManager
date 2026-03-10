#!/usr/bin/env python3
"""
dreo_branch_manager.py 的正式端到端测试。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_branch_manager import TEST_REPO, run_validation


class BranchManagerE2ETest(unittest.TestCase):
    def test_end_to_end_validation(self) -> None:
        repo = run_validation(verbose=False)

        self.assertEqual(repo, TEST_REPO)
        self.assertTrue((repo / ".git").exists(), "临时测试仓库未创建成功")
        self.assertTrue((repo / "README.md").exists(), "临时测试文件 README.md 不存在")


if __name__ == "__main__":
    unittest.main()
