#!/usr/bin/env python3
"""
远端分支支持测试。
"""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dreo_branch_manager as bm


TEST_DATE = "20260319"


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


@contextlib.contextmanager
def pushd(path: Path):
    previous = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


@contextlib.contextmanager
def scripted_input(steps):
    original_input = builtins.input
    iterator = iter(steps)

    def fake_input(prompt: str = "") -> str:
        print(prompt, end="")
        item = next(iterator)
        return str(item)

    builtins.input = fake_input
    try:
        yield
    finally:
        builtins.input = original_input


def run_flow(repo: Path, fn, steps):
    buffer = io.StringIO()
    with pushd(repo), scripted_input(steps), contextlib.redirect_stdout(buffer):
        result = fn()
    return result, buffer.getvalue()


class RemoteBranchSupportTest(unittest.TestCase):
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

        self.original_today = bm.today_str
        bm.today_str = lambda: TEST_DATE

    def tearDown(self) -> None:
        bm.today_str = self.original_today
        self.tempdir.cleanup()

    def local_branches(self) -> set[str]:
        return set(git(self.repo, "branch", "--format=%(refname:short)").splitlines())

    def create_remote_feature(self, name: str, message: str) -> None:
        git(self.repo, "checkout", "master")
        git(self.repo, "checkout", "-b", name)
        (self.repo / f"{name}.txt").write_text(message + "\n", encoding="utf-8")
        git(self.repo, "add", f"{name}.txt")
        git(self.repo, "commit", "-m", message)
        git(self.repo, "push", "-u", "origin", name)
        git(self.repo, "checkout", "master")
        git(self.repo, "branch", "-D", name)

    def create_remote_integration(self, name: str) -> None:
        git(self.repo, "checkout", "master")
        git(self.repo, "checkout", "-b", name)
        git(self.repo, "push", "-u", "origin", name)
        git(self.repo, "checkout", "master")
        git(self.repo, "branch", "-D", name)

    def test_create_feature_branch_restores_remote_only_branch(self) -> None:
        branch = f"feature_test_{TEST_DATE}"
        self.create_remote_feature(branch, "remote feature commit")

        _, output = run_flow(self.repo, bm.create_feature_branch, ["1", "test"])

        self.assertIn("检测到同名远端分支", output)
        self.assertIn(branch, self.local_branches())

    def test_create_integration_branch_can_merge_remote_only_feature(self) -> None:
        feature = f"feature_alpha_{TEST_DATE}"
        integration = f"dev_1.0.0_{TEST_DATE}"
        self.create_remote_feature(feature, "alpha commit")
        self.create_remote_integration(integration)

        _, output = run_flow(
            self.repo,
            bm.create_integration_branch,
            ["1", "1.0.0", "1", "y", "n"],
        )

        self.assertIn("检测到同名远端集成分支", output)
        self.assertIn(feature, self.local_branches())
        self.assertIn(integration, self.local_branches())

    def test_update_integration_branch_restores_remote_only_branches(self) -> None:
        feature = f"feature_sync_{TEST_DATE}"
        integration = f"dev_2.0.0_{TEST_DATE}"

        git(self.repo, "checkout", "master")
        git(self.repo, "checkout", "-b", feature)
        (self.repo / "sync.txt").write_text("v1\n", encoding="utf-8")
        git(self.repo, "add", "sync.txt")
        git(self.repo, "commit", "-m", "feature v1")
        git(self.repo, "push", "-u", "origin", feature)

        git(self.repo, "checkout", "master")
        git(self.repo, "checkout", "-b", integration)
        git(self.repo, "merge", "--no-ff", feature, "-m", f"Merge branch '{feature}' into {integration}")
        git(self.repo, "commit", "--allow-empty", "-m", f"{bm.MERGE_TAG} {integration} <- {feature}")
        git(self.repo, "push", "-u", "origin", integration)

        git(self.repo, "checkout", feature)
        (self.repo / "sync.txt").write_text("v2\n", encoding="utf-8")
        git(self.repo, "commit", "-am", "feature v2")
        git(self.repo, "push", "origin", feature)

        git(self.repo, "checkout", "master")
        git(self.repo, "branch", "-D", feature)
        git(self.repo, "branch", "-D", integration)

        _, output = run_flow(
            self.repo,
            bm.update_integration_branch,
            ["1", "y", "n"],
        )

        self.assertIn(feature, self.local_branches())
        self.assertIn(integration, self.local_branches())
        self.assertIn("已同步 (1)", output)

    def test_pull_remote_branch_to_local(self) -> None:
        branch = f"feature_pull_{TEST_DATE}"
        self.create_remote_feature(branch, "pull me")

        _, output = run_flow(
            self.repo,
            bm.pull_remote_branch_to_local,
            ["1", "y"],
        )

        self.assertIn(branch, self.local_branches())
        self.assertIn(f"已将远端分支拉取到本地并切换到: {branch}", output)

    def test_delete_remote_only_branch(self) -> None:
        branch = f"feature_delete_{TEST_DATE}"
        self.create_remote_feature(branch, "delete me")

        _, output = run_flow(
            self.repo,
            lambda: bm.delete_branches(include_remote=True),
            ["1", "y"],
        )

        remote_refs = git(self.repo, "branch", "-r", "--format=%(refname:short)")
        self.assertNotIn(f"origin/{branch}", remote_refs.splitlines())
        self.assertIn("远端已删除", output)

    def test_show_status_includes_local_and_remote_counts(self) -> None:
        local_feature = f"feature_local_{TEST_DATE}"
        remote_feature = f"feature_remote_{TEST_DATE}"
        local_integration = f"dev_1.0.0_{TEST_DATE}"
        remote_integration = f"dev_2.0.0_{TEST_DATE}"

        git(self.repo, "checkout", "-b", local_feature)
        git(self.repo, "checkout", "master")
        git(self.repo, "checkout", "-b", local_integration)
        git(self.repo, "checkout", "master")

        self.create_remote_feature(remote_feature, "remote only feature")
        self.create_remote_integration(remote_integration)

        _, output = run_flow(self.repo, bm.show_status, [])

        self.assertIn("开发分支: 2（本地：1，远端：1）", output)
        self.assertIn("集成分支: 2（本地：1，远端：1）", output)

    def test_branch_lists_filter_invalid_remote_names(self) -> None:
        valid_feature = f"feature_valid_{TEST_DATE}"
        valid_integration = f"dev_1.0.0_{TEST_DATE}"

        self.create_remote_feature(valid_feature, "valid feature")
        self.create_remote_integration(valid_integration)
        self.create_remote_integration("dev_automate")
        self.create_remote_integration("release_3.1.0_hec")

        with pushd(self.repo):
            bm.refresh_remote_refs()
            feature_branches = bm.get_feature_branches()
            integration_branches = bm.get_integration_branches()

        self.assertIn(valid_feature, feature_branches)
        self.assertIn(valid_integration, integration_branches)
        self.assertNotIn("dev_automate", integration_branches)
        self.assertNotIn("release_3.1.0_hec", integration_branches)

    def test_pull_remote_branch_to_local_supports_paging(self) -> None:
        branches = [f"feature_page_{i:02d}_202603{i:02d}" for i in range(1, 22)]
        for branch in branches:
            self.create_remote_feature(branch, f"create {branch}")

        target_branch = branches[0]
        _, output = run_flow(
            self.repo,
            bm.pull_remote_branch_to_local,
            ["__DOWN__", "21", "y"],
        )

        self.assertIn(target_branch, self.local_branches())

    def test_merge_master_to_current_branch(self) -> None:
        feature = f"feature_merge_base_{TEST_DATE}"

        git(self.repo, "checkout", "-b", feature)
        (self.repo / "feature.txt").write_text("feature work\n", encoding="utf-8")
        git(self.repo, "add", "feature.txt")
        git(self.repo, "commit", "-m", "feature work")

        git(self.repo, "checkout", "master")
        (self.repo / "master.txt").write_text("master update\n", encoding="utf-8")
        git(self.repo, "add", "master.txt")
        git(self.repo, "commit", "-m", "master update")

        git(self.repo, "checkout", feature)

        _, output = run_flow(
            self.repo,
            bm.merge_master_to_current,
            ["y", "n"],
        )

        merge_result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "master", feature],
            cwd=self.repo,
            text=True,
            capture_output=True,
        )

        self.assertEqual(merge_result.returncode, 0)
        self.assertIn("合并主干代码成功: master", output)

    def test_delete_branches_supports_paging(self) -> None:
        branches = [f"feature_delete_page_{i:02d}_202603{i:02d}" for i in range(1, 22)]
        for branch in branches:
            git(self.repo, "checkout", "master")
            git(self.repo, "checkout", "-b", branch)
            (self.repo / f"{branch}.txt").write_text("x\n", encoding="utf-8")
            git(self.repo, "add", f"{branch}.txt")
            git(self.repo, "commit", "-m", f"commit {branch}")

        git(self.repo, "checkout", "master")
        ordered = bm.sort_branches_by_date(branches, limit=len(branches))
        target_branch = ordered[20]

        _, output = run_flow(
            self.repo,
            lambda: bm.delete_branches(include_remote=False),
            ["__DOWN__", "21", "y", "y"],
        )

        self.assertNotIn(target_branch, self.local_branches())
        self.assertIn("当前第 1/2 页", output)

    def test_delete_branches_auto_confirms_single_selection(self) -> None:
        branches = [f"feature_delete_auto_{i:02d}_202603{i:02d}" for i in range(1, 22)]
        for branch in branches:
            git(self.repo, "checkout", "master")
            git(self.repo, "checkout", "-b", branch)
            (self.repo / f"{branch}.txt").write_text("x\n", encoding="utf-8")
            git(self.repo, "add", f"{branch}.txt")
            git(self.repo, "commit", "-m", f"commit {branch}")

        git(self.repo, "checkout", "master")
        ordered = bm.sort_branches_by_date(branches, limit=len(branches))
        target_branch = ordered[9]

        _, output = run_flow(
            self.repo,
            lambda: bm.delete_branches(include_remote=False),
            ["10", "y", "y"],
        )

        self.assertNotIn(target_branch, self.local_branches())
        self.assertIn("将删除以下 1 个分支", output)


if __name__ == "__main__":
    unittest.main()
