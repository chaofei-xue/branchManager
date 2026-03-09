#!/usr/bin/env python3
"""
Dreo 分支管理工具
"""

import subprocess
import sys
from datetime import date


def today_str():
    return date.today().strftime('%Y%m%d')


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


def check_rerere():
    """检查 rerere 是否开启，未开启则提示用户"""
    _, val, _ = run_git('config', '--local', 'rerere.enabled')
    if val != 'true':
        print("\n  [提示] 未开启 rerere（冲突记忆）功能。")
        print("  开启后，同一冲突只需手动解决一次，后续合并将自动重用解决方案。")
        if confirm("现在为此仓库开启 rerere？"):
            run_git('config', '--local', 'rerere.enabled', 'true')
            print("  [✓] rerere 已开启。")


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
            if b.startswith('release_') or b.startswith('dev_')]


def sort_branches_by_date(branches, limit=10):
    """按分支名末尾的 yyyyMMdd 日期倒序排序，最多返回 limit 条"""
    def extract_date(b):
        suffix = b.rsplit('_', 1)[-1]
        return suffix if (len(suffix) == 8 and suffix.isdigit()) else '00000000'
    return sorted(branches, key=extract_date, reverse=True)[:limit]


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
    print("    3. 回到此工具，选择「继续合并」")

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
        # 尝试让 rerere 自动应用已记录的解决方案
        _, rerere_out, _ = run_git('rerere')
        if rerere_out:
            # rerere 有输出说明应用了记录，检查是否还有残余冲突
            _, status, _ = run_git('status', '--porcelain')
            still_conflict = [l for l in status.splitlines()
                              if l[:2] in ('UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD')]
            if not still_conflict:
                # 全部自动解决，暂存并提交
                run_git('add', '-u')
                commit_ok, _, commit_err = run_git('commit', '--no-edit')
                if commit_ok:
                    print(f"  [✓] rerere 自动重用了历史解决方案，合并完成: {source_branch}")
                    print("  [提示] 请检查自动解决的文件是否符合预期。")
                    return True
                print(f"  [!] 自动提交失败: {commit_err}")
        return handle_conflict(source_branch)
    print(f"  [!] 合并失败: {err or out}")
    return False


# ─── 功能 1：创建开发分支 ─────────────────────────────────────────

def create_feature_branch():
    header("创建开发分支（从 master）")

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
    date_suffix = today_str()
    while True:
        name = input(f"\n  分支名称（最终: {branch_type}_<名称>_{date_suffix}）: ").strip()
        if not name:
            print("  名称不能为空。")
            continue
        if any(c in name for c in ' ~^:?*[\\'):
            print("  包含非法字符，请重新输入。")
            continue
        branch_name = f"{branch_type}_{name}_{date_suffix}"
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
    header("创建集成分支（合并开发分支）")

    feature_branches = get_feature_branches()
    if not feature_branches:
        print("  [!] 没有找到开发分支（feature_ / bugfix_ 开头）。")
        print("  请先使用功能 1 创建开发分支。")
        return

    base = get_master_branch()

    # 选择集成环境
    print("  请选择集成分支用途：")
    env_idx = select_one(
        ['dev     — 测试/日常环境集成', 'release — 预发/生产环境集成'],
        "集成用途"
    )
    env_prefix = ['dev', 'release'][env_idx]

    # 输入版本号
    date_suffix = today_str()
    while True:
        version = input(f"\n  版本号或名称（将创建: {env_prefix}_<版本>_{date_suffix}）: ").strip()
        if version:
            break
        print("  版本号不能为空。")

    int_branch = f"{env_prefix}_{version}_{date_suffix}"
    existing = get_local_branches()

    if int_branch in existing:
        print(f"\n  [提示] 分支 '{int_branch}' 已存在。")
        if not confirm("向该分支追加合并开发分支？"):
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

    # 选择要合并的开发分支（按日期倒序，最多显示 10 条）
    sorted_features = sort_branches_by_date(feature_branches)
    if len(feature_branches) > 10:
        print(f"\n  [提示] 共 {len(feature_branches)} 个开发分支，按时间倒序显示最新 10 条：")
    else:
        print(f"\n  选择要合并到 [{int_branch}] 的开发分支：")
    selected = [sorted_features[i] for i in select_many(sorted_features)]

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


# ─── 功能 3：同步更新集成分支 ────────────────────────────────────

def update_integration_branch():
    header("同步更新集成分支（重新合并开发分支新提交）")

    int_branches = get_integration_branches()
    if not int_branches:
        print("  [!] 没有找到集成分支（dev_ / release_ 开头）。")
        return

    # 选择要更新的集成分支
    print("  请选择要同步更新的集成分支：")
    int_branch = int_branches[select_one(int_branches)]

    all_features = sort_branches_by_date(get_feature_branches(), limit=len(get_feature_branches()))
    if not all_features:
        print("  [!] 没有找到开发分支。")
        return

    # 对每个开发分支统计相对于集成分支的新提交数
    has_new, no_new = [], []
    for b in all_features:
        _, count_str, _ = run_git('rev-list', '--count', f'{int_branch}..{b}')
        n = int(count_str) if count_str.isdigit() else 0
        if n > 0:
            has_new.append((b, n))
        else:
            no_new.append(b)

    # 展示分支状态
    print(f"\n  开发分支相对于 [{int_branch}] 的状态：")
    ordered = []
    if has_new:
        print("\n  ── 有新提交（建议同步）─────────────────────")
        for b, n in has_new:
            ordered.append(b)
            print(f"  {len(ordered):2}. {b}  [{n} 个新提交]")
    if no_new:
        print("\n  ── 无新提交 ─────────────────────────────────")
        for b in no_new:
            ordered.append(b)
            print(f"  {len(ordered):2}. {b}  [已是最新]")

    if not has_new:
        print("\n  所有开发分支均已是最新，无需同步。")
        return

    # 默认预选有新提交的分支，允许用户调整
    default_indices = list(range(len(has_new)))
    print(f"\n  请选择要合并的分支（默认已勾选有新提交的分支，多选用逗号分隔，all=全选）")
    print(f"  直接回车使用默认选择 [{','.join(str(i+1) for i in default_indices)}]：")

    raw = input("  > ").strip()
    if raw == '':
        selected_indices = default_indices
    elif raw.lower() == 'all':
        selected_indices = list(range(len(ordered)))
    else:
        parts = [p.strip() for p in raw.split(',')]
        selected_indices = []
        valid = True
        for p in parts:
            if p.isdigit() and 1 <= int(p) <= len(ordered):
                idx = int(p) - 1
                if idx not in selected_indices:
                    selected_indices.append(idx)
            else:
                print(f"  无效输入: '{p}'")
                valid = False
                break
        if not valid or not selected_indices:
            print("  已取消。")
            return

    selected = [ordered[i] for i in selected_indices]
    print(f"\n  将合并以下分支 → [{int_branch}]：")
    for b in selected:
        print(f"    · {b}")
    if not confirm("确认同步？"):
        print("  已取消。")
        return

    # 切换到集成分支
    ok, _, err = run_git('checkout', int_branch)
    if not ok:
        print(f"  [!] 切换到 [{int_branch}] 失败: {err}")
        return

    succeeded, failed, skipped = [], [], []
    for branch in selected:
        _, count_str, _ = run_git('rev-list', '--count', f'{int_branch}..{branch}')
        n = int(count_str) if count_str.isdigit() else 0
        if n == 0:
            print(f"\n  [~] [{branch}] 无新增提交，跳过。")
            skipped.append(branch)
            continue
        print(f"\n  [{branch}] 有 {n} 个新提交，执行合并...")
        if do_merge(branch):
            succeeded.append(branch)
        else:
            failed.append(branch)

    print()
    sep()
    print("  同步结果汇总：")
    if succeeded:
        print(f"  [✓] 已同步 ({len(succeeded)}): " + ", ".join(succeeded))
    if skipped:
        print(f"  [~] 无变更 ({len(skipped)}): " + ", ".join(skipped))
    if failed:
        print(f"  [✗] 失败   ({len(failed)}): " + ", ".join(failed))
    print(f"\n  当前所在集成分支: {get_current_branch()}")


# ─── 功能 4：删除本地分支 ────────────────────────────────────────

def delete_local_branches():
    header("删除本地分支")

    all_branches = get_local_branches()
    current = get_current_branch()
    base = get_master_branch()

    # 排除当前分支和 master/main
    protected = {current, base}
    deletable = [b for b in all_branches if b not in protected]

    if not deletable:
        print("  [!] 没有可删除的分支。")
        print(f"  [提示] 当前分支 [{current}] 和 [{base}] 受保护，不可删除。")
        return

    # 按类型分组展示，方便选择
    feature_bs = [b for b in deletable if b.startswith('feature_') or b.startswith('bugfix_')]
    int_bs     = [b for b in deletable if b.startswith('dev_') or b.startswith('release_')]
    other_bs   = [b for b in deletable if b not in feature_bs and b not in int_bs]

    # 按分组顺序构建带编号的列表，组间插入标题
    ordered = []
    groups = [
        ('开发分支', sort_branches_by_date(feature_bs, limit=len(feature_bs))),
        ('集成分支', sort_branches_by_date(int_bs,     limit=len(int_bs))),
        ('其他分支', other_bs),
    ]
    print()
    for group_name, branches in groups:
        if not branches:
            continue
        print(f"  ── {group_name} {'─' * (36 - len(group_name))}")
        for b in branches:
            ordered.append(b)
            print(f"  {len(ordered):2}. {b}")

    print(f"\n  [提示] 当前分支 [{current}] 和 [{base}] 受保护，不在列表中。")
    print("  选择要删除的分支（多个用逗号分隔，all=全选）")
    selected_indices = []
    while True:
        raw = input("  > ").strip()
        if raw.lower() == 'all':
            selected_indices = list(range(len(ordered)))
            break
        parts = [p.strip() for p in raw.split(',')]
        indices, valid = [], True
        for p in parts:
            if p.isdigit() and 1 <= int(p) <= len(ordered):
                idx = int(p) - 1
                if idx not in indices:
                    indices.append(idx)
            else:
                print(f"  无效输入: '{p}'，请重新输入。")
                valid = False
                break
        if valid and indices:
            selected_indices = indices
            break
        elif valid:
            print("  请至少选择一个选项。")
    selected = [ordered[i] for i in selected_indices]

    print(f"\n  将删除以下 {len(selected)} 个本地分支：")
    for b in selected:
        print(f"    · {b}")
    print("\n  [!] 此操作不可恢复，请确认分支代码已合并或不再需要。")
    if not confirm("确认删除？"):
        print("  已取消。")
        return

    succeeded, failed = [], []
    for branch in selected:
        # 先尝试安全删除（-d），若分支未合并则提示强制
        ok, _, err = run_git('branch', '-d', branch)
        if ok:
            succeeded.append(branch)
            print(f"  [✓] 已删除: {branch}")
        else:
            # 分支有未合并提交，询问是否强制删除
            print(f"\n  [!] [{branch}] 包含未合并的提交，无法安全删除。")
            if confirm(f"强制删除 [{branch}]？（提交将丢失）"):
                ok2, _, err2 = run_git('branch', '-D', branch)
                if ok2:
                    succeeded.append(branch)
                    print(f"  [✓] 已强制删除: {branch}")
                else:
                    failed.append(branch)
                    print(f"  [✗] 删除失败: {err2}")
            else:
                failed.append(branch)
                print(f"  [~] 已跳过: {branch}")

    print()
    sep()
    print("  删除结果汇总：")
    if succeeded:
        print(f"  [✓] 已删除 ({len(succeeded)}): " + ", ".join(succeeded))
    if failed:
        print(f"  [✗] 跳过   ({len(failed)}): " + ", ".join(failed))


# ─── 功能 5：合并发布分支回 master ───────────────────────────────

def merge_to_master():
    header("合并发布分支回 master（基线写入）")

    int_branches = get_integration_branches()
    if not int_branches:
        print("  [!] 没有找到集成/发布分支（dev_ / release_ 开头）。")
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
          f"  |  开发分支: {len(features)}"
          f"  |  集成分支: {len(integrations)}")


def main():
    print("\n" + "═" * 52)
    print("   Dreo 分支管理工具")
    print("═" * 52)

    check_git_repo()
    check_rerere()

    menu = {
        '1': ('创建开发分支（feature / bugfix）', create_feature_branch),
        '2': ('创建集成分支（合并开发分支）', create_integration_branch),
        '3': ('同步更新集成分支（重新合并已集成的开发分支）', update_integration_branch),
        '4': ('删除本地分支（多选）', delete_local_branches),
        '5': ('合并发布分支回 master（基线写入）', merge_to_master),
        '0': ('退出', None),
    }

    while True:
        show_status()
        sep()
        print("  主菜单：")
        for key in ['1', '2', '3', '4', '5', '0']:
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
            print("  无效输入，请输入 0-5。")

        input("\n  按回车键返回主菜单...")


if __name__ == '__main__':
    main()
