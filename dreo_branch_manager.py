#!/usr/bin/env python3
"""
Dreo 分支管理工具
"""

import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
import os
import shutil
import codecs
import re
import unicodedata
from pathlib import Path

try:
    import termios
    import tty
except ImportError:  # pragma: no cover
    termios = None
    tty = None


def today_str():
    return date.today().strftime('%Y%m%d')


USE_COLOR = sys.stdout.isatty() and os.environ.get('TERM', '') != 'dumb'

UI = {
    'app': '🌿',
    'section': '🧭',
    'menu': '📚',
    'current_branch': '📍',
    'feature_branch': '🌱',
    'integration_branch': '🧩',
    'create_feature': '🌱',
    'create_integration': '🧩',
    'sync': '🔄',
    'merge': '🔀',
    'release': '🚀',
    'delete': '🧹',
    'conflict': '🚧',
    'type': '🔖',
    'select': '🎯',
    'status': '📊',
    'records': '📋',
    'report': '📝',
    'pinned': '📌',
    'build': '🏗️',
    'checkout': '🔄',
    'skip': '⏭️',
    'back': '↩️',
    'interrupt': '🛑',
}


def paint(text, *codes):
    if not USE_COLOR or not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def accent(text):
    return paint(text, '1', '36')


def muted(text):
    return paint(text, '2')


def icon_slot(icon, color='36', width=2):
    padding = ' ' * max(0, width - display_width(icon))
    return f"{paint(icon, color)}{padding}"


def note(message, level='info'):
    styles = {
        'info': ('🔹', '36'),
        'success': ('✅', '32'),
        'warn': ('🚧', '33'),
        'error': ('🚫', '31'),
        'tip': ('💡', '35'),
    }
    icon, color = styles.get(level, styles['info'])
    print(f"  {icon_slot(icon, color)} {message}")


def print_list(items, icon='•'):
    for item in items:
        print(f"    {icon} {item}")


def summary_block(title, rows):
    print()
    sep()
    print(f"  {accent(UI['status'] + ' ' + title)}")
    for icon, text in rows:
        print(f"  {icon} {text}")


def branch_badge(branch):
    return paint(branch, '1', '37')


def display_width(text):
    width = 0
    for char in text:
        if char == '\n':
            width = 0
            continue
        if char == '\t':
            width += 4
            continue
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
    return width


def wrapped_line_count(text):
    columns = max(shutil.get_terminal_size(fallback=(80, 24)).columns, 20)
    width = max(display_width(text), 1)
    return max((width - 1) // columns + 1, 1)


def clear_screen():
    if sys.stdin.isatty() and sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def read_input(prompt, prefix='> ', redraw=False, echo_label=None):
    if prompt:
        print(f"\n  {prompt}")
    raw = input(f"  {prefix}")
    value = raw.strip()
    if sys.stdin.isatty() and sys.stdout.isatty():
        # 某些终端/输入法组合在编辑中文或特殊字符时会留下错误回显，
        # 这里按实际显示宽度回退并清理所有被输入内容占用的物理行后再重绘。
        rows = wrapped_line_count(f"  {prefix}{raw}")
        for _ in range(rows):
            sys.stdout.write("\033[1A\r\033[2K")
        sys.stdout.write(f"  {prefix}{value}\n")
        sys.stdout.flush()
        if redraw:
            clear_screen()
            if echo_label:
                note(f"{echo_label}: {value or '（空）'}", 'info')
    return value


def read_text_input(prompt, prefix='> '):
    print(f"\n  {prompt}")
    if not (sys.stdin.isatty() and sys.stdout.isatty() and termios and tty):
        return input(f"  {prefix}").strip()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    decoder = codecs.getincrementaldecoder('utf-8')()
    chars = []

    def render():
        sys.stdout.write("\r\033[2K")
        sys.stdout.write(f"  {prefix}{''.join(chars)}")
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        render()
        while True:
            chunk = os.read(fd, 1)
            if not chunk:
                break
            if chunk in (b'\r', b'\n'):
                break
            if chunk == b'\x03':
                raise KeyboardInterrupt
            if chunk in (b'\x7f', b'\b'):
                if chars:
                    chars.pop()
                    render()
                continue
            if chunk == b'\x1b':
                os.read(fd, 2)
                continue

            text = decoder.decode(chunk, final=False)
            if text:
                chars.append(text)
                render()
        sys.stdout.write("\n")
        sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return ''.join(chars).strip()


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
        note("未找到 git 命令，请确认 git 已安装。", 'error')
        sys.exit(1)


def ensure_git_success(ok, err, action):
    """统一处理关键 git 操作失败场景。"""
    if ok:
        return True
    note(f"{action}失败: {err or '未知错误'}", 'error')
    return False


def check_git_repo():
    ok, _, _ = run_git('rev-parse', '--is-inside-work-tree')
    if not ok:
        note("当前目录不是 git 仓库，请在 git 仓库目录中运行此工具。", 'error')
        sys.exit(1)


def check_rerere():
    """检查 rerere 是否开启，未开启则提示用户"""
    _, val, _ = run_git('config', '--local', 'rerere.enabled')
    if val != 'true':
        print()
        note("未开启 rerere（冲突记忆）功能。", 'tip')
        print("  开启后，同一冲突只需手动解决一次，后续合并将自动重用解决方案。")
        if confirm("现在为此仓库开启 rerere？"):
            run_git('config', '--local', 'rerere.enabled', 'true')
            note("rerere 已开启。", 'success')


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
    branches = [
        b for b in get_local_branches()
        if b.startswith('release_') or b.startswith('dev_')
    ]
    return sort_branches_by_date(branches, limit=len(branches))


def sort_branches_by_date(branches, limit=10):
    """按分支名末尾的 yyyyMMdd 日期倒序排序，最多返回 limit 条"""
    def extract_date(b):
        suffix = b.rsplit('_', 1)[-1]
        return suffix if (len(suffix) == 8 and suffix.isdigit()) else '00000000'
    return sorted(branches, key=extract_date, reverse=True)[:limit]


def get_master_branch():
    branches = get_local_branches()
    return 'master' if 'master' in branches else ('main' if 'main' in branches else None)


def is_integration_branch(branch):
    return branch.startswith('dev_') or branch.startswith('release_')


def get_unmerged_files():
    ok, output, _ = run_git('diff', '--name-only', '--diff-filter=U')
    if not ok:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def has_conflict_markers(path):
    try:
        text = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return True
    return any(marker in text for marker in ('<<<<<<<', '=======', '>>>>>>>'))


# ─── 终端 UI 工具 ─────────────────────────────────────────────────

def sep(char='─', width=72):
    print(muted(char * width))


def header(title, icon=None, subtitle=None):
    icon = icon or UI['section']
    print()
    print(f"  {accent(f'{icon}  {title}')}")
    sep('━')
    if subtitle:
        print(f"  {subtitle}")
        sep()


def select_one(options, prompt="请选择"):
    """单选，返回 0-based 索引；输入 0 返回 None（返回上一级）"""
    for i, opt in enumerate(options, 1):
        print(f"  {accent(f'{i:>2}.')} {opt}")
    print(f"  {muted(' 0.')} 返回上一级")
    while True:
        raw = read_input(f"{prompt} [0-{len(options)}]", prefix='> ')
        if raw == '0':
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        note(f"无效输入，请输入 0 到 {len(options)} 之间的数字。", 'warn')


def select_many(options, prompt="请选择（多个用逗号分隔，all=全选，0=返回）"):
    """多选，返回 0-based 索引列表；输入 0 返回 None（返回上一级）"""
    for i, opt in enumerate(options, 1):
        print(f"  {accent(f'{i:>2}.')} {opt}")
    print(f"\n  {icon_slot(UI['menu'], '36')} {prompt}")
    while True:
        raw = read_input("", prefix='> ')
        if raw == '0':
            return None
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
                note(f"无效输入: '{p}'，请重新输入。", 'warn')
                valid = False
                break
        if valid and indices:
            return indices
        elif valid:
            note("请至少选择一个选项。", 'warn')


def confirm(prompt):
    return read_input(f"{prompt} (y/n)", prefix='> ').lower() == 'y'


# ─── 分支报告 ────────────────────────────────────────────────────

REPORT_TRACKING_RE = re.compile(r"^\[DREO-MERGE\]\s+(\S+)\s+<-\s+(.+)$")
REPORT_MERGE_RE = re.compile(r"^Merge branch '(.+?)' into (.+)$")


def report_read_commits(*log_args):
    ok, output, err = run_git(
        'log',
        *log_args,
        '--pretty=format:%H%x1f%ad%x1f%s',
        '--date=iso-strict',
    )
    if not ok:
        raise RuntimeError(f"读取 git 日志失败: {err or output}")

    commits = []
    for line in output.splitlines():
        sha, timestamp, subject = line.split('\x1f', 2)
        commits.append({
            'sha': sha,
            'timestamp': datetime.fromisoformat(timestamp),
            'subject': subject,
        })
    return commits


def report_first_unique_commits(base, branch):
    return report_read_commits('--reverse', '--no-merges', f'{base}..{branch}')


def report_base_first_parent_commits(base):
    return report_read_commits('--reverse', '--first-parent', '--no-merges', base)


def report_merge_commits():
    return report_read_commits('--reverse', '--merges', '--all')


def report_tracking_commits():
    return [
        commit for commit in report_read_commits('--reverse', '--all')
        if REPORT_TRACKING_RE.match(commit['subject'])
    ]


def collect_report_events():
    base = get_master_branch() or get_current_branch()
    branches = get_local_branches()
    seen = set()
    events = []

    for commit in report_base_first_parent_commits(base):
        key = ('base', commit['sha'])
        if key in seen:
            continue
        seen.add(key)
        events.append({
            'timestamp': commit['timestamp'],
            'sha': commit['sha'],
            'kind': 'base_commit',
            'description': f"{base} 提交 {commit['subject']}",
            'branch': base,
            'source': '',
            'target': '',
        })

    for branch in branches:
        if branch == base or is_integration_branch(branch):
            continue
        for index, commit in enumerate(report_first_unique_commits(base, branch)):
            key = ('branch', branch, commit['sha'])
            if key in seen:
                continue
            seen.add(key)
            desc = (
                f"从 {base} 拉出 {branch}，并提交 {commit['subject']}"
                if index == 0 else
                f"{branch} 提交 {commit['subject']}"
            )
            events.append({
                'timestamp': commit['timestamp'],
                'sha': commit['sha'],
                'kind': 'branch_commit',
                'description': desc,
                'branch': branch,
                'source': '',
                'target': '',
            })

    for commit in report_merge_commits():
        match = REPORT_MERGE_RE.match(commit['subject'])
        if not match:
            continue
        source, target = match.groups()
        events.append({
            'timestamp': commit['timestamp'],
            'sha': commit['sha'],
            'kind': 'merge',
            'description': f"将 {source} 合入 {target}",
            'branch': '',
            'source': source,
            'target': target,
        })

    for commit in report_tracking_commits():
        match = REPORT_TRACKING_RE.match(commit['subject'])
        if not match:
            continue
        target, sources = match.groups()
        events.append({
            'timestamp': commit['timestamp'],
            'sha': commit['sha'],
            'kind': 'tracking',
            'description': f"写入追踪提交 {commit['subject']}",
            'branch': '',
            'source': sources,
            'target': target,
        })

    events.sort(key=lambda item: (item['timestamp'], item['sha'], item['kind']))
    return events


def mermaid_safe_period(timestamp_text):
    return timestamp_text.replace(':', '-')


def mermaid_safe_text(text):
    return text.replace(':', '：')


def build_report_sequence(events):
    rows = []
    for index, event in enumerate(events, 1):
        time_text = event['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
        rows.append(f"{index}. {time_text}：{event['description']}（{event['sha'][:7]}）")
    return rows


def build_report_timeline(events):
    grouped = defaultdict(list)
    for event in events:
        grouped[event['timestamp'].strftime('%Y-%m-%d %H:%M:%S')].append(event['description'])

    lines = [
        '```mermaid',
        'timeline',
        '    title 分支处理时间线',
    ]
    for timestamp_text, descriptions in grouped.items():
        first = True
        for description in descriptions:
            period = mermaid_safe_period(timestamp_text) if first else ' ' * len(mermaid_safe_period(timestamp_text))
            lines.append(f"    {period} : {mermaid_safe_text(description)}")
            first = False
    lines.append('```')
    return '\n'.join(lines)


def report_safe_node_id(name):
    sanitized = re.sub(r'[^A-Za-z0-9_]', '_', name)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f'n_{sanitized}'
    return sanitized


def build_report_flowchart(base, branches, events):
    lines = [
        '```mermaid',
        'flowchart LR',
    ]
    all_nodes = [base] + [branch for branch in branches if branch != base]
    for branch in all_nodes:
        lines.append(f'    {report_safe_node_id(branch)}["{branch}"]')

    added_edges = set()
    for event in events:
        if event['kind'] == 'branch_commit' and event['branch'] and event['description'].startswith(f"从 {base} 拉出 "):
            edge = (base, event['branch'], 'create')
            if edge not in added_edges:
                added_edges.add(edge)
                lines.append(f'    {report_safe_node_id(base)} -->|创建分支| {report_safe_node_id(event["branch"])}')
        elif event['kind'] == 'merge' and event['source'] and event['target']:
            edge = (event['source'], event['target'], event['sha'])
            if edge not in added_edges:
                added_edges.add(edge)
                lines.append(
                    f'    {report_safe_node_id(event["source"])} -->|{event["timestamp"].strftime("%H:%M:%S")} merge| {report_safe_node_id(event["target"])}'
                )

    lines.append('```')
    return '\n'.join(lines)


def build_tracking_section():
    commits = report_tracking_commits()
    if not commits:
        return ['- 未发现 `[DREO-MERGE]` 追踪提交。']

    lines = []
    for commit in commits:
        timestamp_text = commit['timestamp'].isoformat(sep=' ', timespec='seconds')
        lines.append(f"- {timestamp_text}  {commit['subject']} ({commit['sha'][:7]})")
    return lines


def build_branch_report():
    repo = Path.cwd().resolve()
    base = get_master_branch() or get_current_branch()
    current = get_current_branch()
    branches = get_local_branches()
    events = collect_report_events()
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines = [
        '# Git 分支合并报告',
        '',
        f"- 仓库路径：`{repo}`",
        f"- 生成时间：`{generated_at}`",
        f"- 基线分支：`{base}`",
        f"- 当前分支：`{current}`",
        '',
        '## 分支概览',
        '',
    ]
    for branch in branches:
        lines.append(f"- `{branch}`")

    lines.extend([
        '',
        '## 推断的处理顺序',
        '',
    ])
    lines.extend(build_report_sequence(events) or ['1. 未识别到可分析的提交记录。'])
    lines.extend([
        '',
        '## 时间线图',
        '',
        build_report_timeline(events),
        '',
        '## 分支流转图',
        '',
        build_report_flowchart(base, branches, events),
        '',
        '## 追踪提交',
        '',
    ])
    lines.extend(build_tracking_section())
    lines.append('')
    return '\n'.join(lines)


def generate_branch_report(output_path=None):
    output = Path(output_path) if output_path else Path.cwd() / 'branch_merge_report.md'
    output.write_text(build_branch_report(), encoding='utf-8')
    return output.resolve()


def generate_branch_report_menu():
    header("生成分支处理报告", icon=UI['report'])
    note("将根据当前仓库的提交历史、merge 记录和追踪提交生成 Markdown 报告。", 'info')
    output = generate_branch_report()
    note(f"报告已生成: {output}", 'success')
    note("报告包含：处理顺序、时间线图、分支流转图、追踪提交。", 'tip')


# ─── 冲突处理 ────────────────────────────────────────────────────

MERGE_TAG = '[DREO-MERGE]'


def write_tracking_commit(int_branch, branches):
    """写一条空提交，记录本次集成的所有开发分支，格式：
    [DREO-MERGE] {int_branch} <- branch1,branch2,...
    """
    msg = f"{MERGE_TAG} {int_branch} <- {','.join(branches)}"
    ok, _, err = run_git('commit', '--allow-empty', '-m', msg)
    if not ok:
        note(f"集成记录提交写入失败: {err}", 'warn')
        note("后续“已集成分支”识别可能不完整，请检查 git 用户信息或 hook 配置。", 'tip')
        return False
    return True


def handle_conflict(merging_branch, action_label="合并"):
    """引导用户解决合并冲突，返回是否最终成功"""
    header("合并冲突处理", icon=UI['conflict'])
    note(f"{action_label} [{merging_branch}] 时发生冲突。", 'error')
    print(f"\n  {icon_slot('🛠️', '36')} 请在另一个终端中执行以下步骤：")
    print("    1. 编辑冲突文件，删除所有 <<<<<<<, =======, >>>>>>> 标记")
    print("    2. git add <已解决的文件>")
    print("    3. 回到此工具，选择「继续合并」")

    while True:
        sep()
        print(f"  {accent(UI['conflict'] + ' 冲突处理')}")
        print(f"  {accent(' 1.')} 我已解决所有冲突，继续完成合并")
        print(f"  {accent(' 2.')} 放弃此次合并（git merge --abort）")
        choice = read_input("请选择 [1/2]", prefix='> ')

        if choice == '1':
            _, status, _ = run_git('status', '--porcelain')
            unresolved = [l for l in status.splitlines()
                          if l[:2] in ('UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD')]
            if unresolved:
                note("仍有未解决的冲突文件：", 'error')
                print_list([f.strip() for f in unresolved], icon='•')
                note("请解决全部冲突并 git add 后再继续。", 'warn')
                continue

            ok, _, err = run_git('commit', '--no-edit')
            if ok:
                note(f"冲突已解决，{action_label}完成: {merging_branch}", 'success')
                return True
            else:
                note(f"提交失败: {err}", 'error')
                note("请确认所有冲突文件均已 git add。", 'warn')

        elif choice == '2':
            run_git('merge', '--abort')
            note(f"已放弃{action_label}: {merging_branch}", 'warn')
            return False
        else:
            note("请输入 1 或 2。", 'warn')


def do_merge(source_branch, action_label="合并"):
    """将 source_branch 合并到当前分支，处理冲突。返回是否成功。"""
    current = get_current_branch()
    print(f"\n  {icon_slot(UI['merge'], '36')} {action_label} [{source_branch}] → [{current}] ...")
    ok, out, err = run_git('merge', '--no-ff', source_branch)
    if ok:
        note(f"{action_label}成功: {source_branch}", 'success')
        return True
    if 'CONFLICT' in out or 'CONFLICT' in err:
        run_git('rerere')

        # rerere 可能发生在 merge 阶段，也可能只更新工作区而不会自动 git add。
        for path in get_unmerged_files():
            if not has_conflict_markers(path):
                run_git('add', path)

        still_conflict = get_unmerged_files()
        if not still_conflict:
            commit_ok, _, commit_err = run_git('commit', '--no-edit')
            if commit_ok:
                note(f"rerere 自动重用了历史解决方案，{action_label}完成: {source_branch}", 'success')
                note("请检查自动解决的文件是否符合预期。", 'tip')
                return True
            note(f"自动提交失败: {commit_err}", 'error')
        return handle_conflict(source_branch, action_label=action_label)
    note(f"{action_label}失败: {err or out}", 'error')
    return False


# ─── 功能 1：创建开发分支 ─────────────────────────────────────────

def create_feature_branch():
    header("创建开发分支（从 master）", icon=UI['create_feature'])

    base = get_master_branch()
    if not base:
        note("未找到 master / main 分支，请先初始化仓库。", 'error')
        return

    # 选择分支类型
    print(f"  {icon_slot(UI['type'], '36')} 请选择分支类型：")
    type_idx = select_one(['feature  — 新功能开发', 'bugfix   — 缺陷修复'], "分支类型")
    if type_idx is None:
        return False
    branch_type = ['feature', 'bugfix'][type_idx]

    # 输入分支名称
    existing = get_local_branches()
    date_suffix = today_str()
    while True:
        name = read_text_input(
            f"分支名称（最终: {branch_type}_<名称>_{date_suffix}，直接回车返回）",
            prefix='> ',
        )
        if not name:
            return False
        if any(c in name for c in ' ~^:?*[\\'):
            note("包含非法字符，请重新输入。", 'warn')
            continue
        branch_name = f"{branch_type}_{name}_{date_suffix}"
        if branch_name in existing:
            note(f"分支 '{branch_name}' 已存在，请换一个名称。", 'error')
            continue
        break

    # 切换到 base 并更新
    if not checkout_and_update_base(base):
        return

    # 创建新分支
    ok, _, err = run_git('checkout', '-b', branch_name)
    if ok:
        note(f"已创建并切换到: {branch_name}  (基于 {base})", 'success')
    else:
        note(f"创建分支失败: {err}", 'error')


# ─── 集成分支公共：选分支并合并 ──────────────────────────────────

def _merge_into_integration(int_branch, candidates, action_name="合并"):
    """将 candidates 中用户选中的分支合并到 int_branch，写追踪提交。"""
    sorted_c = sort_branches_by_date(candidates, limit=len(candidates))
    total = len(candidates)
    print(f"\n  {icon_slot(UI['feature_branch'], '36')} 选择要{action_name}到 [{int_branch}] 的开发分支（共 {total} 个，已按时间倒序排序）：")
    indices = select_many(sorted_c)
    if indices is None:
        return False
    selected = [sorted_c[i] for i in indices]

    print(f"\n  {icon_slot(UI['merge'], '36')} 将{action_name}以下分支 → [{int_branch}]：")
    print_list(selected)
    if not confirm(f"确认执行{action_name}？"):
        note("已取消。", 'warn')
        return False

    ok, _, err = run_git('checkout', int_branch)
    if not ensure_git_success(ok, err, f"切换到 [{int_branch}]"):
        return False

    succeeded, failed = [], []
    for branch in selected:
        if do_merge(branch):
            succeeded.append(branch)
        else:
            failed.append(branch)

    if succeeded:
        write_tracking_commit(int_branch, succeeded)

    rows = []
    if succeeded:
        rows.append(('✅', f"成功 ({len(succeeded)}): " + ", ".join(succeeded)))
    if failed:
        rows.append(('❌', f"跳过 ({len(failed)}): " + ", ".join(failed)))
    rows.append((UI['integration_branch'], f"当前所在集成分支: {get_current_branch()}"))
    summary_block(f"{action_name}结果汇总", rows)


def checkout_and_update_base(base):
    print(f"\n  {icon_slot(UI['checkout'], '36')} 切换到 {base}，同步最新代码...")
    ok, _, err = run_git('checkout', base)
    if not ensure_git_success(ok, err, f"切换到 {base}"):
        return False
    ok, _, _ = run_git('pull', 'origin', base)
    if not ok:
        note(f"拉取远端失败，使用本地 {base} 继续。", 'warn')
    return True


def sync_base_into_integration(int_branch, base):
    ok, _, err = run_git('checkout', int_branch)
    if not ensure_git_success(ok, err, f"切换到 [{int_branch}]"):
        return 'failed'

    _, ahead, _ = run_git('rev-list', '--count', f'{int_branch}..{base}')
    new_commits = int(ahead) if ahead.isdigit() else 0
    if new_commits == 0:
        note(f"[{int_branch}] 已包含最新 {base}，无需同步主干代码。", 'tip')
        return 'skipped'

    print(f"\n  {icon_slot(UI['sync'], '36')} 检测到 {base} 有 {new_commits} 个新提交，先同步到 [{int_branch}] ...")
    if do_merge(base, action_label="同步主干代码"):
        return 'success'
    return 'failed'


# ─── 功能 2.1：创建集成分支 ───────────────────────────────────────

def create_integration_branch():
    header("2.1  创建集成分支", icon=UI['create_integration'])

    feature_branches = get_feature_branches()
    if not feature_branches:
        note("没有找到开发分支（feature_ / bugfix_ 开头）。", 'error')
        note("请先使用「1. 创建开发分支」。", 'tip')
        return

    base = get_master_branch()
    if not base:
        note("未找到 master / main 分支。", 'error')
        return

    print(f"  {icon_slot(UI['type'], '36')} 请选择集成分支用途：")
    env_idx = select_one(
        ['dev     — 测试/日常环境集成', 'release — 预发/生产环境集成'],
        "集成用途"
    )
    if env_idx is None:
        return False
    env_prefix = ['dev', 'release'][env_idx]

    date_suffix = today_str()
    while True:
        version = read_text_input(
            f"版本号或名称（将创建: {env_prefix}_<版本>_{date_suffix}，直接回车返回）",
            prefix='> ',
        )
        if not version:
            return False
        break

    int_branch = f"{env_prefix}_{version}_{date_suffix}"
    if int_branch in get_local_branches():
        note(f"分支 '{int_branch}' 已存在，请使用「2.3 添加新的开发分支」功能。", 'error')
        return

    print(f"\n  {icon_slot(UI['build'], '36')} 从最新 {base} 创建集成分支 {int_branch}...")
    if not checkout_and_update_base(base):
        return
    ok, _, err = run_git('checkout', '-b', int_branch)
    if not ok:
        note(f"创建失败: {err}", 'error')
        return
    note(f"已创建集成分支: {int_branch}", 'success')

    _merge_into_integration(int_branch, feature_branches, action_name="合并")


# ─── 功能 2.3：添加新的开发分支到集成分支 ────────────────────────

def add_branches_to_integration():
    header("2.3  添加新的开发分支到集成分支", icon='➕')

    int_branches = get_integration_branches()
    if not int_branches:
        note("没有找到集成分支，请先创建。", 'error')
        return

    print(f"  {icon_slot(UI['select'], '36')} 请选择目标集成分支：")
    idx = select_one(int_branches)
    if idx is None:
        return False
    int_branch = int_branches[idx]

    # 已集成的分支
    already = set(get_merged_feature_branches(int_branch))
    all_features = get_feature_branches()
    candidates = [b for b in all_features if b not in already]

    if not candidates:
        note(f"所有开发分支均已集成到 [{int_branch}]，无可添加的分支。", 'tip')
        return

    if already:
        print(f"\n  {icon_slot(UI['pinned'], '36')} 已集成: {', '.join(already)}")

    return _merge_into_integration(int_branch, candidates, action_name="添加")


# ─── 功能 3：同步更新集成分支 ────────────────────────────────────

def get_merged_feature_branches(int_branch):
    """通过 DREO-MERGE 标志位查找曾被集成到该分支的所有开发分支
    格式: [DREO-MERGE] {int_branch} <- branch1,branch2,...
    """
    _, log, _ = run_git('log', '--all', '-F', f'--grep={MERGE_TAG} {int_branch} <-',
                        '--pretty=format:%s')
    seen, result = set(), []
    for line in log.splitlines():
        if '<-' not in line:
            continue
        branch_list = line.split('<-', 1)[-1].strip()
        for b in branch_list.split(','):
            b = b.strip()
            if b and b not in seen:
                seen.add(b)
                result.append(b)
    return result


def update_integration_branch():
    header("同步更新集成分支（先同步主干，再合并开发分支新增提交）", icon=UI['sync'])

    int_branches = get_integration_branches()
    if not int_branches:
        note("没有找到集成分支（dev_ / release_ 开头）。", 'error')
        return

    # 选择要更新的集成分支
    print(f"  {icon_slot(UI['select'], '36')} 请选择要同步更新的集成分支：")
    idx = select_one(int_branches)
    if idx is None:
        return False
    int_branch = int_branches[idx]

    # 通过标志位查找曾被集成的开发分支
    print(f"\n  {icon_slot('🔎', '36')} 正在查找 [{int_branch}] 的集成记录...")
    merged_branches = get_merged_feature_branches(int_branch)

    if not merged_branches:
        note("未找到任何集成记录。", 'error')
        note(f"仅识别通过本工具合并（含 {MERGE_TAG} 标志）的分支。", 'tip')
        return

    local_branches = get_local_branches()
    existing = [b for b in merged_branches if b in local_branches]
    missing  = [b for b in merged_branches if b not in local_branches]

    print(f"\n  {icon_slot(UI['records'], '36')} 检测到以下开发分支曾被集成到 [{int_branch}]：")
    branch_lines = []
    for b in merged_branches:
        tag = '' if b in local_branches else ' [本地已删除，将跳过]'
        branch_lines.append(f"{b}{tag}")
    print_list(branch_lines)

    if not existing:
        note("所有已集成的开发分支在本地均不存在，无法同步。", 'error')
        return
    if missing:
        note(f"{len(missing)} 个分支本地不存在，将跳过。", 'warn')

    print(f"\n  {icon_slot(UI['sync'], '36')} 将对以上 {len(existing)} 个分支执行 re-merge，只引入新增提交。")
    if not confirm("确认同步？"):
        note("已取消。", 'warn')
        return False

    base = get_master_branch()
    if not base:
        note("未找到 master / main 分支。", 'error')
        return

    base_sync_status = sync_base_into_integration(int_branch, base)
    if base_sync_status == 'failed':
        summary_block("同步结果汇总", [
            ('❌', f"主干同步失败，已停止后续开发分支同步: {base} -> {int_branch}"),
            (UI['integration_branch'], f"当前所在集成分支: {get_current_branch()}"),
        ])
        return False

    succeeded, failed, skipped = [], [], []
    for branch in existing:
        _, ahead, _ = run_git('rev-list', '--count', f'{int_branch}..{branch}')
        new_commits = int(ahead) if ahead.isdigit() else 0

        if new_commits == 0:
            print(f"\n  {icon_slot(UI['skip'], '33')} [{branch}] 无新增提交，跳过。")
            skipped.append(branch)
            continue

        print(f"\n  {icon_slot(UI['merge'], '36')} [{branch}] 有 {new_commits} 个新提交，执行合并...")
        if do_merge(branch):
            succeeded.append(branch)
        else:
            failed.append(branch)

    # 同步后更新追踪提交（记录本次实际同步的分支）
    if succeeded:
        write_tracking_commit(int_branch, succeeded)

    rows = []
    if base_sync_status == 'success':
        rows.append(('🔄', f"已同步最新主干代码: {base}"))
    elif base_sync_status == 'skipped':
        rows.append(('📌', f"主干已是最新: {base}"))
    if succeeded:
        rows.append(('✅', f"已同步 ({len(succeeded)}): " + ", ".join(succeeded)))
    if skipped:
        rows.append(('⏭️', f"无变更 ({len(skipped)}): " + ", ".join(skipped)))
    if failed:
        rows.append(('❌', f"失败   ({len(failed)}): " + ", ".join(failed)))
    rows.append((UI['integration_branch'], f"当前所在集成分支: {get_current_branch()}"))
    summary_block("同步结果汇总", rows)


# ─── 功能 4：删除分支 ────────────────────────────────────────────

def delete_branches(include_remote=False):
    mode = "本地 + 云端" if include_remote else "仅本地"
    header(f"删除分支（{mode}）", icon=UI['delete'])

    all_branches = get_local_branches()
    current = get_current_branch()
    base = get_master_branch()

    # 排除当前分支和 master/main
    protected = {current, base}
    deletable = [b for b in all_branches if b not in protected]

    if not deletable:
        note("没有可删除的分支。", 'error')
        note(f"当前分支 [{current}] 和 [{base}] 受保护，不可删除。", 'tip')
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
        print(f"  {accent(UI['menu'] + ' ' + group_name)}")
        for b in branches:
            ordered.append(b)
            print(f"  {accent(f'{len(ordered):>2}.')} {b}")

    note(f"当前分支 [{current}] 和 [{base}] 受保护，不在列表中。", 'tip')
    print(f"  {icon_slot(UI['delete'], '36')} 选择要删除的分支（多个用逗号分隔，all=全选，0=返回上一级）")
    selected_indices = []
    while True:
        raw = read_input("", prefix='> ')
        if raw == '0':
            return False
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
                note(f"无效输入: '{p}'，请重新输入。", 'warn')
                valid = False
                break
        if valid and indices:
            selected_indices = indices
            break
        elif valid:
            note("请至少选择一个选项。", 'warn')
    selected = [ordered[i] for i in selected_indices]

    print(f"\n  {icon_slot(UI['records'], '36')} 将删除以下 {len(selected)} 个本地分支：")
    print_list(selected)
    note("此操作不可恢复，请确认分支代码已合并或不再需要。", 'warn')
    if not confirm("确认删除？"):
        note("已取消。", 'warn')
        return False

    succeeded, failed = [], []
    for branch in selected:
        ok, _, err = run_git('branch', '-d', branch)
        if not ok:
            note(f"[{branch}] 包含未合并的提交，无法安全删除。", 'error')
            if confirm(f"强制删除 [{branch}]？（提交将丢失）"):
                ok, _, err = run_git('branch', '-D', branch)
            else:
                failed.append(branch)
                print(f"  {icon_slot(UI['skip'], '33')} 已跳过: {branch}")
                continue

        if ok:
            note(f"本地已删除: {branch}", 'success')
            if include_remote:
                rok, _, rerr = run_git('push', 'origin', '--delete', branch)
                if rok:
                    note(f"远端已删除: {branch}", 'success')
                else:
                    note(f"远端删除失败（可能不存在）: {rerr}", 'warn')
            succeeded.append(branch)
        else:
            failed.append(branch)
            note(f"删除失败: {err}", 'error')

    rows = []
    if succeeded:
        rows.append(('✅', f"已删除 ({len(succeeded)}): " + ", ".join(succeeded)))
    if failed:
        rows.append(('❌', f"跳过   ({len(failed)}): " + ", ".join(failed)))
    summary_block("删除结果汇总", rows)


# ─── 功能 5：合并发布分支回 master ───────────────────────────────

def merge_to_master():
    header("合并发布分支回 master（基线写入）", icon=UI['release'])

    release_branches = [b for b in get_integration_branches() if b.startswith('release_')]
    if not release_branches:
        note("没有找到发布分支（release_ 开头）。", 'error')
        return

    base = get_master_branch()
    if not base:
        note("未找到 master / main 分支。", 'error')
        return

    print(f"  {icon_slot(UI['select'], '36')} 选择要合并到 [{base}] 的发布分支：")
    idx = select_one(release_branches)
    if idx is None:
        return False
    release_branch = release_branches[idx]

    print(f"\n  {icon_slot(UI['merge'], '36')} 操作：[{release_branch}] → [{base}]")
    if not confirm("确认执行？"):
        note("已取消。", 'warn')
        return False

    # 切换到 master
    ok, _, err = run_git('checkout', base)
    if not ok:
        note(f"切换到 {base} 失败: {err}", 'error')
        return

    ok, _, _ = run_git('pull', 'origin', base)
    if not ok:
        note(f"拉取远端失败，使用本地 {base} 继续。", 'warn')

    if do_merge(release_branch):
        _, log, _ = run_git('log', '--oneline', '-5')
        note(f"[{release_branch}] 已成功合并到 {base}！", 'success')
        print(f"\n  {icon_slot(UI['records'], '36')} 最近提交记录：")
        print_list(log.splitlines())
        note(f"推送到远端: git push origin {base}", 'tip')
    else:
        note("合并失败或已放弃。", 'error')


# ─── 菜单系统 ────────────────────────────────────────────────────

def show_status():
    current = get_current_branch()
    features = get_feature_branches()
    integrations = get_integration_branches()
    branch_text = paint(current, '1', '37')
    print()
    print(f"  {icon_slot(UI['current_branch'], '32')} 当前分支: {branch_text}"
          f"   {icon_slot(UI['feature_branch'], '36')} 开发分支: {len(features)}"
          f"   {icon_slot(UI['integration_branch'], '35')} 集成分支: {len(integrations)}")


def run_submenu(title, items):
    """
    通用二级菜单。items: [(label, fn), ...]，fn=None 表示返回。
    返回 True 表示正常退出子菜单，False 表示用户选择返回。
    """
    keys = [str(i) for i in range(1, len(items) + 1)] + ['0']
    while True:
        show_status()
        sep()
        print(f"  {accent(UI['menu'] + ' ' + title)}")
        for i, (label, _) in enumerate(items, 1):
            print(f"  {accent(f'{i:>2}.')} {label}")
        print(f"  {muted(' 0.')} 返回上一级")
        sep()

        choice = read_input("", prefix='> ')
        if choice == '0':
            return False
        if choice in keys[:-1]:
            idx = int(choice) - 1
            print()
            result = items[idx][1]()
            if result is not False:
                read_input("按回车键继续", prefix='')
        else:
            note(f"无效输入，请输入 0-{len(items)}。", 'warn')


def menu_integration():
    return run_submenu("集成分支管理", [
        ("创建集成分支",                   create_integration_branch),
        ("更新集成分支（同步已集成分支新提交）", update_integration_branch),
        ("添加新的开发分支到集成分支",        add_branches_to_integration),
    ])


def menu_delete():
    return run_submenu("删除分支", [
        ("仅本地",       lambda: delete_branches(include_remote=False)),
        ("本地 + 云端",  lambda: delete_branches(include_remote=True)),
    ])


def main():
    print()
    sep('━')
    print(f"  {accent(UI['app'] + '  Dreo 分支管理工具')}")
    print("  开发分支用于功能开发与缺陷修复，集成分支用于联调、验证与发布。")
    print("  建议在干净工作区中使用；重复冲突场景建议开启 rerere。")
    sep('━')

    check_git_repo()
    check_rerere()

    main_items = [
        ("创建开发分支（feature / bugfix）", create_feature_branch),
        ("集成分支管理（测试 / 生产）",      menu_integration),
        ("合并集成分支到 master",            merge_to_master),
        ("生成分支处理报告",                 generate_branch_report_menu),
        ("删除分支",                        menu_delete),
    ]

    while True:
        show_status()
        sep()
        print(f"  {accent(UI['menu'] + ' 主菜单')}")
        for i, (label, _) in enumerate(main_items, 1):
            print(f"  {accent(f'{i:>2}.')} {label}")
        print(f"  {muted(' 0.')} 退出")
        sep()

        choice = read_input("", prefix='> ')
        if choice == '0':
            print(f"\n  {icon_slot('👋', '36')} 再见！\n")
            sys.exit(0)
        elif choice.isdigit() and 1 <= int(choice) <= len(main_items):
            print()
            result = main_items[int(choice) - 1][1]()
            if result is not False:
                read_input("按回车键返回主菜单", prefix='')
        else:
            note(f"无效输入，请输入 0-{len(main_items)}。", 'warn')


def run():
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n  {icon_slot(UI['interrupt'], '33')} 已中断，退出脚本。\n")
        sys.exit(130)


if __name__ == '__main__':
    run()
