#!/usr/bin/env python3
"""
Dreo 分支管理工具的参数化入口。

示例：
  dreo_branch_operate 2 2 dev_3.6.0_20260319
  dreo_branch_operate 1 feature demo master
  dreo_branch_operate 2 3 dev_3.6.0_20260319 feature_a_20260319 feature_b_20260319
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from unittest import mock

import dreo_branch_manager as bm


class OperationFailure(Exception):
    pass


def fail(message: str) -> None:
    bm.note(message, 'error')
    raise OperationFailure(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dreo 分支管理工具的非交互参数入口。\n\n"
            "菜单映射：\n"
            "  1      创建开发分支（feature / bugfix）\n"
            "  2 1    创建集成分支\n"
            "  2 2    更新集成分支（同步已集成分支新提交）\n"
            "  2 3    添加新的开发分支到集成分支\n"
            "  3      拉取远端分支到本地\n"
            "  4      合并 master 到当前分支\n"
            "  5      合并集成分支到 master\n"
            "  6      生成分支处理报告\n"
            "  7 1    删除分支（仅本地）\n"
            "  7 2    删除分支（本地 + 云端）"
        ),
        epilog=(
            "参数格式：\n"
            "  1 <feature|bugfix> <名称> [master|current]\n"
            "      创建开发分支，基线默认 master。\n\n"
            "  2 1 <dev|release> <版本> <开发分支1> [开发分支2 ...]\n"
            "      创建集成分支，并把指定开发分支集成进去。\n\n"
            "  2 2 <集成分支名>\n"
            "      更新指定集成分支，先同步 master，再同步已集成开发分支的新提交。\n\n"
            "  2 3 <集成分支名> <开发分支1> [开发分支2 ...]\n"
            "      向指定集成分支追加新的开发分支。\n\n"
            "  3 <远端分支名>\n"
            "      将指定远端分支拉取到本地。\n\n"
            "  4\n"
            "      将 master 合并到当前分支。\n\n"
            "  5 <release分支名>\n"
            "      将指定 release 分支合并到 master。\n"
            "      可选: --push --delete-related\n\n"
            "  6\n"
            "      在当前仓库生成 HTML / Markdown 分支处理报告。\n\n"
            "  7 1 <分支1> [分支2 ...]\n"
            "      删除本地分支。\n\n"
            "  7 2 <分支1> [分支2 ...]\n"
            "      删除本地 + 云端分支。\n\n"
            "示例:\n"
            "  dreo_branch_operate 2 2 dev_3.6.0_20260319\n"
            "  dreo_branch_operate 1 feature test1 master\n"
            "  dreo_branch_operate 2 3 dev_3.6.0_20260319 feature_test1_20260319 feature_test2_20260319\n"
            "  dreo_branch_operate 5 release_3.5.0_20260324 --push --delete-related\n"
            "  dreo_branch_operate 7 2 feature_test1_20260324 feature_test2_20260324"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "items",
        nargs="+",
        help="菜单编号与参数，格式见下方“参数格式”。",
    )
    parser.add_argument("--push", action="store_true", help="操作成功后自动推送远端。")
    parser.add_argument(
        "--delete-related",
        action="store_true",
        help="用于“合并集成分支到 master”场景，合并成功后自动删除关联开发分支。",
    )
    args = parser.parse_args()
    args.menu1 = args.items[0]
    if args.menu1 in ("2", "7"):
        args.menu2 = args.items[1] if len(args.items) >= 2 else None
        args.params = args.items[2:]
    else:
        args.menu2 = None
        args.params = args.items[1:]
    return args


def option_matches_target(option: str, target: str) -> bool:
    cleaned = option.strip()
    if cleaned == target or cleaned.startswith(f"{target}  ["):
        return True
    if "｜" in cleaned:
        suffix = cleaned.split("｜", 1)[1].strip()
        if suffix == target or suffix.startswith(f"{target}  ["):
            return True
    return cleaned.startswith(f"{target} ")


def make_select_one(targets: list[str]):
    queue = list(targets)

    def _select_one(options, prompt="请选择"):
        if not queue:
            fail(f"内部错误：未为选择框 [{prompt}] 提供目标值。")
        target = queue.pop(0)
        for idx, option in enumerate(options):
            if option_matches_target(option, target):
                return idx
        fail(f"未在选项中找到目标 [{target}]。")

    return _select_one


def make_select_many(targets: list[str]):
    required = list(targets)

    def _select_many(options, prompt="", auto_confirm_single=False):
        indices = []
        missing = []
        for target in required:
            found = None
            for idx, option in enumerate(options):
                if option_matches_target(option, target):
                    found = idx
                    break
            if found is None:
                missing.append(target)
            elif found not in indices:
                indices.append(found)
        if missing:
            fail(f"未在候选列表中找到以下分支: {', '.join(missing)}")
        return indices

    return _select_many


def make_confirm(enable_delete_related: bool):
    def _confirm(prompt: str) -> bool:
        if "删除这些关联开发分支" in prompt:
            return enable_delete_related
        return True

    return _confirm


def make_offer_push(enable_push: bool):
    def _offer_push(branch, set_upstream=False, prompt=None):
        if not enable_push:
            bm.note(f"参数模式未启用 --push，已跳过远端推送: {branch}", 'tip')
            return False
        if not bm.has_origin_remote():
            bm.note("未检测到 origin 远端，已跳过推送。", 'tip')
            return False
        with bm.LoadingIndicator(f"正在推送分支 [{branch}] 到远端"):
            if set_upstream:
                ok, _, err = bm.run_git('push', '-u', 'origin', branch)
            else:
                ok, _, err = bm.run_git('push', 'origin', branch)
        if ok:
            bm.note(f"已推送到远端: {branch}", 'success')
            return True
        bm.note(f"推送失败: {err}", 'error')
        return False

    return _offer_push


def make_noninteractive_conflict_handler():
    def _handle_conflict(merging_branch, action_label="合并"):
        bm.run_git('merge', '--abort')
        bm.note(f"{action_label} [{merging_branch}] 发生冲突，参数模式已自动终止并执行 git merge --abort。", 'error')
        return False

    return _handle_conflict


@contextlib.contextmanager
def patched_manager(**patches):
    stack = contextlib.ExitStack()
    try:
        for name, value in patches.items():
            stack.enter_context(mock.patch.object(bm, name, value))
        yield
    finally:
        stack.close()


def run_create_feature(args: argparse.Namespace) -> bool:
    if len(args.params) < 2:
        fail("创建开发分支需要参数：<feature|bugfix> <名称> [master|current]")
    branch_type, name = args.params[0], args.params[1]
    base_name = args.params[2] if len(args.params) >= 3 else "master"
    if branch_type not in ("feature", "bugfix"):
        fail("分支类型只能是 feature 或 bugfix。")
    if base_name not in ("master", "current"):
        fail("基线只能是 master 或 current。")

    current = bm.get_current_branch()
    master = bm.get_master_branch()
    if not master:
        fail("未找到 master / main 分支。")

    branch_name = f"{branch_type}_{name}_{bm.today_str()}"
    if bm.has_local_branch(branch_name):
        fail(f"分支 '{branch_name}' 已存在；参数模式下无法二次改名，请更换名称后重试。")

    select_targets = []
    if current != master:
        select_targets.append(master if base_name == "master" else current)
    select_targets.append(branch_type)

    with patched_manager(
        select_one=make_select_one(select_targets),
        read_text_input=lambda prompt, prefix='> ': name,
        confirm=make_confirm(enable_delete_related=False),
        offer_push_branch=make_offer_push(args.push),
        handle_conflict=make_noninteractive_conflict_handler(),
    ):
        result = bm.create_feature_branch()
    return result is not False


def run_create_integration(args: argparse.Namespace) -> bool:
    if len(args.params) < 3:
        fail("创建集成分支需要参数：<dev|release> <版本> <开发分支1> [开发分支2 ...]")
    env_prefix, version = args.params[0], args.params[1]
    branches = args.params[2:]
    if env_prefix not in ("dev", "release"):
        fail("集成用途只能是 dev 或 release。")

    with patched_manager(
        select_one=make_select_one([env_prefix]),
        select_many=make_select_many(branches),
        read_text_input=lambda prompt, prefix='> ': version,
        confirm=make_confirm(enable_delete_related=False),
        offer_push_branch=make_offer_push(args.push),
        handle_conflict=make_noninteractive_conflict_handler(),
    ):
        result = bm.create_integration_branch()
    return result is not False


def run_update_integration(args: argparse.Namespace) -> bool:
    if not args.params:
        fail("更新集成分支需要参数：<集成分支名>")
    int_branch = args.params[0]
    with patched_manager(
        select_one=make_select_one([int_branch]),
        confirm=make_confirm(enable_delete_related=False),
        offer_push_branch=make_offer_push(args.push),
        handle_conflict=make_noninteractive_conflict_handler(),
    ):
        result = bm.update_integration_branch()
    return result is not False


def run_add_branches_to_integration(args: argparse.Namespace) -> bool:
    if len(args.params) < 2:
        fail("添加开发分支到集成分支需要参数：<集成分支名> <开发分支1> [开发分支2 ...]")
    int_branch = args.params[0]
    branches = args.params[1:]
    with patched_manager(
        select_one=make_select_one([int_branch]),
        select_many=make_select_many(branches),
        confirm=make_confirm(enable_delete_related=False),
        offer_push_branch=make_offer_push(args.push),
        handle_conflict=make_noninteractive_conflict_handler(),
    ):
        result = bm.add_branches_to_integration()
    return result is not False


def run_pull_remote_branch(args: argparse.Namespace) -> bool:
    if not args.params:
        fail("拉取远端分支到本地需要参数：<分支名>")
    branch = args.params[0]
    with patched_manager(select_one=make_select_one([branch])):
        result = bm.pull_remote_branch_to_local()
    return result is not False


def run_merge_master_to_current(args: argparse.Namespace) -> bool:
    with patched_manager(
        confirm=make_confirm(enable_delete_related=False),
        offer_push_branch=make_offer_push(args.push),
        handle_conflict=make_noninteractive_conflict_handler(),
    ):
        result = bm.merge_master_to_current()
    return result is not False


def run_merge_to_master(args: argparse.Namespace) -> bool:
    if not args.params:
        fail("合并集成分支到 master 需要参数：<release 分支名>")
    release_branch = args.params[0]
    with patched_manager(
        select_one=make_select_one([release_branch]),
        confirm=make_confirm(enable_delete_related=args.delete_related),
        offer_push_branch=make_offer_push(args.push),
        handle_conflict=make_noninteractive_conflict_handler(),
    ):
        result = bm.merge_to_master()
    return result is not False


def run_generate_report(args: argparse.Namespace) -> bool:
    result = bm.generate_branch_report_menu()
    return result is not False


def run_delete_branches(args: argparse.Namespace) -> bool:
    if args.menu2 not in ("1", "2"):
        fail("删除分支需要二级菜单编号：1=仅本地，2=本地+云端。")
    if not args.params:
        fail("删除分支需要参数：<分支1> [分支2 ...]")
    include_remote = args.menu2 == "2"
    with patched_manager(
        select_many=make_select_many(args.params),
        confirm=make_confirm(enable_delete_related=False),
    ):
        result = bm.delete_branches(include_remote=include_remote)
    return result is not False


def dispatch(args: argparse.Namespace) -> bool:
    bm.check_git_repo()
    if args.menu1 == "1":
        return run_create_feature(args)
    if args.menu1 == "2" and args.menu2 == "1":
        return run_create_integration(args)
    if args.menu1 == "2" and args.menu2 == "2":
        return run_update_integration(args)
    if args.menu1 == "2" and args.menu2 == "3":
        return run_add_branches_to_integration(args)
    if args.menu1 == "3":
        return run_pull_remote_branch(args)
    if args.menu1 == "4":
        return run_merge_master_to_current(args)
    if args.menu1 == "5":
        return run_merge_to_master(args)
    if args.menu1 == "6":
        return run_generate_report(args)
    if args.menu1 == "7":
        return run_delete_branches(args)
    fail("不支持的菜单路径，请检查参数。")
    return False


def print_result(success: bool) -> None:
    print(f"执行结果: {'成功' if success else '失败'}")
    print(f"DREO_RESULT={'SUCCESS' if success else 'FAILED'}")


def run() -> None:
    args = parse_args()
    original_note = bm.note
    state = {"had_error": False}

    def tracking_note(message, level='info'):
        if level == 'error':
            state["had_error"] = True
        return original_note(message, level)

    with mock.patch.object(bm, "note", tracking_note):
        try:
            success = dispatch(args)
        except OperationFailure:
            print_result(False)
            raise SystemExit(1)
        except KeyboardInterrupt:
            tracking_note("已中断。", 'error')
            print_result(False)
            raise SystemExit(130)
        except Exception as exc:
            tracking_note(f"参数化执行失败: {exc}", 'error')
            print_result(False)
            raise SystemExit(1)

    success = success and not state["had_error"]
    print_result(success)
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    run()
