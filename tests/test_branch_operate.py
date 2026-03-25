#!/usr/bin/env python3
"""
参数化操作入口测试。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATE = ROOT / "dreo_branch_operate.py"


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 执行失败\n标准输出:\n{result.stdout}\n标准错误:\n{result.stderr}"
        )
    return result.stdout.strip()


class BranchOperateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"

        git(self.root, "init", "--bare", str(self.remote))
        self.repo.mkdir(parents=True, exist_ok=True)
        git(self.repo, "init", "-b", "master")
        git(self.repo, "config", "user.name", "Codex Test")
        git(self.repo, "config", "user.email", "codex-test@example.com")
        git(self.repo, "remote", "add", "origin", str(self.remote))

        (self.repo / "README.md").write_text("init\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "init")
        git(self.repo, "push", "-u", "origin", "master")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_operate_updates_integration_branch_by_name(self) -> None:
        feature = "feature_demo_20260319"
        integration = "dev_3.6.0_20260319"

        git(self.repo, "checkout", "-b", feature)
        (self.repo / "demo.txt").write_text("v1\n", encoding="utf-8")
        git(self.repo, "add", "demo.txt")
        git(self.repo, "commit", "-m", "feature v1")
        git(self.repo, "push", "-u", "origin", feature)

        git(self.repo, "checkout", "master")
        git(self.repo, "checkout", "-b", integration)
        git(self.repo, "merge", "--no-ff", feature, "-m", f"Merge branch '{feature}' into {integration}")
        git(self.repo, "commit", "--allow-empty", "-m", f"[DREO-MERGE] {integration} <- {feature}")
        git(self.repo, "push", "-u", "origin", integration)

        git(self.repo, "checkout", feature)
        (self.repo / "demo.txt").write_text("v2\n", encoding="utf-8")
        git(self.repo, "commit", "-am", "feature v2")
        git(self.repo, "push", "origin", feature)

        git(self.repo, "checkout", "master")
        result = subprocess.run(
            [sys.executable, str(OPERATE), "2", "2", integration],
            cwd=self.repo,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)
        self.assertIn("已同步 (1)", result.stdout)
        self.assertIn("执行结果: 成功", result.stdout)
        self.assertTrue(result.stdout.strip().endswith("DREO_RESULT=SUCCESS"))

        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", feature, integration],
            cwd=self.repo,
            text=True,
            capture_output=True,
        )
        self.assertEqual(ancestor.returncode, 0)

    def test_operate_create_feature_fails_fast_when_local_branch_exists(self) -> None:
        branch = f"feature_test1_{date.today().strftime('%Y%m%d')}"
        git(self.repo, "checkout", "-b", branch)
        git(self.repo, "checkout", "master")

        result = subprocess.run(
            [sys.executable, str(OPERATE), "1", "feature", "test1", "master"],
            cwd=self.repo,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"分支 '{branch}' 已存在", result.stdout + result.stderr)
        self.assertIn("执行结果: 失败", result.stdout + result.stderr)
        self.assertTrue((result.stdout + result.stderr).strip().endswith("DREO_RESULT=FAILED"))

    def test_operate_fails_fast_when_merge_conflict_occurs(self) -> None:
        feature = "feature_conflict_20260319"
        integration = "dev_3.6.0_20260319"

        git(self.repo, "checkout", "-b", feature)
        (self.repo / "conflict.txt").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", "conflict.txt")
        git(self.repo, "commit", "-m", "feature v1")
        git(self.repo, "push", "-u", "origin", feature)

        git(self.repo, "checkout", "master")
        git(self.repo, "checkout", "-b", integration)
        git(self.repo, "merge", "--no-ff", feature, "-m", f"Merge branch '{feature}' into {integration}")
        git(self.repo, "commit", "--allow-empty", "-m", f"[DREO-MERGE] {integration} <- {feature}")
        (self.repo / "conflict.txt").write_text("integration edit\n", encoding="utf-8")
        git(self.repo, "commit", "-am", "integration edit")
        git(self.repo, "push", "-u", "origin", integration)

        git(self.repo, "checkout", feature)
        (self.repo / "conflict.txt").write_text("feature v2\n", encoding="utf-8")
        git(self.repo, "commit", "-am", "feature v2")
        git(self.repo, "push", "origin", feature)
        git(self.repo, "checkout", "master")

        result = subprocess.run(
            [sys.executable, str(OPERATE), "2", "2", integration],
            cwd=self.repo,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)
        self.assertIn("参数模式已自动终止并执行 git merge --abort", result.stdout)
        self.assertIn("执行结果: 失败", result.stdout)
        self.assertTrue(result.stdout.strip().endswith("DREO_RESULT=FAILED"))

        merge_head = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            cwd=self.repo,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(merge_head.returncode, 0)


if __name__ == "__main__":
    unittest.main()
