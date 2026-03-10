#!/usr/bin/env python3
"""
可重复执行的 git_branch_manager.py 端到端验证脚本。
"""

from __future__ import annotations

import builtins
import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_REPO = ROOT / ".tmp_branch_manager_validation"
TEST_DATE = "20260310"

FEATURE_1 = f"feature_test1_{TEST_DATE}"
FEATURE_2 = f"feature_test2_{TEST_DATE}"
DEV_350 = f"dev_3.5.0_{TEST_DATE}"
DEV_351 = f"dev_3.5.1_{TEST_DATE}"

sys.path.insert(0, str(ROOT))

import git_branch_manager as bm


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=TEST_REPO,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 执行失败\n标准输出:\n{result.stdout}\n标准错误:\n{result.stderr}"
        )
    return result.stdout.strip()


def write_readme(text: str) -> None:
    (TEST_REPO / "README.md").write_text(text, encoding="utf-8")


@contextlib.contextmanager
def pushd(path: Path):
    previous = Path.cwd()
    try:
        path.mkdir(parents=True, exist_ok=True)
        os_chdir(path)
        yield
    finally:
        os_chdir(previous)


def os_chdir(path: Path) -> None:
    import os

    os.chdir(path)


@contextlib.contextmanager
def scripted_input(steps):
    original_input = builtins.input
    iterator = iter(steps)

    def fake_input(prompt: str = "") -> str:
        print(prompt, end="")
        while True:
            try:
                item = next(iterator)
            except StopIteration as exc:
                raise AssertionError(f"出现了未预期的输入提示: {prompt}") from exc
            if callable(item):
                item(prompt)
                continue
            return str(item)

    builtins.input = fake_input
    try:
        yield
    finally:
        builtins.input = original_input


def run_flow(fn, steps) -> tuple[object, str]:
    buffer = io.StringIO()
    with pushd(TEST_REPO), scripted_input(steps), contextlib.redirect_stdout(buffer):
        result = fn()
    return result, buffer.getvalue()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def log(message: str, verbose: bool) -> None:
    if verbose:
        print(message)


def tracking_subjects(branch_name: str) -> list[str]:
    log = git(
        "log",
        "--all",
        "-F",
        f"--grep={bm.MERGE_TAG} {branch_name} <-",
        "--pretty=format:%s",
    )
    return [line for line in log.splitlines() if line.strip()]


def latest_tracking_subject(branch_name: str) -> str:
    subjects = tracking_subjects(branch_name)
    assert_true(bool(subjects), f"未找到 {branch_name} 的追踪提交")
    return subjects[0]


def local_branches() -> set[str]:
    return set(git("branch", "--format=%(refname:short)").splitlines())


def setup_repo() -> None:
    shutil.rmtree(TEST_REPO, ignore_errors=True)
    TEST_REPO.mkdir(parents=True, exist_ok=True)

    git("init", "-b", "master")
    git("config", "user.name", "Codex Test")
    git("config", "user.email", "codex-test@example.com")
    git("config", "rerere.enabled", "true")

    write_readme("初始内容\n")
    git("add", "README.md")
    git("commit", "-m", "初始化仓库")


def create_feature_branches() -> None:
    _, output = run_flow(bm.create_feature_branch, ["1", "test1"])
    assert_true(FEATURE_1 in output, "未成功创建 feature_test1 分支")

    _, output = run_flow(bm.create_feature_branch, ["1", "test2"])
    assert_true(FEATURE_2 in output, "未成功创建 feature_test2 分支")

    branches = local_branches()
    assert_true(FEATURE_1 in branches, f"缺少分支 {FEATURE_1}")
    assert_true(FEATURE_2 in branches, f"缺少分支 {FEATURE_2}")


def create_initial_integration() -> None:
    _, output = run_flow(bm.create_integration_branch, ["1", "3.5.0", "all", "y"])
    assert_true(DEV_350 in output, "未成功创建初始集成分支")
    assert_true(DEV_350 in local_branches(), f"缺少分支 {DEV_350}")


def create_conflicting_commits() -> None:
    git("checkout", FEATURE_1)
    write_readme("feature test1 修改\n")
    git("commit", "-am", "feature_test1 修改 README")

    git("checkout", FEATURE_2)
    write_readme("feature test2 冲突修改\n")
    git("commit", "-am", "feature_test2 以不同方式修改 README")


def update_and_abort_conflict() -> None:
    _, output = run_flow(bm.update_integration_branch, ["1", "y", "2"])
    assert_true("已同步 (1): feature_test1_20260310" in output, "feature_test1 更新未成功")
    assert_true("失败   (1): feature_test2_20260310" in output, "未正确报告 feature_test2 的放弃合并结果")
    assert_true(
        latest_tracking_subject(DEV_350) == f"{bm.MERGE_TAG} {DEV_350} <- {FEATURE_1}",
        "追踪提交应该只记录成功更新的分支",
    )


def resolve_current_conflict(_prompt: str) -> None:
    write_readme("feature test1 修改\nfeature test2 冲突修改\n")
    git("add", "README.md")


def update_and_resolve_conflict() -> None:
    _, output = run_flow(bm.update_integration_branch, ["1", "y", resolve_current_conflict, "1"])
    assert_true("冲突已解决，合并完成" in output, "手动解决冲突路径未完成")
    assert_true("无变更 (1): feature_test1_20260310" in output, "feature_test1 本应被跳过")
    assert_true(
        latest_tracking_subject(DEV_350) == f"{bm.MERGE_TAG} {DEV_350} <- {FEATURE_2}",
        "手动解决后，最新追踪提交应该只记录 feature_test2",
    )
    git("checkout", DEV_350)
    assert_true(
        (TEST_REPO / "README.md").read_text(encoding="utf-8")
        == "feature test1 修改\nfeature test2 冲突修改\n",
        "解决冲突后的 README 内容不正确",
    )


def rerere_replay() -> None:
    _, output = run_flow(bm.create_integration_branch, ["1", "3.5.1", "all", "y"])
    assert_true(
        "rerere 自动重用了历史解决方案" in output,
        "未输出 rerere 自动复用提示",
    )
    git("checkout", DEV_351)
    assert_true(
        (TEST_REPO / "README.md").read_text(encoding="utf-8")
        == "feature test1 修改\nfeature test2 冲突修改\n",
        "rerere 重放后未恢复预期的 README 内容",
    )
    assert_true(
        latest_tracking_subject(DEV_351)
        == f"{bm.MERGE_TAG} {DEV_351} <- {FEATURE_1},{FEATURE_2}",
        "rerere 重放后，追踪提交应记录两个分支",
    )


def run_validation(verbose: bool = True) -> Path:
    original_today_str = bm.today_str
    bm.today_str = lambda: TEST_DATE
    try:
        log(f"[1/6] 准备临时测试仓库: {TEST_REPO}", verbose)
        setup_repo()

        log("[2/6] 创建开发分支", verbose)
        create_feature_branches()

        log("[3/6] 创建初始集成分支", verbose)
        create_initial_integration()

        log("[4/6] 制造冲突提交并验证放弃合并路径", verbose)
        create_conflicting_commits()
        update_and_abort_conflict()

        log("[5/6] 手动解决冲突并验证更新路径", verbose)
        update_and_resolve_conflict()

        log("[6/6] 重放同一冲突并验证 rerere 自动解决", verbose)
        rerere_replay()

        log("\n验证通过。", verbose)
        log(f"临时测试仓库保留在: {TEST_REPO}", verbose)
        return TEST_REPO
    finally:
        bm.today_str = original_today_str


def main() -> int:
    run_validation(verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
