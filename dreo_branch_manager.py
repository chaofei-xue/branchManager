#!/usr/bin/env python3
"""
Dreo 分支管理工具
"""

import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from functools import lru_cache
from html import escape
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
PAGE_SIZE = 20

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

MANAGED_BRANCH_PATTERNS = {
    'feature': re.compile(r'^(feature|bugfix)_[^/]+_\d{8}$'),
    'integration': re.compile(r'^(dev|release)_[^/]+_\d{8}$'),
}

MERGE_TAG = '[DREO-MERGE]'

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

_BRANCH_NAME_INVALID_CHARS = set(' ~^:?*[\\')


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


def terminal_link(path, label=None):
    try:
        target = Path(path).resolve().as_uri()
    except ValueError:
        return str(path)

    text = label or str(path)
    if not sys.stdout.isatty():
        return str(path)
    hyperlink = f"\033]8;;{target}\033\\{text}\033]8;;\033\\"
    return paint(hyperlink, '4', '34')


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
    text = _ANSI_RE.sub('', text)
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


def _drain_escape_sequence(fd):
    """读取并丢弃 ESC 后续字节，非阻塞方式避免挂起。"""
    import select as _select
    for _ in range(8):
        ready, _, _ = _select.select([fd], [], [], 0.05)
        if not ready:
            break
        os.read(fd, 1)


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
                _drain_escape_sequence(fd)
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


def read_menu_input(prompt="", prefix='> '):
    if prompt:
        print(f"\n  {prompt}")
    if not (sys.stdin.isatty() and sys.stdout.isatty() and termios and tty):
        return input(f"  {prefix}").strip()

    import select as _select

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
                sys.stdout.write("\n")
                sys.stdout.flush()
                return ''.join(chars).strip()
            if chunk == b'\x03':
                raise KeyboardInterrupt
            if chunk in (b'\x7f', b'\b'):
                if chars:
                    chars.pop()
                    render()
                continue
            if chunk == b'\x1b':
                ready, _, _ = _select.select([fd], [], [], 0.05)
                if ready:
                    seq = os.read(fd, 1)
                    if seq == b'[':
                        ready2, _, _ = _select.select([fd], [], [], 0.05)
                        if ready2:
                            code = os.read(fd, 1)
                            sys.stdout.write("\r\033[2K\n")
                            sys.stdout.flush()
                            if code == b'A':
                                return '__UP__'
                            if code == b'B':
                                return '__DOWN__'
                    _drain_escape_sequence(fd)
                render()
                continue

            text = decoder.decode(chunk, final=False)
            if text:
                chars.append(text)
                render()
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


def has_origin_remote():
    ok, _, _ = run_git('remote', 'get-url', 'origin')
    return ok


def offer_push_branch(branch, set_upstream=False, prompt=None):
    if not has_origin_remote():
        note("未检测到 origin 远端，已跳过推送。", 'tip')
        return False

    message = prompt or f"是否立即推送 [{branch}] 到远端？"
    if not confirm(message):
        note("已跳过远端推送。", 'warn')
        return False

    if set_upstream:
        ok, _, err = run_git('push', '-u', 'origin', branch)
    else:
        ok, _, err = run_git('push', 'origin', branch)

    if ok:
        note(f"已推送到远端: {branch}", 'success')
        return True

    note(f"推送失败: {err}", 'error')
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


def get_remote_branches():
    if not has_origin_remote():
        return []
    ok, output, _ = run_git('branch', '-r', '--format=%(refname:short)')
    if not ok:
        return []

    branches = []
    for line in output.splitlines():
        name = line.strip()
        if not name or name.endswith('/HEAD'):
            continue
        if name.startswith('origin/'):
            branches.append(name.split('/', 1)[1])
    return branches


def is_managed_feature_branch(branch):
    return bool(MANAGED_BRANCH_PATTERNS['feature'].match(branch))


def is_managed_integration_branch(branch):
    return bool(MANAGED_BRANCH_PATTERNS['integration'].match(branch))


def refresh_remote_refs():
    if not has_origin_remote():
        return False
    ok, _, err = run_git('fetch', 'origin', '--prune')
    if not ok:
        note(f"拉取远端分支引用失败，继续使用本地缓存的远端信息: {err}", 'warn')
        return False
    return True


def has_local_branch(branch):
    return branch in set(get_local_branches())


def has_remote_branch(branch):
    return branch in set(get_remote_branches())


def branch_available(branch):
    return has_local_branch(branch) or has_remote_branch(branch)


def branch_display_name(branch):
    if not has_local_branch(branch) and has_remote_branch(branch):
        return f"{branch}  [远端]"
    return branch


def branch_location_label(branch):
    local = has_local_branch(branch)
    remote = has_remote_branch(branch)
    if local and remote:
        return '本地 + 远端'
    if local:
        return '仅本地'
    if remote:
        return '仅远端'
    return '不存在'


def format_branch_with_location(branch):
    return f"{branch}  [{branch_location_label(branch)}]"


def branch_counts(prefixes):
    local = {
        b for b in get_local_branches()
        if b.startswith(prefixes)
        and (is_managed_feature_branch(b) if prefixes == ('feature_', 'bugfix_') else is_managed_integration_branch(b))
    }
    remote = {
        b for b in get_remote_branches()
        if b.startswith(prefixes)
        and (is_managed_feature_branch(b) if prefixes == ('feature_', 'bugfix_') else is_managed_integration_branch(b))
    }
    return {
        'local': len(local),
        'remote': len(remote),
        'total': len(local | remote),
    }


def ensure_local_branch(branch, checkout=False):
    if has_local_branch(branch):
        if checkout:
            ok, _, err = run_git('checkout', branch)
            return ensure_git_success(ok, err, f"切换到 [{branch}]")
        return True

    if not has_remote_branch(branch):
        note(f"未找到分支: {branch}", 'error')
        return False

    if checkout:
        ok, _, err = run_git('checkout', '-b', branch, '--track', f'origin/{branch}')
        if ensure_git_success(ok, err, f"从远端创建并切换到 [{branch}]"):
            note(f"已从远端创建本地跟踪分支: {branch}", 'success')
            return True
        return False

    ok, _, err = run_git('branch', '--track', branch, f'origin/{branch}')
    if ensure_git_success(ok, err, f"从远端创建本地分支 [{branch}]"):
        note(f"已从远端创建本地跟踪分支: {branch}", 'success')
        return True
    return False


def get_feature_branches():
    branches = {
        b for b in get_local_branches() + get_remote_branches()
        if is_managed_feature_branch(b)
    }
    return sort_branches_by_date(list(branches), limit=len(branches))


def get_integration_branches():
    branches = {
        b for b in get_local_branches() + get_remote_branches()
        if is_managed_integration_branch(b)
    }
    return sort_branches_by_date(list(branches), limit=len(branches))


def sort_branches_by_date(branches, limit=10):
    """按分支名末尾的 yyyyMMdd 日期倒序排序，最多返回 limit 条"""
    def extract_date(b):
        suffix = b.rsplit('_', 1)[-1]
        return suffix if (len(suffix) == 8 and suffix.isdigit()) else '00000000'
    return sorted(branches, key=extract_date, reverse=True)[:limit]


def get_master_branch():
    branches = set(get_local_branches() + get_remote_branches())
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


def page_window(options, page, page_size=PAGE_SIZE):
    total_pages = max((len(options) - 1) // page_size + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = min(start + page_size, len(options))
    return page, total_pages, start, end, options[start:end]


def select_one(options, prompt="请选择"):
    """单选，返回 0-based 索引；输入 0 返回 None（返回上一级）"""
    if not options:
        note("没有可选项。", 'warn')
        return None

    page = 0
    while True:
        page, total_pages, start, end, window = page_window(options, page)
        for i, opt in enumerate(window, start + 1):
            print(f"  {accent(f'{i:>2}.')} {opt}")
        print(f"  {muted(' 0.')} 返回上一级")
        if len(options) > PAGE_SIZE:
            print(f"  {muted(f' 当前第 {page + 1}/{total_pages} 页，方向键上/下翻页；输入编号后回车确认')}")

        raw = read_menu_input(f"{prompt} [0-{len(options)}]", prefix='> ') if len(options) > PAGE_SIZE else read_input(f"{prompt} [0-{len(options)}]", prefix='> ')
        if raw == '0':
            return None
        if raw == '__DOWN__':
            page = 0 if page + 1 >= total_pages else page + 1
            continue
        if raw == '__UP__':
            page = total_pages - 1 if page == 0 else page - 1
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        note(f"无效输入，请输入 0 到 {len(options)} 之间的数字。", 'warn')


def select_many(options, prompt="请选择（多个用逗号分隔，all=全选，0=返回）", auto_confirm_single=False):
    """多选，返回 0-based 索引列表；输入 0 返回 None（返回上一级）"""
    if not options:
        note("没有可选项。", 'warn')
        return None

    page = 0
    selected = []
    while True:
        page, total_pages, start, end, window = page_window(options, page)
        for i, opt in enumerate(window, start + 1):
            marker = '●' if (i - 1) in selected else '○'
            print(f"  {accent(f'{i:>2}.')} {marker} {opt}")
        print(f"\n  {icon_slot(UI['menu'], '36')} {prompt}")
        if len(options) > PAGE_SIZE:
            if auto_confirm_single:
                print(f"  {muted(f' 当前第 {page + 1}/{total_pages} 页，方向键上/下翻页；输入单个编号后回车直接确认；多个编号用逗号分隔')}")
            else:
                print(f"  {muted(f' 当前第 {page + 1}/{total_pages} 页，方向键上/下翻页；输入编号后回车选择；直接回车确认')}")
        elif selected:
            print(f"  {muted(f' 已选择 {len(selected)} 项，直接回车确认')}")

        raw = read_menu_input("", prefix='> ') if len(options) > PAGE_SIZE else read_input("", prefix='> ')
        if raw == '0':
            return None
        if raw.lower() == 'all':
            return list(range(len(options)))
        if raw == '__DOWN__':
            page = 0 if page + 1 >= total_pages else page + 1
            continue
        if raw == '__UP__':
            page = total_pages - 1 if page == 0 else page - 1
            continue
        if raw == '':
            if selected:
                return selected
            note("请至少选择一个选项。", 'warn')
            continue

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
            if auto_confirm_single and not selected and len(indices) == 1 and ',' not in raw:
                return indices
            if len(options) <= PAGE_SIZE:
                return indices
            for idx in indices:
                if idx in selected:
                    selected.remove(idx)
                else:
                    selected.append(idx)
            selected.sort()
        elif valid:
            note("请至少选择一个选项。", 'warn')


def confirm(prompt):
    return read_input(f"{prompt} (y/n)", prefix='> ').lower() == 'y'


# ─── 分支报告 ────────────────────────────────────────────────────

REPORT_TRACKING_RE = re.compile(r"^\[DREO-MERGE\]\s+(\S+)\s+<-\s+(.+)$")
REPORT_MERGE_RE = re.compile(r"^Merge branch '(.+?)' into (.+)$")


def parse_git_iso_datetime(value):
    value = value.strip()
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    return datetime.fromisoformat(value)


def report_repo_key():
    return str(Path.cwd())


@lru_cache(maxsize=None)
def report_branch_sets(repo_key):
    local = tuple(sorted(get_local_branches()))
    remote = tuple(sorted(get_remote_branches()))
    return set(local), set(remote)


@lru_cache(maxsize=None)
def report_branch_head_map(repo_key):
    branch_map = {}
    local_branches, remote_branches = report_branch_sets(repo_key)
    all_branches = sorted(local_branches | remote_branches)
    for branch in all_branches:
        ref = branch if branch in local_branches else f'origin/{branch}'
        ok, output, _ = run_git('rev-parse', ref)
        if ok:
            branch_map[branch] = output.strip()
    return branch_map


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
            'timestamp': parse_git_iso_datetime(timestamp),
            'subject': subject,
        })
    return commits


def report_read_commits_with_parents(*log_args):
    ok, output, err = run_git(
        'log',
        *log_args,
        '--pretty=format:%H%x1f%P%x1f%ad%x1f%s',
        '--date=iso-strict',
    )
    if not ok:
        raise RuntimeError(f"读取 git 日志失败: {err or output}")

    commits = []
    for line in output.splitlines():
        sha, parents_text, timestamp, subject = line.split('\x1f', 3)
        commits.append({
            'sha': sha,
            'parents': [item for item in parents_text.split() if item],
            'timestamp': parse_git_iso_datetime(timestamp),
            'subject': subject,
        })
    return commits


def report_ref_name(branch):
    local_branches, remote_branches = report_branch_sets(report_repo_key())
    if branch in local_branches:
        return branch
    if branch in remote_branches:
        return f'origin/{branch}'
    return branch


def report_normalize_creation_source(branch, base, source):
    if not source:
        return None
    source = source.strip()
    if source == 'HEAD':
        return 'HEAD'
    if source.startswith('origin/'):
        source = source.split('/', 1)[1]
    if source == branch:
        return None
    return source


def report_resolve_creation_source_from_sha(branch, base, sha):
    branch_heads = report_branch_head_map(report_repo_key())
    candidates = []
    for candidate, head_sha in branch_heads.items():
        if candidate == branch:
            continue
        if head_sha.startswith(sha):
            candidates.append(candidate)
    if not candidates:
        return base
    if len(candidates) == 1:
        return candidates[0]
    if base in candidates:
        return base
    return candidates[0]


@lru_cache(maxsize=None)
def _report_branch_source(repo_key, branch, base):
    entry = _report_branch_creation_reflog(repo_key, branch)
    if not entry:
        return None, None
    source = report_normalize_creation_source(branch, base, entry.get('source'))
    if source == 'HEAD':
        source = report_resolve_creation_source_from_sha(branch, base, entry['sha'])
    return source, entry


def report_branch_source(branch, base):
    return _report_branch_source(report_repo_key(), branch, base)


def report_unique_commits_from_source(source, base, branch):
    compare_base = source or base
    return report_first_unique_commits(compare_base, branch)


def report_first_unique_commits(base, branch):
    return report_read_commits('--reverse', '--no-merges', f'{report_ref_name(base)}..{report_ref_name(branch)}')


def report_base_first_parent_commits(base):
    return report_read_commits('--reverse', '--first-parent', '--no-merges', base)


def report_merge_commits():
    return report_read_commits('--reverse', '--merges', '--all')


def report_tracking_commits():
    return [
        commit for commit in report_read_commits('--reverse', '--all')
        if REPORT_TRACKING_RE.match(commit['subject'])
    ]


def report_merge_base(base, branch):
    ok, output, _ = run_git('merge-base', report_ref_name(base), report_ref_name(branch))
    return output.strip() if ok and output.strip() else None


def report_commit_descends_from(start_sha, commit_sha):
    if not start_sha or commit_sha == start_sha:
        return True
    ok, _, _ = run_git('merge-base', '--is-ancestor', start_sha, commit_sha)
    return ok


def report_sha_matches_branch_line(base, sha):
    base_ref = report_ref_name(base)
    return report_commit_descends_from(sha, base_ref) or report_commit_descends_from(base_ref, sha)


def report_start_commit(base, branches):
    candidate_bases = []
    for branch in branches:
        if branch == base:
            continue
        merge_base = report_merge_base(base, branch)
        if merge_base:
            candidate_bases.append(merge_base)

    if not candidate_bases:
        return None

    for commit in report_base_first_parent_commits(base):
        if commit['sha'] in candidate_bases:
            return commit
    return None


def report_commit_by_sha(sha):
    commits = report_read_commits(sha, '--max-count=1')
    return commits[0] if commits else None


@lru_cache(maxsize=None)
def _report_branch_creation_reflog(repo_key, branch):
    ok, output, err = run_git('reflog', 'show', '--date=iso-strict', branch)
    if not ok:
        return None

    entries = []
    for line in output.splitlines():
        match = re.match(r'^([0-9a-f]+)\s+.+@\{(.+?)\}:\s+(.+)$', line)
        if not match:
            continue
        sha, timestamp_text, action = match.groups()
        source = None
        action_match = re.match(r'^branch: Created from (.+)$', action)
        if action_match:
            source = action_match.group(1).strip()
        entries.append({
            'sha': sha,
            'timestamp': parse_git_iso_datetime(timestamp_text),
            'action': action,
            'source': source,
        })

    for entry in reversed(entries):
        if entry['action'].startswith('branch: Created from'):
            return entry
    return entries[-1] if entries else None


def report_branch_creation_reflog(branch):
    return _report_branch_creation_reflog(report_repo_key(), branch)


def report_current_branch_merge_commits(current, start_sha):
    revision = f'{start_sha}..{current}' if start_sha else current
    return report_read_commits_with_parents('--reverse', '--merges', '--first-parent', revision)


def report_current_branch_no_merge_commits(current, start_sha):
    revision = f'{start_sha}..{current}' if start_sha else current
    return report_read_commits('--reverse', '--first-parent', '--no-merges', revision)


def report_resolve_parent_source(base, current, parent_sha):
    if report_sha_matches_branch_line(base, parent_sha):
        return base

    candidates = []
    for branch, head_sha in report_branch_head_map(report_repo_key()).items():
        if branch in (base, current):
            continue
        if head_sha == parent_sha:
            candidates.append(branch)
    return candidates[0] if candidates else None


def report_tracking_commits_for_branch(branch, start_sha=None):
    if branch and not is_integration_branch(branch):
        return []

    revision = f'{start_sha}..{branch}' if start_sha else branch
    ok, output, err = run_git(
        'log',
        '--reverse',
        revision,
        '-F',
        f'--grep={MERGE_TAG}',
        '--pretty=format:%H%x1f%ad%x1f%s',
        '--date=iso-strict',
    )
    if not ok:
        raise RuntimeError(f"读取分支追踪提交失败: {err or output}")

    results = []
    for line in output.splitlines():
        sha, timestamp, subject = line.split('\x1f', 2)
        if REPORT_TRACKING_RE.match(subject):
            results.append({
                'sha': sha,
                'timestamp': parse_git_iso_datetime(timestamp),
                'subject': subject,
            })
    return results


def collect_report_events():
    base = get_master_branch() or get_current_branch()
    current = get_current_branch()
    branches = get_local_branches()

    if current != base:
        current_source, create_entry = report_branch_source(current, base)
        current_compare_base = current_source or base
        start_sha = report_merge_base(current_compare_base, current)
        start_commit = report_commit_by_sha(start_sha) if start_sha else None
        events = []
        seen = set()
        merged_sources = []
        tracked_sources = []
        tracked_source_events = {}
        has_current_create_event = False

        if create_entry or start_commit:
            if create_entry:
                create_timestamp = create_entry['timestamp']
                create_sha = create_entry['sha']
            else:
                create_timestamp = start_commit['timestamp']
                create_sha = start_commit['sha']
            if create_entry and current_source:
                events.append({
                    'timestamp': create_timestamp,
                    'sha': create_sha,
                    'kind': 'create_branch',
                    'description': f"从 {current_source} 拉出 {current}",
                    'branch': current,
                    'source': current_source,
                    'target': current,
                })
                has_current_create_event = True
            if start_commit:
                seen.add(('current', start_commit['sha']))

        current_commits = report_current_branch_no_merge_commits(current, start_sha)
        for index, commit in enumerate(current_commits):
            if REPORT_TRACKING_RE.match(commit['subject']):
                continue
            key = ('current', commit['sha'])
            if key in seen:
                continue
            seen.add(key)
            if not has_current_create_event and index == 0:
                description = f"从 {current_compare_base} 拉出 {current}，并提交 {commit['subject']}"
                source_branch = current_compare_base
            else:
                description = f"{current} 提交 {commit['subject']}"
                source_branch = ''
            events.append({
                'timestamp': commit['timestamp'],
                'sha': commit['sha'],
                'kind': 'branch_commit',
                'description': description,
                'branch': current,
                'source': source_branch,
                'target': current if source_branch else '',
            })

        for commit in report_current_branch_merge_commits(current, start_sha):
            match = REPORT_MERGE_RE.match(commit['subject'])
            if match:
                source, target = match.groups()
                if target != current:
                    continue
            else:
                if len(commit.get('parents', [])) < 2:
                    continue
                source = report_resolve_parent_source(base, current, commit['parents'][1])
                target = current
                if not source:
                    continue
            merged_sources.append(source)
            events.append({
                'timestamp': commit['timestamp'],
                'sha': commit['sha'],
                'kind': 'merge',
                'description': f"将 {source} 合入 {target}",
                'branch': '',
                'source': source,
                'target': target,
            })

        tracking_commits = report_tracking_commits_for_branch(current, start_sha)
        for commit in tracking_commits:
            match = REPORT_TRACKING_RE.match(commit['subject'])
            if not match:
                continue
            target, sources = match.groups()
            for source in [item.strip() for item in sources.split(',') if item.strip()]:
                if source not in tracked_sources:
                    tracked_sources.append(source)
                tracked_source_events.setdefault(source, {
                    'timestamp': commit['timestamp'],
                    'sha': commit['sha'],
                    'target': target,
                })

        source_branches = []
        for branch in merged_sources + tracked_sources:
            if branch not in source_branches and branch not in (base, current):
                source_branches.append(branch)

        for branch in source_branches:
            branch_source, branch_create_entry = report_branch_source(branch, base)
            branch_compare_base = branch_source or base
            unique_commits = report_unique_commits_from_source(branch_source, base, branch)
            for index, commit in enumerate(unique_commits):
                key = ('branch', branch, commit['sha'])
                if key in seen:
                    continue
                seen.add(key)
                desc = (
                    f"从 {branch_compare_base} 拉出 {branch}，并提交 {commit['subject']}"
                    if index == 0 else
                    f"{branch} 提交 {commit['subject']}"
                )
                events.append({
                    'timestamp': commit['timestamp'],
                    'sha': commit['sha'],
                    'kind': 'branch_commit',
                    'description': desc,
                    'branch': branch,
                    'source': branch_compare_base if index == 0 else '',
                    'target': branch if index == 0 else '',
                })

            if not unique_commits:
                merge_base_sha = report_merge_base(branch_compare_base, branch)
                base_commit = report_commit_by_sha(merge_base_sha) if merge_base_sha else None
                if branch_create_entry or base_commit:
                    events.append({
                        'timestamp': branch_create_entry['timestamp'] if branch_create_entry else base_commit['timestamp'],
                        'sha': branch_create_entry['sha'] if branch_create_entry else base_commit['sha'],
                        'kind': 'create_branch',
                        'description': f"从 {branch_compare_base} 拉出 {branch}",
                        'branch': branch,
                        'source': branch_compare_base,
                        'target': branch,
                    })
                if branch in tracked_source_events and branch not in merged_sources:
                    skip_info = tracked_source_events[branch]
                    events.append({
                        'timestamp': skip_info['timestamp'],
                        'sha': skip_info['sha'],
                        'kind': 'skip_merge',
                        'description': f"{branch} 与 {skip_info['target']} 一致，跳过合并",
                        'branch': '',
                        'source': branch,
                        'target': skip_info['target'],
                    })

        for commit in tracking_commits:
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

        first_merge_time = None
        for event in events:
            if event['kind'] == 'merge':
                first_merge_time = event['timestamp']
                break
        if first_merge_time:
            for event in events:
                if event['kind'] == 'create_branch' and event['branch'] == current and event['timestamp'] >= first_merge_time:
                    from datetime import timedelta
                    event['timestamp'] = first_merge_time - timedelta(seconds=1)

        events.sort(key=lambda item: (item['timestamp'], item['sha'], item['kind']))
        return events

    start_commit = report_start_commit(base, branches)
    start_sha = start_commit['sha'] if start_commit else None
    seen = set()
    events = []

    if start_commit:
        key = ('base', start_commit['sha'])
        seen.add(key)
        events.append({
            'timestamp': start_commit['timestamp'],
            'sha': start_commit['sha'],
            'kind': 'base_commit',
            'description': f"{base} 提交 {start_commit['subject']}",
            'branch': base,
            'source': '',
            'target': '',
        })

    for commit in report_base_first_parent_commits(base):
        if start_sha and not report_commit_descends_from(start_sha, commit['sha']):
            continue
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
        branch_source, _ = report_branch_source(branch, base)
        branch_compare_base = branch_source or base
        for index, commit in enumerate(report_unique_commits_from_source(branch_source, base, branch)):
            if start_sha and not report_commit_descends_from(start_sha, commit['sha']):
                continue
            key = ('branch', branch, commit['sha'])
            if key in seen:
                continue
            seen.add(key)
            desc = (
                f"从 {branch_compare_base} 拉出 {branch}，并提交 {commit['subject']}"
                if index == 0 else
                f"{branch} 提交 {commit['subject']}"
            )
            events.append({
                'timestamp': commit['timestamp'],
                'sha': commit['sha'],
                'kind': 'branch_commit',
                'description': desc,
                'branch': branch,
                'source': branch_compare_base if index == 0 else '',
                'target': branch if index == 0 else '',
            })

    for commit in report_merge_commits():
        if start_sha and not report_commit_descends_from(start_sha, commit['sha']):
            continue
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
        if start_sha and not report_commit_descends_from(start_sha, commit['sha']):
            continue
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
        is_create = (
            event['kind'] == 'create_branch'
            or (event['kind'] == 'branch_commit' and event.get('source') and event.get('target'))
        )
        if is_create and event.get('branch'):
            source_branch = event.get('source') or base
            edge = (source_branch, event['branch'], 'create')
            if edge not in added_edges:
                added_edges.add(edge)
                lines.append(f'    {report_safe_node_id(source_branch)} -->|创建分支| {report_safe_node_id(event["branch"])}')
        elif event['kind'] == 'merge' and event['source'] and event['target']:
            edge = (event['source'], event['target'], event['sha'])
            if edge not in added_edges:
                added_edges.add(edge)
                lines.append(
                    f'    {report_safe_node_id(event["source"])} -->|{event["timestamp"].strftime("%H:%M:%S")} merge| {report_safe_node_id(event["target"])}'
                )
        elif event['kind'] == 'skip_merge' and event['source'] and event['target']:
            edge = (event['source'], event['target'], f"skip-{event['sha']}")
            if edge not in added_edges:
                added_edges.add(edge)
                lines.append(
                    f'    {report_safe_node_id(event["source"])} -.->|{event["timestamp"].strftime("%H:%M:%S")} skip| {report_safe_node_id(event["target"])}'
                )

    lines.append('```')
    return '\n'.join(lines)


def build_tracking_section(branch=None, start_sha=None):
    commits = report_tracking_commits_for_branch(branch, start_sha) if branch else report_tracking_commits()
    if not commits:
        return ['- 未发现 `[DREO-MERGE]` 追踪提交。']

    lines = []
    for commit in commits:
        timestamp_text = commit['timestamp'].isoformat(sep=' ', timespec='seconds')
        lines.append(f"- {timestamp_text}  {commit['subject']} ({commit['sha'][:7]})")
    return lines


def build_branch_report_markdown():
    repo = Path.cwd().resolve()
    base = get_master_branch() or get_current_branch()
    current = get_current_branch()
    events = collect_report_events()
    branches = report_branch_order(base, get_local_branches(), events, current=current)
    start_sha = report_merge_base(base, current) if current != base else None
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
    lines.extend(build_tracking_section(branch=current if current != base else None, start_sha=start_sha))
    lines.append('')
    return '\n'.join(lines)


def report_branch_order(base, branches, events, current=None):
    if current and current != base:
        names = {base, current}
    else:
        names = set(branches)
    for event in events:
        if event['branch']:
            names.add(event['branch'])
        if event['source']:
            for item in str(event['source']).split(','):
                item = item.strip()
                if item:
                    names.add(item)
        if event['target']:
            names.add(event['target'])

    ordered = []
    if base in names:
        ordered.append(base)
        names.remove(base)

    groups = [
        sorted([b for b in names if b.startswith(('feature_', 'bugfix_'))]),
        sort_branches_by_date([b for b in names if b.startswith(('dev_', 'release_'))], limit=len(names)),
        sorted([b for b in names if b not in ordered and not b.startswith(('feature_', 'bugfix_', 'dev_', 'release_'))]),
    ]
    for group in groups:
        for branch in group:
            if branch not in ordered:
                ordered.append(branch)
    return ordered


def report_event_type(event):
    if event['kind'] == 'create_branch':
        return 'create'
    if event['kind'] == 'branch_commit' and event['description'].startswith('从 '):
        return 'create'
    return event['kind']


def report_kind_label(event_type):
    labels = {
        'base_commit': '主干提交',
        'branch_commit': '分支提交',
        'create': '创建分支',
        'merge': '分支合并',
        'skip_merge': '跳过合并',
        'tracking': '追踪记录',
    }
    return labels.get(event_type, event_type)


def build_report_timeline_html(events):
    if not events:
        return '<p class="empty">未识别到可分析的提交记录。</p>'

    blocks = ['<div class="timeline">']
    for index, event in enumerate(events, 1):
        event_type = report_event_type(event)
        timestamp_text = event['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
        blocks.append(
            f'<article class="timeline-item timeline-{escape(event_type)}">'
            f'<div class="timeline-index">{index}</div>'
            f'<div class="timeline-body">'
            f'<div class="timeline-meta"><span class="kind-chip">{escape(report_kind_label(event_type))}</span>'
            f'<time>{escape(timestamp_text)}</time><span class="sha">{escape(event["sha"][:7])}</span></div>'
            f'<div class="timeline-text">{escape(event["description"])}</div>'
            f'</div></article>'
        )
    blocks.append('</div>')
    return ''.join(blocks)


def wrap_svg_text(text, max_width=26, max_lines=3):
    lines = []
    current = []
    current_width = 0

    for char in text:
        char_width = display_width(char)
        if current and current_width + char_width > max_width:
            lines.append(''.join(current))
            current = [char]
            current_width = char_width
            if len(lines) >= max_lines - 1:
                break
            continue
        current.append(char)
        current_width += char_width

    remainder = ''.join(current)
    if len(lines) < max_lines and remainder:
        lines.append(remainder)

    consumed = ''.join(lines)
    if len(consumed) < len(text):
        if lines:
            lines[-1] = lines[-1].rstrip(' .,_-') + '…'
        else:
            lines = [text[: max(1, max_width - 1)] + '…']
    return lines or ['']


def svg_multiline_text(x, center_y, lines, class_name, line_height=16):
    start_y = center_y - ((len(lines) - 1) * line_height) / 2
    parts = [f'<text x="{x}" y="{start_y}" text-anchor="middle" class="{class_name}">']
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else line_height
        parts.append(f'<tspan x="{x}" dy="{dy}">{escape(line)}</tspan>')
    parts.append('</text>')
    return ''.join(parts)


def build_tracking_card_lines(event):
    sources = [item.strip() for item in str(event['source']).split(',') if item.strip()]
    summary = [
        f"目标分支：{event['target']}",
        f"纳入分支：{len(sources)} 个",
    ]
    if sources:
        source_text = "、".join(sources)
        summary.extend(wrap_svg_text(f"来源：{source_text}", max_width=40, max_lines=4))
    return summary


def build_report_flow_svg(base, branches, events):
    ordered_branches = report_branch_order(base, branches, events)
    if not ordered_branches:
        return '<p class="empty">无可绘制的分支信息。</p>'

    graph_events = [
        event for event in events
        if report_event_type(event) in ('create', 'merge', 'skip_merge')
    ]
    padding_x = 72
    branch_gap = 84
    top_y = 106
    header_y = 20

    branch_cards = {}
    cursor_x = padding_x
    for branch in ordered_branches:
        branch_lines = wrap_svg_text(branch, max_width=22, max_lines=2)
        branch_width = min(max(168, max(display_width(line) for line in branch_lines) * 8 + 40), 260)
        center_x = cursor_x + branch_width / 2
        branch_cards[branch] = {
            'x': center_x,
            'width': branch_width,
            'lines': branch_lines,
            'height': 54 if len(branch_lines) == 1 else 70,
        }
        cursor_x += branch_width + branch_gap

    branch_x = {branch: card['x'] for branch, card in branch_cards.items()}
    width = max(1100, int(cursor_x - branch_gap + padding_x))

    event_blocks = []
    for event in graph_events:
        event_type = report_event_type(event)
        lines = wrap_svg_text(event['description'], max_width=28, max_lines=3)
        card_width = min(max(220, max(display_width(line) for line in lines) * 7 + 40), 340)
        card_height = 28 + len(lines) * 18
        row_height = max(108, card_height + 52)
        event_blocks.append({
            'event': event,
            'type': event_type,
            'lines': lines,
            'card_width': card_width,
            'card_height': card_height,
            'row_height': row_height,
        })

    current_y = top_y + 38
    for block in event_blocks:
        block['y'] = current_y
        current_y += block['row_height']

    height = max(300, int(current_y + 56))
    lane_bottom = height - 40

    svg = [
        f'<svg class="branch-graph-svg" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="分支流转图">',
        '<defs>',
        '<marker id="arrow-head" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">',
        '<path d="M0,0 L10,5 L0,10 z" fill="#2563eb" />',
        '</marker>',
        '<marker id="arrow-head-purple" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">',
        '<path d="M0,0 L10,5 L0,10 z" fill="#8b5cf6" />',
        '</marker>',
        '<filter id="card-shadow" x="-20%" y="-20%" width="140%" height="140%">',
        '<feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#0f172a" flood-opacity="0.14" />',
        '</filter>',
        '</defs>',
    ]

    for branch in ordered_branches:
        card = branch_cards[branch]
        x = card['x']
        card_y = header_y if len(card['lines']) == 1 else 14
        svg.append(f'<line x1="{x}" y1="{top_y}" x2="{x}" y2="{lane_bottom}" class="lane-line" />')
        svg.append(f'<rect x="{x - card["width"] / 2}" y="{card_y}" width="{card["width"]}" '
                   f'height="{card["height"]}" rx="14" class="branch-card" />')
        svg.append(svg_multiline_text(x, card_y + card['height'] / 2 + 1, card['lines'], 'branch-card-text', line_height=16))

    for block in event_blocks:
        event = block['event']
        y = block['y']
        event_type = block['type']
        lines = block['lines']

        source = base if event_type == 'create' else event['source']
        target = event['branch'] if event_type == 'create' else event['target']
        if source not in branch_x or target not in branch_x:
            continue
        x1 = branch_x[source]
        x2 = branch_x[target]
        line_class = {
            'create': 'create-line',
            'merge': 'merge-line',
            'skip_merge': 'skip-line',
        }.get(event_type, 'merge-line')
        mid_x = (x1 + x2) / 2
        svg.append(f'<circle cx="{x1}" cy="{y}" r="5" class="event-dot" />')
        svg.append(f'<circle cx="{x2}" cy="{y}" r="5" class="event-dot" />')
        marker = 'url(#arrow-head)' if event_type != 'skip_merge' else 'url(#arrow-head-purple)'
        svg.append(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" class="{line_class}" marker-end="{marker}" />')
        svg.append(f'<rect x="{mid_x - block["card_width"] / 2}" y="{y - block["card_height"] / 2}" '
                   f'width="{block["card_width"]}" height="{block["card_height"]}" rx="12" '
                   f'class="event-label-card" filter="url(#card-shadow)" />')
        svg.append(svg_multiline_text(mid_x, y + 1, lines, 'event-label', line_height=16))

    svg.append('</svg>')
    return ''.join(svg)


def build_tracking_section_html(branch=None, start_sha=None):
    commits = report_tracking_commits_for_branch(branch, start_sha) if branch else report_tracking_commits()
    if not commits:
        return '<p class="empty">未发现追踪提交。</p>'

    rows = ['<table class="tracking-table"><thead><tr><th>时间</th><th>提交信息</th><th>SHA</th></tr></thead><tbody>']
    for commit in commits:
        timestamp_text = commit['timestamp'].isoformat(sep=' ', timespec='seconds')
        rows.append(
            f'<tr><td>{escape(timestamp_text)}</td>'
            f'<td>{escape(commit["subject"])}</td>'
            f'<td><code>{escape(commit["sha"][:7])}</code></td></tr>'
        )
    rows.append('</tbody></table>')
    return ''.join(rows)


def build_branch_report_html():
    repo = Path.cwd().resolve()
    base = get_master_branch() or get_current_branch()
    current = get_current_branch()
    events = collect_report_events()
    branches = report_branch_order(base, get_local_branches(), events, current=current)
    start_sha = report_merge_base(base, current) if current != base else None
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    feature_counts = branch_counts(('feature_', 'bugfix_'))
    integration_counts = branch_counts(('dev_', 'release_'))

    branch_chips = ''.join(
        f'<span class="branch-chip">{escape(branch)}</span>' for branch in report_branch_order(base, branches, events, current=current)
    ) or '<span class="empty">未检测到分支。</span>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Git 分支合并报告</title>
  <style>
    :root {{
      --bg: #f3f6fb;
      --card: #ffffff;
      --line: #d7e0ec;
      --text: #0f172a;
      --muted: #526072;
      --blue: #2563eb;
      --green: #0f9d58;
      --orange: #f59e0b;
      --violet: #7c3aed;
      --shadow: 0 18px 42px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(37, 99, 235, 0.10), transparent 28%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      color: var(--text);
    }}
    .page {{
      width: min(1440px, calc(100% - 48px));
      margin: 32px auto 56px;
    }}
    .hero {{
      background: linear-gradient(135deg, #0f172a, #1d4ed8);
      color: #fff;
      border-radius: 24px;
      padding: 28px 32px;
      box-shadow: var(--shadow);
    }}
    .hero h1 {{ margin: 0 0 12px; font-size: 34px; }}
    .hero p {{ margin: 6px 0; color: rgba(255,255,255,0.82); }}
    .meta-grid, .stats-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      margin-top: 20px;
    }}
    .meta-card, .stat-card, .panel {{
      background: var(--card);
      border: 1px solid rgba(215, 224, 236, 0.85);
      border-radius: 22px;
      box-shadow: var(--shadow);
      color: var(--text);
    }}
    .meta-card, .stat-card {{
      padding: 18px 20px;
    }}
    .meta-card .label, .stat-card .label {{
      color: var(--muted);
      font-size: 13px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .meta-card .value, .stat-card .value {{
      margin-top: 10px;
      font-size: 20px;
      font-weight: 700;
      word-break: break-all;
    }}
    .sections {{
      display: grid;
      gap: 22px;
      margin-top: 24px;
    }}
    .panel {{
      padding: 22px 24px;
    }}
    .panel h2 {{
      margin: 0 0 18px;
      font-size: 22px;
    }}
    .branch-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .branch-chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      background: #eaf1ff;
      color: #1d4ed8;
      font-weight: 600;
    }}
    .branch-chip::before {{
      content: "🌿";
      font-size: 14px;
    }}
    .timeline {{
      position: relative;
      display: grid;
      gap: 16px;
      padding-left: 14px;
    }}
    .timeline::before {{
      content: "";
      position: absolute;
      left: 22px;
      top: 8px;
      bottom: 8px;
      width: 2px;
      background: linear-gradient(180deg, #93c5fd, #dbeafe);
    }}
    .timeline-item {{
      position: relative;
      display: grid;
      grid-template-columns: 44px 1fr;
      gap: 16px;
      align-items: start;
    }}
    .timeline-index {{
      position: relative;
      z-index: 1;
      display: grid;
      place-items: center;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: #fff;
      border: 2px solid #bfdbfe;
      color: var(--blue);
      font-weight: 700;
      box-shadow: 0 8px 18px rgba(37, 99, 235, 0.12);
    }}
    .timeline-body {{
      padding: 16px 18px;
      background: #f8fbff;
      border: 1px solid var(--line);
      border-radius: 18px;
    }}
    .timeline-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .kind-chip {{
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      background: #dbeafe;
      color: #1d4ed8;
      font-weight: 700;
    }}
    .sha {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: #e2e8f0;
      border-radius: 999px;
      padding: 4px 10px;
    }}
    .timeline-text {{ line-height: 1.7; }}
    .graph-wrap {{
      overflow-x: auto;
      padding-bottom: 8px;
    }}
    .branch-graph-svg {{
      width: 100%;
      min-width: 960px;
      background: linear-gradient(180deg, #f8fbff, #ffffff);
      border-radius: 20px;
      border: 1px solid var(--line);
    }}
    .branch-card {{
      fill: #eaf1ff;
      stroke: #93c5fd;
      stroke-width: 1.5;
    }}
    .branch-card-text {{
      fill: #0f172a;
      font-size: 13px;
      font-weight: 700;
    }}
    .lane-line {{
      stroke: #cbd5e1;
      stroke-width: 3;
      stroke-dasharray: 8 8;
    }}
    .event-dot {{
      fill: #2563eb;
    }}
    .create-line {{
      stroke: #0f9d58;
      stroke-width: 4;
    }}
    .merge-line {{
      stroke: #2563eb;
      stroke-width: 4;
    }}
    .skip-line {{
      stroke: #8b5cf6;
      stroke-width: 3;
      stroke-dasharray: 8 8;
    }}
    .event-label-card {{
      fill: #ffffff;
      stroke: #d7e0ec;
      stroke-width: 1.2;
    }}
    .event-label {{
      fill: #0f172a;
      font-size: 12px;
      font-weight: 600;
    }}
    .tracking-card {{
      fill: #f5f3ff;
      stroke: #c4b5fd;
      stroke-width: 1.2;
    }}
    .tracking-link {{
      stroke: #8b5cf6;
      stroke-width: 2.2;
      stroke-dasharray: 6 6;
      opacity: 0.78;
    }}
    .tracking-node {{
      fill: #8b5cf6;
    }}
    .tracking-target-node {{
      fill: #7c3aed;
    }}
    .tracking-title {{
      fill: #6d28d9;
      font-size: 12px;
      font-weight: 700;
    }}
    .tracking-text {{
      fill: #4c1d95;
      font-size: 11px;
      font-weight: 600;
    }}
    .tracking-table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 16px;
      border: 1px solid var(--line);
    }}
    .tracking-table th, .tracking-table td {{
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    .tracking-table thead {{
      background: #eff6ff;
    }}
    .tracking-table tbody tr:nth-child(even) {{
      background: #fafcff;
    }}
    .empty {{
      color: var(--muted);
      margin: 0;
    }}
    @media (max-width: 900px) {{
      .page {{ width: min(100% - 24px, 1440px); margin: 16px auto 32px; }}
      .hero {{ padding: 22px 20px; border-radius: 20px; }}
      .hero h1 {{ font-size: 28px; }}
      .panel {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <h1>Git 分支合并报告</h1>
      <p>基于当前仓库的提交历史、merge 记录和追踪提交自动生成。</p>
      <div class="meta-grid">
        <div class="meta-card"><div class="label">仓库路径</div><div class="value">{escape(str(repo))}</div></div>
        <div class="meta-card"><div class="label">生成时间</div><div class="value">{escape(generated_at)}</div></div>
        <div class="meta-card"><div class="label">基线分支</div><div class="value">{escape(base)}</div></div>
        <div class="meta-card"><div class="label">当前分支</div><div class="value">{escape(current)}</div></div>
      </div>
    </section>

    <section class="stats-grid">
      <article class="stat-card"><div class="label">开发分支</div><div class="value">{feature_counts['total']}</div><div class="label">本地 {feature_counts['local']} / 远端 {feature_counts['remote']}</div></article>
      <article class="stat-card"><div class="label">集成分支</div><div class="value">{integration_counts['total']}</div><div class="label">本地 {integration_counts['local']} / 远端 {integration_counts['remote']}</div></article>
      <article class="stat-card"><div class="label">事件数</div><div class="value">{len(events)}</div><div class="label">按时间顺序推断</div></article>
      <article class="stat-card"><div class="label">追踪提交</div><div class="value">{len(report_tracking_commits())}</div><div class="label">含 [DREO-MERGE]</div></article>
    </section>

    <section class="sections">
      <article class="panel">
        <h2>分支概览</h2>
        <div class="branch-chips">{branch_chips}</div>
      </article>

      <article class="panel">
        <h2>时间线</h2>
        {build_report_timeline_html(events)}
      </article>

      <article class="panel">
        <h2>分支流转图</h2>
        <div class="graph-wrap">{build_report_flow_svg(base, branches, events)}</div>
      </article>

      <article class="panel">
        <h2>追踪提交</h2>
        {build_tracking_section_html(branch=current if current != base else None, start_sha=start_sha)}
      </article>
    </section>
  </main>
</body>
</html>
"""


def build_branch_report():
    return build_branch_report_markdown()


def generate_branch_report(output_path=None):
    output = Path(output_path) if output_path else Path.cwd() / 'branch_merge_report.md'
    if output.suffix.lower() == '.html':
        content = build_branch_report_html()
    else:
        content = build_branch_report_markdown()
    output.write_text(content, encoding='utf-8')
    return output.resolve()


def generate_branch_report_menu():
    header("生成分支处理报告", icon=UI['report'])
    note("将根据当前仓库的提交历史、merge 记录和追踪提交生成报告。", 'info')
    html_output = generate_branch_report(Path.cwd() / 'branch_merge_report.html')
    md_output = generate_branch_report(Path.cwd() / 'branch_merge_report.md')
    note(f"HTML 报告已生成: {terminal_link(html_output)}", 'success')
    note(f"Markdown 报告已生成: {terminal_link(md_output)}", 'success')
    note("HTML 报告包含可视化时间线和分支流转图，建议优先查看。", 'tip')


# ─── 冲突处理 ────────────────────────────────────────────────────


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


def _has_merge_conflict(stdout, stderr):
    combined = stdout + stderr
    return 'CONFLICT' in combined or '冲突' in combined or get_unmerged_files()


def do_merge(source_branch, action_label="合并"):
    """将 source_branch 合并到当前分支，处理冲突。返回是否成功。"""
    current = get_current_branch()
    print(f"\n  {icon_slot(UI['merge'], '36')} {action_label} [{source_branch}] → [{current}] ...")
    ok, out, err = run_git('merge', '--no-ff', source_branch)
    if ok:
        note(f"{action_label}成功: {source_branch}", 'success')
        return True
    if _has_merge_conflict(out, err):
        run_git('rerere')

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
    header("创建开发分支", icon=UI['create_feature'])
    refresh_remote_refs()

    master_branch = get_master_branch()
    if not master_branch:
        note("未找到 master / main 分支，请先初始化仓库。", 'error')
        return
    current_branch = get_current_branch()

    base = master_branch
    if current_branch != master_branch:
        print(f"  {icon_slot(UI['type'], '36')} 请选择分支基线：")
        base_idx = select_one([
            f'{master_branch}  — 推荐',
            f'{current_branch}  — 不建议',
        ], "分支基线")
        if base_idx is None:
            return False
        base = [master_branch, current_branch][base_idx]
        if base == current_branch:
            note(f"将基于当前分支 [{current_branch}] 创建新开发分支。", 'warn')
            note("新分支会继承当前分支的全部代码，可能增加后续集成和报告阅读成本。", 'tip')
            if not confirm("确认继续使用当前分支作为基线？"):
                note("已取消。", 'warn')
                return False
    else:
        note(f"当前分支就是 [{master_branch}]，将直接基于它创建。", 'tip')

    # 选择分支类型
    print(f"  {icon_slot(UI['type'], '36')} 请选择分支类型：")
    type_idx = select_one(['feature  — 新功能开发', 'bugfix   — 缺陷修复'], "分支类型")
    if type_idx is None:
        return False
    branch_type = ['feature', 'bugfix'][type_idx]

    # 输入分支名称
    date_suffix = today_str()
    while True:
        name = read_text_input(
            f"分支名称（最终: {branch_type}_<名称>_{date_suffix}，直接回车返回）",
            prefix='> ',
        )
        if not name:
            return False
        if any(c in name for c in _BRANCH_NAME_INVALID_CHARS):
            note("包含非法字符，请重新输入。", 'warn')
            continue
        branch_name = f"{branch_type}_{name}_{date_suffix}"
        if has_local_branch(branch_name):
            note(f"分支 '{branch_name}' 已存在，请换一个名称。", 'error')
            continue
        break

    if has_remote_branch(branch_name):
        note(f"检测到同名远端分支 [{branch_name}]，将直接拉取到本地。", 'tip')
        if not ensure_local_branch(branch_name, checkout=True):
            return
        return

    # 切换到 base 并更新
    if base == master_branch:
        if not checkout_and_update_base(base):
            return
    else:
        ok, _, err = run_git('checkout', base)
        if not ok:
            note(f"切换到基线分支失败: {err}", 'error')
            return

    # 创建新分支
    ok, _, err = run_git('checkout', '-b', branch_name)
    if ok:
        note(f"已创建并切换到: {branch_name}  (基于 {base})", 'success')
        offer_push_branch(
            branch_name,
            set_upstream=True,
            prompt=f"是否将新开发分支 [{branch_name}] 推送到远端？",
        )
    else:
        note(f"创建分支失败: {err}", 'error')


# ─── 集成分支公共：选分支并合并 ──────────────────────────────────

def _merge_into_integration(int_branch, candidates, action_name="合并"):
    """将 candidates 中用户选中的分支合并到 int_branch，写追踪提交。"""
    sorted_c = sort_branches_by_date(candidates, limit=len(candidates))
    total = len(candidates)
    print(f"\n  {icon_slot(UI['feature_branch'], '36')} 选择要{action_name}到 [{int_branch}] 的开发分支（共 {total} 个，已按时间倒序排序）：")
    display_options = [branch_display_name(branch) for branch in sorted_c]
    indices = select_many(display_options)
    if indices is None:
        return False
    selected = [sorted_c[i] for i in indices]

    print(f"\n  {icon_slot(UI['merge'], '36')} 将{action_name}以下分支 → [{int_branch}]：")
    print_list([branch_display_name(branch) for branch in selected])
    if not confirm(f"确认执行{action_name}？"):
        note("已取消。", 'warn')
        return False

    if not ensure_local_branch(int_branch, checkout=True):
        return False

    succeeded, failed = [], []
    for branch in selected:
        if not ensure_local_branch(branch):
            failed.append(branch)
            continue
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
    return {
        'selected': selected,
        'succeeded': succeeded,
        'failed': failed,
    }


def checkout_and_update_base(base):
    print(f"\n  {icon_slot(UI['checkout'], '36')} 切换到 {base}，同步最新代码...")
    if not ensure_local_branch(base, checkout=True):
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
    refresh_remote_refs()

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
        if any(c in version for c in _BRANCH_NAME_INVALID_CHARS):
            note("包含非法字符，请重新输入。", 'warn')
            continue
        break

    int_branch = f"{env_prefix}_{version}_{date_suffix}"
    if has_local_branch(int_branch):
        note(f"分支 '{int_branch}' 已存在，请使用「2.3 添加新的开发分支」功能。", 'error')
        return

    if has_remote_branch(int_branch):
        note(f"检测到同名远端集成分支 [{int_branch}]，将直接拉取到本地继续操作。", 'tip')
        if not ensure_local_branch(int_branch, checkout=True):
            return
        result = _merge_into_integration(int_branch, feature_branches, action_name="合并")
        if result and result['succeeded']:
            offer_push_branch(
                int_branch,
                prompt=f"是否将更新后的集成分支 [{int_branch}] 推送到远端？",
            )
        return

    print(f"\n  {icon_slot(UI['build'], '36')} 从最新 {base} 创建集成分支 {int_branch}...")
    if not checkout_and_update_base(base):
        return
    ok, _, err = run_git('checkout', '-b', int_branch)
    if not ok:
        note(f"创建失败: {err}", 'error')
        return
    note(f"已创建集成分支: {int_branch}", 'success')

    result = _merge_into_integration(int_branch, feature_branches, action_name="合并")
    if result is not False:
        offer_push_branch(
            int_branch,
            set_upstream=True,
            prompt=f"是否将集成分支 [{int_branch}] 推送到远端？",
        )


# ─── 功能 2.3：添加新的开发分支到集成分支 ────────────────────────

def add_branches_to_integration():
    header("2.3  添加新的开发分支到集成分支", icon='➕')
    refresh_remote_refs()

    int_branches = get_integration_branches()
    if not int_branches:
        note("没有找到集成分支，请先创建。", 'error')
        return

    print(f"  {icon_slot(UI['select'], '36')} 请选择目标集成分支：")
    idx = select_one([branch_display_name(branch) for branch in int_branches])
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

    result = _merge_into_integration(int_branch, candidates, action_name="添加")
    if result and result['succeeded']:
        offer_push_branch(
            int_branch,
            prompt=f"是否将更新后的集成分支 [{int_branch}] 推送到远端？",
        )
    return result


# ─── 功能 3：同步更新集成分支 ────────────────────────────────────

def get_merged_feature_branches(int_branch):
    """通过 DREO-MERGE 标志位查找曾被集成到该分支的所有开发分支
    格式: [DREO-MERGE] {int_branch} <- branch1,branch2,...
    """
    _, log, _ = run_git('log', '--all', '-F', f'--grep={MERGE_TAG} {int_branch} <-',
                        '--pretty=format:%s')
    prefix = f"{MERGE_TAG} {int_branch} <- "
    seen, result = set(), []
    for line in log.splitlines():
        if not line.startswith(prefix):
            continue
        branch_list = line[len(prefix):]
        for b in branch_list.split(','):
            b = b.strip()
            if b and b not in seen:
                seen.add(b)
                result.append(b)
    return result


def update_integration_branch():
    header("同步更新集成分支（先同步主干，再合并开发分支新增提交）", icon=UI['sync'])
    refresh_remote_refs()

    int_branches = get_integration_branches()
    if not int_branches:
        note("没有找到集成分支（dev_ / release_ 开头）。", 'error')
        return

    # 选择要更新的集成分支
    print(f"  {icon_slot(UI['select'], '36')} 请选择要同步更新的集成分支：")
    idx = select_one([branch_display_name(branch) for branch in int_branches])
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

    existing = [b for b in merged_branches if branch_available(b)]
    missing  = [b for b in merged_branches if not branch_available(b)]

    print(f"\n  {icon_slot(UI['records'], '36')} 检测到以下开发分支曾被集成到 [{int_branch}]：")
    branch_lines = []
    for b in merged_branches:
        if has_local_branch(b):
            tag = ''
        elif has_remote_branch(b):
            tag = ' [仅远端存在，将先拉到本地]'
        else:
            tag = ' [本地与远端均不存在，将跳过]'
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
        if not ensure_local_branch(branch):
            failed.append(branch)
            continue
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

    if base_sync_status == 'success' or succeeded:
        offer_push_branch(
            int_branch,
            prompt=f"是否将同步后的集成分支 [{int_branch}] 推送到远端？",
        )


# ─── 功能 4：删除分支 ────────────────────────────────────────────

def delete_named_branches(branches, include_remote=False):
    local_branches = set(get_local_branches())
    remote_branches = set(get_remote_branches()) if include_remote else set()
    current = get_current_branch()
    base = get_master_branch()
    protected = {current, base}

    succeeded, failed = [], []
    for branch in branches:
        if branch in protected:
            failed.append(branch)
            note(f"受保护分支，已跳过: {branch}", 'warn')
            continue

        local_deleted = False
        remote_deleted = False

        if branch in local_branches:
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
                local_deleted = True
                note(f"本地已删除: {branch}", 'success')
            else:
                failed.append(branch)
                note(f"删除失败: {err}", 'error')
                continue

        if include_remote and branch in remote_branches:
            rok, _, rerr = run_git('push', 'origin', '--delete', branch)
            if rok:
                remote_deleted = True
                note(f"远端已删除: {branch}", 'success')
            else:
                note(f"远端删除失败: {rerr}", 'warn')

        if local_deleted or remote_deleted:
            details = []
            if local_deleted:
                details.append('本地')
            if remote_deleted:
                details.append('远端')
            succeeded.append(f"{branch}（{' + '.join(details)}）")
        else:
            failed.append(branch)
            note(f"删除失败: {branch}", 'error')

    rows = []
    if succeeded:
        rows.append(('✅', f"已删除 ({len(succeeded)}): " + ", ".join(succeeded)))
    if failed:
        rows.append(('❌', f"跳过   ({len(failed)}): " + ", ".join(failed)))
    summary_block("删除结果汇总", rows)
    return {'succeeded': succeeded, 'failed': failed}


def delete_branches(include_remote=False):
    mode = "本地 + 云端" if include_remote else "仅本地"
    header(f"删除分支（{mode}）", icon=UI['delete'])

    local_branches = set(get_local_branches())
    remote_branches = set(get_remote_branches()) if include_remote else set()
    all_branches = sorted(local_branches | remote_branches)
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

    # 按分组顺序构建列表，复用分页多选逻辑
    ordered = []
    display_options = []
    groups = [
        ('开发分支', sort_branches_by_date(feature_bs, limit=len(feature_bs))),
        ('集成分支', sort_branches_by_date(int_bs,     limit=len(int_bs))),
        ('其他分支', other_bs),
    ]
    for group_name, branches in groups:
        for b in branches:
            ordered.append(b)
            display_options.append(f"{group_name}｜{format_branch_with_location(b)}")

    note(f"当前分支 [{current}] 和 [{base}] 受保护，不在列表中。", 'tip')
    print(f"  {icon_slot(UI['delete'], '36')} 选择要删除的分支：")
    selected_indices = select_many(
        display_options,
        "多个用逗号分隔；all=全选；0=返回；长列表可用方向键上/下翻页，单个编号回车可直接确认",
        auto_confirm_single=True,
    )
    if selected_indices is None:
        return False
    selected = [ordered[i] for i in selected_indices]

    print(f"\n  {icon_slot(UI['records'], '36')} 将删除以下 {len(selected)} 个分支：")
    print_list([format_branch_with_location(branch) for branch in selected])
    note("此操作不可恢复，请确认分支代码已合并或不再需要。", 'warn')
    if not confirm("确认删除？"):
        note("已取消。", 'warn')
        return False

    delete_named_branches(selected, include_remote=include_remote)


# ─── 功能 5：合并发布分支回 master ───────────────────────────────

def merge_to_master():
    header("合并发布分支回 master（基线写入）", icon=UI['release'])
    refresh_remote_refs()

    release_branches = [b for b in get_integration_branches() if b.startswith('release_')]
    if not release_branches:
        note("没有找到发布分支（release_ 开头）。", 'error')
        return

    base = get_master_branch()
    if not base:
        note("未找到 master / main 分支。", 'error')
        return

    print(f"  {icon_slot(UI['select'], '36')} 选择要合并到 [{base}] 的发布分支：")
    idx = select_one([branch_display_name(branch) for branch in release_branches])
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

    if not ensure_local_branch(release_branch):
        return

    if do_merge(release_branch):
        _, log, _ = run_git('log', '--oneline', '-5')
        note(f"[{release_branch}] 已成功合并到 {base}！", 'success')
        print(f"\n  {icon_slot(UI['records'], '36')} 最近提交记录：")
        print_list(log.splitlines())
        offer_push_branch(
            base,
            prompt=f"是否将 [{base}] 的最新合并结果推送到远端？",
        )
        related_features = [
            branch for branch in get_merged_feature_branches(release_branch)
            if is_managed_feature_branch(branch)
        ]
        if related_features:
            print(f"\n  {icon_slot(UI['delete'], '36')} 检测到 [{release_branch}] 关联以下开发分支：")
            print_list([format_branch_with_location(branch) for branch in related_features])
            if confirm("是否立即删除这些关联开发分支？（本地 + 远端）"):
                delete_named_branches(related_features, include_remote=True)
            else:
                note("已保留关联开发分支，不做删除。", 'tip')
        else:
            note(f"未检测到 [{release_branch}] 的关联开发分支记录。", 'tip')
    else:
        note("合并失败或已放弃。", 'error')


# ─── 功能 6：合并 master 到当前分支 ──────────────────────────────

def merge_master_to_current():
    header("合并 master 到当前分支", icon=UI['sync'])
    refresh_remote_refs()

    current = get_current_branch()
    base = get_master_branch()

    if not base:
        note("未找到 master / main 分支。", 'error')
        return False
    if current == base:
        note(f"当前已在 [{base}]，无需执行此操作。", 'tip')
        return False

    print(f"\n  {icon_slot(UI['merge'], '36')} 操作：[{base}] → [{current}]")
    if not confirm("确认执行？"):
        note("已取消。", 'warn')
        return False

    if not checkout_and_update_base(base):
        return False

    ok, _, err = run_git('checkout', current)
    if not ensure_git_success(ok, err, f"切换回 [{current}]"):
        return False

    _, ahead, _ = run_git('rev-list', '--count', f'{current}..{base}')
    new_commits = int(ahead) if ahead.isdigit() else 0
    if new_commits == 0:
        note(f"[{current}] 已包含最新 {base}，无需合并。", 'tip')
        return True

    print(f"\n  {icon_slot(UI['sync'], '36')} 检测到 {base} 有 {new_commits} 个新提交，将合并到 [{current}] ...")
    if do_merge(base, action_label="合并主干代码"):
        offer_push_branch(
            current,
            prompt=f"是否将合并后的当前分支 [{current}] 推送到远端？",
        )
        return True

    note("合并失败或已放弃。", 'error')
    return False


# ─── 功能 7：拉取远端分支到本地 ──────────────────────────────────

def pull_remote_branch_to_local():
    header("拉取远端分支到本地", icon='📥')
    refresh_remote_refs()

    remote_only = [
        branch for branch in get_remote_branches()
        if not has_local_branch(branch)
    ]
    remote_only = sort_branches_by_date(remote_only, limit=len(remote_only))

    if not remote_only:
        note("没有检测到仅存在于远端的分支。", 'tip')
        return

    print(f"  {icon_slot(UI['select'], '36')} 请选择要拉取到本地的远端分支：")
    idx = select_one([branch_display_name(branch) for branch in remote_only])
    if idx is None:
        return False

    branch = remote_only[idx]
    print(f"\n  {icon_slot('📥', '36')} 操作：origin/{branch} → 本地 {branch}")
    if not confirm("确认拉取并切换到该分支？"):
        note("已取消。", 'warn')
        return False

    if ensure_local_branch(branch, checkout=True):
        note(f"已将远端分支拉取到本地并切换到: {branch}", 'success')


# ─── 菜单系统 ────────────────────────────────────────────────────

def show_status():
    current = get_current_branch()
    features = branch_counts(('feature_', 'bugfix_'))
    integrations = branch_counts(('dev_', 'release_'))
    branch_text = paint(current, '1', '37')
    print()
    print(f"  {icon_slot(UI['current_branch'], '32')} 当前分支: {branch_text}\n")
    print(f"     开发分支: {features['total']}（本地：{features['local']}，远端：{features['remote']}）")
    print(f"     集成分支: {integrations['total']}（本地：{integrations['local']}，远端：{integrations['remote']}）")


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
        ("拉取远端分支到本地",               pull_remote_branch_to_local),
        ("合并 master 到当前分支",           merge_master_to_current),
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
