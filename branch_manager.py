#!/usr/bin/env python3
"""
AoneFlow Git 分支管理工具
基于阿里巴巴 AoneFlow 分支模型
分支结构: feature/bugfix + n*release + master
"""

import subprocess
import sys


# ─── Git 基础操作 ────────────────────────────────────────────────

def run_git(*args, capture=True):
    """执行 git 命令，返回 (success, stdout, stderr)"""
    try:
        result = subprocess.run(
            ['git'] + list(args),
            capture_output=capture,
            text=True
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        print("错误：未找到 git 命令，请确认 git 已安装。")
        sys.exit(1)


def check_git_repo():
    ok, _, _ = run_git('rev-parse', '--is-inside-work-tree')
    if not ok:
        print("错误：当前目录不是 git 仓库，请在 git 仓库目录中运行此工具。")
        sys.exit(1)


def get_current_branch():
    _, branch, _ = run_git('rev-parse', '--abbrev-ref', 'HEAD')
    return branch


def get_local_branches():
    _, output, _ = run_git('branch', '--format=%(refname:short)')
    return [b.strip() for b in output.splitlines() if b.strip()]


def get_feature_branches():
    return [b for b in get_local_branches()
            if b.startswith('feature_') or b.startswith('bugfix_')]


def get_integration_branches():
    return [b for b in get_local_branches()
            if b.startswith('release_') or b.startswith('test_')]


def get_master_branch():
    branches = get_local_branches()
    return 'master' if 'master' in branches else ('main' if 'main' in branches else None)


# ─── 终端 UI 工具 ─────────────────────────────────────────────────

def sep(char='─', width=52):
    print(char * width)


def header(title):
    sep()
    print(f"  {title}")
    sep()


def select_one(options, prompt="请选择"):
    """单选，返回 0-based 索引"""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input(f"\n{prompt} [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  无效输入，请输入 1 到 {len(options)} 之间的数字。")


def select_many(options, prompt="请选择（多个用逗号分隔，all=全选）"):
    """多选，返回 0-based 索引列表"""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    print(f"\n  {prompt}")
    while True:
        raw = input("  > ").strip()
        if raw.lower() == 'all':
            return list(range(len(options)))
        parts = [p.strip() for p in raw.split(',')]
        indices, valid = [], True
        for p in parts:
            if p.isdigit() and 1 <= int(p) <= len(options):
                idx = int(p) - 1
                if idx not in indices:
                    indices.append(idx)
            else:
                print(f"  无效输入: '{p}'，请重新输入。")
                valid = False
                break
        if valid and indices:
            return indices
        elif valid:
            print("  请至少选择一个选项。")


def confirm(prompt):
    return input(f"\n  {prompt} (y/n): ").strip().lower() == 'y'


# ─── 冲突处理 ────────────────────────────────────────────────────

def handle_conflict(merging_branch):
    """引导用户解决合并冲突，返回是否最终成功"""
    print(f"\n  [!] 合并 [{merging_branch}] 时发生冲突！")
    print("\n  请在另一个终端中执行以下步骤：")
    print("    1. 编辑冲突文件，删除所有 <<<<<<<, =======, >>>>>>> 标记")
    print("    2. git add <已解决的文件>")
    print("    3. 回到此工具，选择"继续合并"")

    while True:
        sep()
        print("  冲突处理：")
        print("  1. 我已解决所有冲突，继续完成合并")
        print("  2. 放弃此次合并（git merge --abort）")
        choice = input("\n  请选择 [1/2]: ").strip()

        if choice == '1':
            # 检查是否仍有未解决冲突
            _, status, _ = run_git('status', '--porcelain')
            unresolved = [l for l in status.splitlines()
                          if l[:2] in ('UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD')]
            if unresolved:
                print("\n  [!] 仍有未解决的冲突文件：")
                for f in unresolved:
                    print(f"      {f.strip()}")
                print("  请解决全部冲突并 git add 后再继续。")
                continue

            ok, _, err = run_git('commit', '--no-edit')
            if ok:
                print(f"  [✓] 冲突已解决，合并完成: {merging_branch}")
                return True
            else:
                print(f"  [!] 提交失败: {err}")
                print("  请确认所有冲突文件均已 git add。")

        elif choice == '2':
            run_git('merge', '--abort')
            print(f"  [✗] 已放弃合并: {merging_branch}")
            return False
        else:
            print("  请输入 1 或 2。")


def do_merge(source_branch):
    """将 source_branch 合并到当前分支，处理冲突。返回是否成功。"""
    current = get_current_branch()
    print(f"\n  合并 [{source_branch}] → [{current}] ...")
    ok, out, err = run_git('merge', '--no-ff', source_branch)
    if ok:
        print(f"  [✓] 合并成功: {source_branch}")
        return True
    if 'CONFLICT' in out or 'CONFLICT' in err:
        return handle_conflict(source_branch)
    print(f"  [!] 合并失败: {err or out}")
    return False


# ─── 功能 1：创建特性分支 ─────────────────────────────────────────

def create_feature_branch():
    header("创建特性分支（从 master）")

    base = get_master_branch()
    if not base:
        print("  [!] 未找到 master / main 分支，请先初始化仓库。")
        return

    # 选择分支类型
    print("  请选择分支类型：")
    type_idx = select_one(['feature  — 新功能开发', 'bugfix   — 缺陷修复'], "分支类型")
    branch_type = ['feature', 'bugfix'][type_idx]

    # 输入分支名称
    existing = get_local_branches()
    while True:
        name = input(f"\n  分支名称（最终: {branch_type}_<名称>）: ").strip()
        if not name:
            print("  名称不能为空。")
            continue
        if any(c in name for c in ' ~^:?*[\\'):
            print("  包含非法字符，请重新输入。")
            continue
        branch_name = f"{branch_type}_{name}"
        if branch_name in existing:
            print(f"  [!] 分支 '{branch_name}' 已存在，请换一个名称。")
            continue
        break

    # 切换到 base 并更新
    print(f"\n  切换到 {base}，同步最新代码...")
    run_git('checkout', base)
    ok, _, _ = run_git('pull', 'origin', base)
    if not ok:
        print(f"  [提示] 拉取远端失败，使用本地 {base} 继续。")

    # 创建新分支
    ok, _, err = run_git('checkout', '-b', branch_name)
    if ok:
        print(f"\n  [✓] 已创建并切换到: {branch_name}  (基于 {base})")
    else:
        print(f"\n  [!] 创建分支失败: {err}")


# ─── 功能 2：创建/更新集成分支 ───────────────────────────────────

def create_integration_branch():
    header("创建集成分支（合并特性分支）")

    feature_branches = get_feature_branches()
    if not feature_branches:
        print("  [!] 没有找到特性分支（feature_ / bugfix_ 开头）。")
        print("  请先使用功能 1 创建特性分支。")
        return

    base = get_master_branch()

    # 选择集成环境
    print("  请选择集成分支用途：")
    env_idx = select_one(
        ['test    — 测试/日常环境集成', 'release — 预发/生产环境集成'],
        "集成用途"
    )
    env_prefix = ['test', 'release'][env_idx]

    # 输入版本号
    while True:
        version = input(f"\n  版本号或名称（将创建: {env_prefix}_<版本>）: ").strip()
        if version:
            break
        print("  版本号不能为空。")

    int_branch = f"{env_prefix}_{version}"
    existing = get_local_branches()

    if int_branch in existing:
        print(f"\n  [提示] 分支 '{int_branch}' 已存在。")
        if not confirm("向该分支追加合并特性分支？"):
            return
        run_git('checkout', int_branch)
    else:
        if not base:
            print("  [!] 未找到 master / main 分支。")
            return
        print(f"\n  从 {base} 创建集成分支 {int_branch}...")
        run_git('checkout', base)
        ok, _, err = run_git('checkout', '-b', int_branch)
        if not ok:
            print(f"  [!] 创建失败: {err}")
            return
        print(f"  [✓] 已创建集成分支: {int_branch}")

    # 选择要合并的特性分支
    print(f"\n  选择要合并到 [{int_branch}] 的特性分支：")
    selected = [feature_branches[i] for i in select_many(feature_branches)]

    print(f"\n  将合并以下分支 → [{int_branch}]：")
    for b in selected:
        print(f"    · {b}")
    if not confirm("确认执行合并？"):
        print("  已取消。")
        return

    succeeded, failed = [], []
    for branch in selected:
        if do_merge(branch):
            succeeded.append(branch)
        else:
            failed.append(branch)

    # 结果汇总
    print()
    sep()
    print("  合并结果汇总：")
    if succeeded:
        print(f"  [✓] 成功 ({len(succeeded)}): " + ", ".join(succeeded))
    if failed:
        print(f"  [✗] 跳过 ({len(failed)}): " + ", ".join(failed))
    print(f"\n  当前所在集成分支: {get_current_branch()}")


# ─── 功能 3：合并发布分支回 master ───────────────────────────────

def merge_to_master():
    header("合并发布分支回 master（基线写入）")

    int_branches = get_integration_branches()
    if not int_branches:
        print("  [!] 没有找到集成/发布分支（release_ / test_ 开头）。")
        return

    base = get_master_branch()
    if not base:
        print("  [!] 未找到 master / main 分支。")
        return

    print(f"  选择要合并到 [{base}] 的发布分支：")
    release_branch = int_branches[select_one(int_branches)]

    print(f"\n  操作：[{release_branch}] → [{base}]")
    if not confirm("确认执行？"):
        print("  已取消。")
        return

    # 切换到 master
    ok, _, err = run_git('checkout', base)
    if not ok:
        print(f"  [!] 切换到 {base} 失败: {err}")
        return

    ok, _, _ = run_git('pull', 'origin', base)
    if not ok:
        print(f"  [提示] 拉取远端失败，使用本地 {base} 继续。")

    if do_merge(release_branch):
        _, log, _ = run_git('log', '--oneline', '-5')
        print(f"\n  [✓] [{release_branch}] 已成功合并到 {base}！")
        print("\n  最近提交记录：")
        for line in log.splitlines():
            print(f"    {line}")
        print(f"\n  [提示] 推送到远端: git push origin {base}")
    else:
        print(f"\n  [✗] 合并失败或已放弃。")


# ─── 主菜单 ──────────────────────────────────────────────────────

def show_status():
    current = get_current_branch()
    features = get_feature_branches()
    integrations = get_integration_branches()
    print(f"\n  当前分支: \033[1m{current}\033[0m"
          f"  |  特性分支: {len(features)}"
          f"  |  集成分支: {len(integrations)}")


def main():
    print("\n" + "═" * 52)
    print("   AoneFlow Git 分支管理工具")
    print("   feature/bugfix  +  n×release  +  master")
    print("═" * 52)

    check_git_repo()

    menu = {
        '1': ('创建特性分支（feature / bugfix）', create_feature_branch),
        '2': ('创建 / 更新集成分支（合并特性分支）', create_integration_branch),
        '3': ('合并发布分支回 master（基线写入）', merge_to_master),
        '0': ('退出', None),
    }

    while True:
        show_status()
        sep()
        print("  主菜单：")
        for key in ['1', '2', '3', '0']:
            print(f"  {key}. {menu[key][0]}")
        sep()

        choice = input("  请选择操作: ").strip()

        if choice == '0':
            print("\n  再见！\n")
            sys.exit(0)
        elif choice in menu:
            print()
            menu[choice][1]()
        else:
            print("  无效输入，请输入 0-3。")

        input("\n  按回车键返回主菜单...")


if __name__ == '__main__':
    main()
