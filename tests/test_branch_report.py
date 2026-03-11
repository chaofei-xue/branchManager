#!/usr/bin/env python3
"""
分支合并报告脚本测试。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dreo_branch_manager as bm


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 执行失败\n标准输出:\n{result.stdout}\n标准错误:\n{result.stderr}"
        )
    return result.stdout.strip()


class BranchReportTest(unittest.TestCase):
    def test_generate_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir(parents=True, exist_ok=True)

            git(repo, "init", "-b", "master")
            git(repo, "config", "user.name", "Codex Test")
            git(repo, "config", "user.email", "codex-test@example.com")

            (repo / "README.md").write_text("m1\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "m1")

            git(repo, "checkout", "-b", "feature_demo_20260311")
            (repo / "README.md").write_text("feature demo\n", encoding="utf-8")
            git(repo, "commit", "-am", "f1")

            git(repo, "checkout", "master")
            git(repo, "checkout", "-b", "dev_1.0.0_20260311")
            git(repo, "merge", "--no-ff", "feature_demo_20260311", "-m", "Merge branch 'feature_demo_20260311' into dev_1.0.0_20260311")
            git(repo, "commit", "--allow-empty", "-m", "[DREO-MERGE] dev_1.0.0_20260311 <- feature_demo_20260311")

            output = repo / "report.md"
            previous = Path.cwd()
            try:
                os.chdir(repo)
                bm.generate_branch_report(output)
            finally:
                os.chdir(previous)
            content = output.read_text(encoding="utf-8")

            self.assertIn("# Git 分支合并报告", content)
            self.assertIn("feature_demo_20260311", content)
            self.assertIn("dev_1.0.0_20260311", content)
            self.assertIn("```mermaid", content)
            self.assertIn("[DREO-MERGE] dev_1.0.0_20260311 <- feature_demo_20260311", content)


if __name__ == "__main__":
    unittest.main()
