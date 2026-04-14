#!/usr/bin/env python3
"""
Dreo 分支管理工具
"""

import subprocess
import sys
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
import os
import shutil
import codecs
import re
import threading
import time
import unicodedata
from pathlib import Path
import atexit

from branch_report_templates import render_html_report, render_markdown_report

try:
    import termios
    import tty
except ImportError:  # pragma: no cover
    termios = None
    tty = None


def _restore_tty():
    if termios and tty and sys.stdin.isatty():
        os.system('stty sane 2>/dev/null')


atexit.register(_restore_tty)


def today_str():
    return date.today().strftime('%Y%m%d')


APP_VERSION = "2026.04.14"
INSTALL_METADATA_FILE = "dreo_branch_manager_meta.json"


def install_metadata_path():
    return Path(__file__).resolve().with_name(INSTALL_METADATA_FILE)


def load_install_metadata():
    path = install_metadata_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}


def current_version_label():
    metadata = load_install_metadata()
    revision = str(metadata.get('source_revision') or '').strip()
    return revision or APP_VERSION


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


def is_valid_branch_name(name):
    ok, _, _ = run_git('check-ref-format', '--branch', name, capture=True)
    return ok


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


class LoadingIndicator:
    def __init__(self, message, frames=None, interval=0.12):
        self.message = message
        self.frames = frames or ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.interval = interval
        self.enabled = sys.stdout.isatty()
        self._stop = threading.Event()
        self._thread = None
        self._width = 0

    def _render(self):
        index = 0
        while not self._stop.is_set():
            line = f"  {self.frames[index % len(self.frames)]} {self.message}"
            self._width = max(self._width, display_width(line))
            print(f"\r{line}", end='', flush=True)
            index += 1
            if self._stop.wait(self.interval):
                break

    def __enter__(self):
        if self.enabled:
            self._thread = threading.Thread(target=self._render, daemon=True)
            self._thread.start()
        else:
            print(f"  {icon_slot(UI['sync'], '36')} {self.message}...")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=0.3)
            clear = ' ' * max(self._width, display_width(self.message) + 4)
            print(f"\r{clear}\r", end='', flush=True)
        return False


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


@lru_cache(maxsize=1)
def get_default_remote():
    ok, out, _ = run_git('remote')
    if not ok or not out:
        return 'origin'
    remotes = [line.strip() for line in out.splitlines() if line.strip()]
    return 'origin' if 'origin' in remotes else (remotes[0] if remotes else 'origin')


def has_default_remote():
    remote = get_default_remote()
    ok, _, _ = run_git('remote', 'get-url', remote)
    return ok


def offer_push_branch(branch, set_upstream=False, prompt=None):
    if not has_default_remote():
        note(f"未检测到 {get_default_remote()} 远端，已跳过推送。", 'tip')
        return False

    message = prompt or f"是否立即推送 [{branch}] 到远端？"
    if not confirm(message):
        note("已跳过远端推送。", 'warn')
        return False

    with LoadingIndicator(f"正在推送分支 [{branch}] 到远端"):
        remote = get_default_remote()
        if set_upstream:
            ok, _, err = run_git('push', '-u', remote, branch)
        else:
            ok, _, err = run_git('push', remote, branch)

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
    """检查 rerere 是否开启，未开启则自动在当前仓库启用。"""
    _, val, _ = run_git('config', '--local', 'rerere.enabled')
    if val != 'true':
        print()
        note("检测到当前仓库未开启 rerere（冲突记忆）功能。", 'tip')
        print("  已为当前仓库自动开启；同一冲突手动解决一次后，后续可自动复用解决方案。")
        ok, _, err = run_git('config', '--local', 'rerere.enabled', 'true')
        if ok:
            note("rerere 已自动开启。", 'success')
        else:
            note(f"自动开启 rerere 失败: {err}", 'warn')


def get_current_branch():
    _, branch, _ = run_git('rev-parse', '--abbrev-ref', 'HEAD')
    return branch


def get_local_branches():
    _, output, _ = run_git('branch', '--format=%(refname:short)')
    return [b.strip() for b in output.splitlines() if b.strip()]


def get_remote_branches():
    if not has_default_remote():
        return []
    ok, output, _ = run_git('branch', '-r', '--format=%(refname:short)')
    if not ok:
        return []

    branches = []
    remote = get_default_remote()
    for line in output.splitlines():
        name = line.strip()
        if not name or name.endswith('/HEAD'):
            continue
        if name.startswith(f'{remote}/'):
            branches.append(name.split('/', 1)[1])
    return branches


def is_managed_feature_branch(branch):
    return bool(MANAGED_BRANCH_PATTERNS['feature'].match(branch))


def is_managed_integration_branch(branch):
    return bool(MANAGED_BRANCH_PATTERNS['integration'].match(branch))


def refresh_remote_refs():
    if not has_default_remote():
        return False
    remote = get_default_remote()
    with LoadingIndicator("正在刷新远端分支信息"):
        ok, _, err = run_git('fetch', remote, '--prune')
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


def format_branch_with_sets(branch, local_branches, remote_branches):
    local = branch in local_branches
    remote = branch in remote_branches
    if local and remote:
        label = '本地 + 远端'
    elif local:
        label = '仅本地'
    elif remote:
        label = '仅远端'
    else:
        label = '不存在'
    return f"{branch}  [{label}]"


def branch_source_ref(branch):
    """同步/合并时优先使用远端分支，避免本地分支落后导致漏合并。"""
    remote = get_default_remote()
    return f"{remote}/{branch}" if has_remote_branch(branch) else branch


def latest_commit_subject(ref):
    ok, output, _ = run_git('log', '-1', '--pretty=format:%s', ref)
    return output.strip() if ok else ''


def branch_counts(prefixes, validator, local_branches=None, remote_branches=None):
    local_s = set(local_branches) if local_branches is not None else set(get_local_branches())
    remote_s = set(remote_branches) if remote_branches is not None else set(get_remote_branches())
    local_m = {b for b in local_s if b.startswith(prefixes) and validator(b)}
    remote_m = {b for b in remote_s if b.startswith(prefixes) and validator(b)}
    return {
        'local': len(local_m),
        'remote': len(remote_m),
        'total': len(local_m | remote_m),
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

    remote = get_default_remote()
    if checkout:
        ok, _, err = run_git('checkout', '-b', branch, '--track', f'{remote}/{branch}')
        if ensure_git_success(ok, err, f"从远端创建并切换到 [{branch}]"):
            note(f"已从远端创建本地跟踪分支: {branch}", 'success')
            return True
        return False

    ok, _, err = run_git('branch', '--track', branch, f'{remote}/{branch}')
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
    while True:
        answer = read_input(f"{prompt} (y/n)", prefix='> ').strip().lower()
        if answer in ('y', 'n'):
            return answer == 'y'
        note("请输入 y 或 n。", 'warn')


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
    remote = get_default_remote()
    for branch in all_branches:
        ref = branch if branch in local_branches else f'{remote}/{branch}'
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
        parts = line.split('\x1f', 2)
        if len(parts) != 3:
            continue
        sha, timestamp, subject = parts
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
        parts = line.split('\x1f', 3)
        if len(parts) != 4:
            continue
        sha, parents_text, timestamp, subject = parts
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
        remote = get_default_remote()
        return f'{remote}/{branch}'
    return branch


def report_has_ref(branch):
    local_branches, remote_branches = report_branch_sets(report_repo_key())
    return branch in local_branches or branch in remote_branches


def report_normalize_creation_source(branch, base, source):
    if not source:
        return None
    source = source.strip()
    if source == 'HEAD':
        return 'HEAD'
    remote = get_default_remote()
    if source.startswith(f'{remote}/'):
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
    if not report_has_ref(branch):
        return []
    compare_base = source or base
    return report_first_unique_commits(compare_base, branch)


def report_first_unique_commits(base, branch):
    if not report_has_ref(base) or not report_has_ref(branch):
        return []
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
    if not report_has_ref(base) or not report_has_ref(branch):
        return None
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
        parts = line.split('\x1f', 2)
        if len(parts) != 3:
            continue
        sha, timestamp, subject = parts
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


def build_branch_report_markdown():
    repo = Path.cwd().resolve()
    base = get_master_branch() or get_current_branch()
    current = get_current_branch()
    events = collect_report_events()
    branches = report_branch_order(base, get_local_branches(), events, current=current)
    start_sha = report_merge_base(base, current) if current != base else None
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tracking_commits = report_tracking_commits_for_branch(current, start_sha) if current != base else report_tracking_commits()

    return render_markdown_report(
        repo=repo,
        generated_at=generated_at,
        base=base,
        current=current,
        branches=branches,
        events=events,
        tracking_commits=tracking_commits,
    )


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


def build_branch_report_html():
    repo = Path.cwd().resolve()
    base = get_master_branch() or get_current_branch()
    current = get_current_branch()
    events = collect_report_events()
    branches = report_branch_order(base, get_local_branches(), events, current=current)
    start_sha = report_merge_base(base, current) if current != base else None
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    feature_counts = branch_counts(('feature_', 'bugfix_'), is_managed_feature_branch)
    integration_counts = branch_counts(('dev_', 'release_'), is_managed_integration_branch)
    tracking_commits = report_tracking_commits_for_branch(current, start_sha) if current != base else report_tracking_commits()

    return render_html_report(
        repo=repo,
        generated_at=generated_at,
        base=base,
        current=current,
        branches=branches,
        events=events,
        tracking_commits=tracking_commits,
        feature_counts=feature_counts,
        integration_counts=integration_counts,
        all_tracking_count=len(report_tracking_commits()),
    )


def build_branch_report():
    return build_branch_report_markdown()


def clear_report_cache():
    report_branch_sets.cache_clear()
    report_branch_head_map.cache_clear()
    _report_branch_source.cache_clear()
    _report_branch_creation_reflog.cache_clear()


def generate_branch_report(output_path=None):
    clear_report_cache()
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


def run_git_in_repo(repo, *args):
    result = subprocess.run(
        ['git', *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0, result.stdout.strip(), result.stderr.strip()


def git_output_in_repo(repo, *args):
    ok, out, _ = run_git_in_repo(repo, *args)
    return out if ok else ''


def resolve_script_update_source():
    current_script = Path(__file__).resolve()
    candidates = []
    metadata = load_install_metadata()
    if metadata:
        candidates.append({
            'repo': metadata.get('source_repo', ''),
            'branch': metadata.get('source_repo_branch', ''),
            'remote_name': metadata.get('source_remote_name', ''),
            'source_script': metadata.get('source_script', ''),
            'install_script': metadata.get('source_install_script', ''),
        })

    local_install = current_script.with_name('dreo_branch_install.py')
    local_repo = git_output_in_repo(current_script.parent, 'rev-parse', '--show-toplevel')
    if local_install.exists() and local_repo:
        candidates.append({
            'repo': local_repo,
            'branch': git_output_in_repo(current_script.parent, 'rev-parse', '--abbrev-ref', 'HEAD'),
            'remote_name': 'origin' if git_output_in_repo(current_script.parent, 'remote', 'get-url', 'origin') else '',
            'source_script': str(current_script),
            'install_script': str(local_install),
        })

    for item in candidates:
        repo = Path(str(item.get('repo') or '')).expanduser()
        source_script = Path(str(item.get('source_script') or '')).expanduser()
        install_script = Path(str(item.get('install_script') or '')).expanduser()
        if repo.is_dir() and source_script.is_file() and install_script.is_file():
            return {
                'repo': repo,
                'branch': str(item.get('branch') or '').strip(),
                'remote_name': str(item.get('remote_name') or '').strip(),
                'source_script': source_script,
                'install_script': install_script,
            }
    return None


def update_script_menu():
    header("更新脚本", icon=UI['build'])
    source = resolve_script_update_source()
    if not source:
        note("未找到脚本源码仓库信息，无法自动更新。请先重新执行安装器。", 'error')
        return False

    repo = source['repo']
    branch = source['branch']
    remote_name = source['remote_name']
    source_script = source['source_script']
    install_script = source['install_script']

    if not branch or branch == 'HEAD':
        note("源码仓库当前不在可更新的本地分支上，无法自动更新。", 'error')
        return False
    if not remote_name:
        note("源码仓库未配置可用远端，无法自动更新。", 'error')
        return False

    note(f"源码仓库: {repo}", 'tip')
    note(f"更新分支: {branch}", 'tip')
    with LoadingIndicator("正在从脚本仓库拉取最新代码"):
        ok, _, err = run_git_in_repo(repo, 'fetch', remote_name)
        if not ok:
            note(f"拉取远端信息失败: {err}", 'error')
            return False
        ok, _, err = run_git_in_repo(repo, 'pull', '--ff-only', remote_name, branch)
        if not ok:
            note(f"更新源码仓库失败: {err}", 'error')
            return False

    with LoadingIndicator("正在更新已安装脚本"):
        result = subprocess.run(
            [sys.executable, str(install_script), '--action', 'update', '--source', str(source_script)],
            text=True,
            capture_output=True,
        )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        note("安装器更新失败。", 'error')
        return False

    note(f"当前版本: {current_version_label()}", 'success')
    note("脚本已更新，重新启动后可使用新版本。", 'tip')
    return True


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


def do_merge(source_branch, action_label="合并", display_branch=None):
    """将 source_branch 合并到当前分支，处理冲突。返回是否成功。"""
    current = get_current_branch()
    label = display_branch or source_branch
    print(f"\n  {icon_slot(UI['merge'], '36')} {action_label} [{label}] → [{current}] ...")
    ok, out, err = run_git('merge', '--no-ff', source_branch)
    if ok:
        note(f"{action_label}成功: {label}", 'success')
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
                note(f"rerere 自动重用了历史解决方案，{action_label}完成: {label}", 'success')
                note("请检查自动解决的文件是否符合预期。", 'tip')
                return True
            note(f"自动提交失败: {commit_err}", 'error')
        return handle_conflict(label, action_label=action_label)
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
        branch_name = f"{branch_type}_{name}_{date_suffix}"
        if not is_valid_branch_name(branch_name):
            note("分支名称包含非法字符或不符合 Git 规范，请重新输入。", 'warn')
            continue
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
    remote = get_default_remote()
    with LoadingIndicator(f"正在同步 {base} 最新代码"):
        ok, _, _ = run_git('pull', remote, base)
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
        int_branch = f"{env_prefix}_{version}_{date_suffix}"
        if not is_valid_branch_name(int_branch):
            note("包含非法字符或不符合 Git 规范，请重新输入。", 'warn')
            continue
        break

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

    local_set = set(get_local_branches())
    remote_set = set(get_remote_branches())
    existing = [b for b in merged_branches if b in local_set or b in remote_set]
    missing  = [b for b in merged_branches if b not in local_set and b not in remote_set]

    print(f"\n  {icon_slot(UI['records'], '36')} 检测到以下开发分支曾被集成到 [{int_branch}]：")
    branch_lines = []
    for b in merged_branches:
        if b in remote_set and b in local_set:
            tag = ' [本地 + 远端，更新时将优先使用远端]'
        elif b in remote_set:
            tag = ' [仅远端存在，更新时将直接使用远端]'
        elif b in local_set:
            tag = ' [仅本地存在]'
        else:
            tag = ' [本地与远端均不存在，将跳过]'
        branch_lines.append(f"{b}{tag}")
    print_list(branch_lines)

    if not existing:
        note("所有已集成的开发分支都已删除或不可用，本次无可同步内容，已跳过。", 'warn')
        summary_block("同步结果汇总", [
            ('⏭️', f"无可同步的已集成开发分支: {int_branch}"),
            (UI['integration_branch'], f"当前所在集成分支: {get_current_branch()}"),
        ])
        return True
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
        if branch not in local_set and branch in remote_set:
            if not ensure_local_branch(branch):
                failed.append(branch)
                continue
            local_set.add(branch)
        elif branch not in local_set and branch not in remote_set:
            failed.append(branch)
            continue
        source_ref = branch_source_ref(branch)
        _, ahead, _ = run_git('rev-list', '--count', f'{int_branch}..{source_ref}')
        new_commits = int(ahead) if ahead.isdigit() else 0

        if new_commits == 0:
            print(f"\n  {icon_slot(UI['skip'], '33')} [{branch}] 无新增提交，跳过。")
            skipped.append(branch)
            continue

        latest_subject = latest_commit_subject(source_ref)
        latest_info = f"；最新提交: {latest_subject}" if latest_subject else ""
        print(f"\n  {icon_slot(UI['merge'], '36')} [{branch}] 有 {new_commits} 个新提交{latest_info}，执行合并...")
        if do_merge(source_ref, display_branch=branch):
            succeeded.append(branch)
        else:
            failed.append(branch)

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
            remote = get_default_remote()
            rok, _, rerr = run_git('push', remote, '--delete', branch)
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

    with LoadingIndicator("正在整理可删除分支列表"):
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
                display_options.append(f"{group_name}｜{format_branch_with_sets(b, local_branches, remote_branches)}")

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
    print_list([format_branch_with_sets(branch, local_branches, remote_branches) for branch in selected])
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

    remote = get_default_remote()
    with LoadingIndicator(f"正在同步 {base} 最新代码"):
        ok, _, _ = run_git('pull', remote, base)
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

    remote_branches = get_remote_branches()
    local_branches = set(get_local_branches())
    remote_only = [
        branch for branch in remote_branches
        if branch not in local_branches
    ]
    remote_only = sort_branches_by_date(remote_only, limit=len(remote_only))

    if not remote_only:
        note("没有检测到仅存在于远端的分支。", 'tip')
        return

    print(f"  {icon_slot(UI['select'], '36')} 请选择要拉取到本地的远端分支：")
    idx = select_one([f"{branch}  [远端]" for branch in remote_only])
    if idx is None:
        return False

    branch = remote_only[idx]
    remote = get_default_remote()
    print(f"\n  {icon_slot('📥', '36')} 操作：{remote}/{branch} → 本地 {branch}")
    if not confirm("确认拉取并切换到该分支？"):
        note("已取消。", 'warn')
        return False

    if ensure_local_branch(branch, checkout=True):
        note(f"已将远端分支拉取到本地并切换到: {branch}", 'success')


# ─── 菜单系统 ────────────────────────────────────────────────────

def show_status():
    current = get_current_branch()
    local_set = set(get_local_branches())
    remote_set = set(get_remote_branches())
    features = branch_counts(('feature_', 'bugfix_'), is_managed_feature_branch, local_set, remote_set)
    integrations = branch_counts(('dev_', 'release_'), is_managed_integration_branch, local_set, remote_set)
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
    print(f"  版本: {paint(current_version_label(), '1', '33')}")
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
        ("更新脚本",                        update_script_menu),
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
