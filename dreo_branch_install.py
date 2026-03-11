#!/usr/bin/env python3
"""
安装 dreo_branch_manager.py 到 macOS / Linux 用户环境。

功能：
1. 复制 dreo_branch_manager.py 到用户目录。
2. 生成 dreo_branch_manager / branch / dbm 三个启动命令。
3. 自动为常见 shell 配置 PATH，确保新终端可直接运行。
4. 支持安装、更新和卸载。
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path


APP_NAME = "dreo_branch_manager"
ALIASES = ("dreo_branch_manager", "branch", "dbm")
INSTALL_MARKER = "dreo-branch-manager"
POSIX_RC_FILES = (".zshrc", ".bashrc", ".bash_profile", ".profile")
FISH_RC_FILE = ".config/fish/config.fish"
ACTION_INSTALL = "install"
ACTION_UPDATE = "update"
ACTION_UNINSTALL = "uninstall"


def note(message: str) -> None:
    print(f"[INFO] {message}")


def success(message: str) -> None:
    print(f"[ OK ] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def fail(message: str, code: int = 1) -> None:
    print(f"[FAIL] {message}", file=sys.stderr)
    sys.exit(code)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="安装、更新或卸载 Dreo 分支管理脚本。"
    )
    parser.add_argument(
        "--action",
        choices=(ACTION_INSTALL, ACTION_UPDATE, ACTION_UNINSTALL),
        help="执行指定操作；不传时进入交互菜单。",
    )
    parser.add_argument(
        "--emit-activation",
        action="store_true",
        help="仅输出当前 shell 立即生效所需的环境变量命令。",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="目标用户 HOME 目录，默认使用当前用户 HOME。",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().with_name("dreo_branch_manager.py"),
        help="待安装的 dreo_branch_manager.py 路径。",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=None,
        help="脚本安装目录，默认是 <HOME>/.local/share/dreo_branch_manager。",
    )
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=None,
        help="命令安装目录，默认是 <HOME>/.local/bin。",
    )
    return parser.parse_args()


def ensure_supported_platform() -> None:
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        fail("当前安装器仅支持 macOS 和 Linux。")


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    home = args.home.expanduser().resolve()
    install_dir = (
        args.install_dir.expanduser().resolve()
        if args.install_dir
        else home / ".local" / "share" / APP_NAME
    )
    bin_dir = (
        args.bin_dir.expanduser().resolve()
        if args.bin_dir
        else home / ".local" / "bin"
    )
    source = args.source.expanduser().resolve()
    target_script = install_dir / "dreo_branch_manager.py"
    return {
        "home": home,
        "source": source,
        "install_dir": install_dir,
        "bin_dir": bin_dir,
        "target_script": target_script,
    }


def ensure_source_exists(source: Path) -> None:
    if not source.is_file():
        fail(f"未找到源脚本: {source}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def shell_name() -> str:
    shell = os.environ.get("SHELL", "")
    return Path(shell).name if shell else ""


def activation_command(bin_dir: Path) -> str:
    current_shell = shell_name()
    if current_shell == "fish":
        return f'set -gx PATH "{bin_dir}" $PATH'
    return f'export PATH="{bin_dir}:$PATH"'


def remove_file_if_exists(path: Path, description: str) -> bool:
    if not path.exists():
        return False
    path.unlink()
    success(f"已删除{description}: {path}")
    return True


def remove_dir_if_empty(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        path.rmdir()
    except OSError:
        return False
    success(f"已清理空目录: {path}")
    return True


def copy_main_script(source: Path, target_script: Path) -> None:
    ensure_dir(target_script.parent)
    shutil.copy2(source, target_script)
    mode = target_script.stat().st_mode
    target_script.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    success(f"已安装主脚本: {target_script}")


def launcher_content(target_script: Path) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f'exec python3 "{target_script}" "$@"',
            "",
        ]
    )


def write_file(path: Path, content: str, executable: bool = False) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")
    if executable:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_launchers(bin_dir: Path, target_script: Path) -> list[Path]:
    installed = []
    content = launcher_content(target_script)
    for alias in ALIASES:
        launcher = bin_dir / alias
        write_file(launcher, content, executable=True)
        installed.append(launcher)
        success(f"已创建启动命令: {launcher}")
    return installed


def replace_managed_block(original: str, block: str, begin: str, end: str) -> str:
    start = original.find(begin)
    finish = original.find(end)
    if start != -1 and finish != -1 and finish > start:
        finish += len(end)
        trimmed = original[:start].rstrip()
        suffix = original[finish:].lstrip("\n")
        new_text = trimmed + "\n\n" + block
        if suffix:
            new_text += "\n\n" + suffix
        return new_text.rstrip() + "\n"

    text = original.rstrip()
    if text:
        text += "\n\n"
    return text + block + "\n"


def remove_managed_block(original: str, begin: str, end: str) -> str:
    start = original.find(begin)
    finish = original.find(end)
    if start == -1 or finish == -1 or finish <= start:
        return original

    finish += len(end)
    prefix = original[:start].rstrip()
    suffix = original[finish:].lstrip("\n")
    if prefix and suffix:
        return prefix + "\n\n" + suffix.rstrip() + "\n"
    if prefix:
        return prefix.rstrip() + "\n"
    if suffix:
        return suffix.rstrip() + "\n"
    return ""


def posix_path_block(bin_dir: Path) -> str:
    begin = f"# >>> {INSTALL_MARKER} path >>>"
    end = f"# <<< {INSTALL_MARKER} path <<<"
    body = "\n".join(
        [
            begin,
            f'if [ -d "{bin_dir}" ]; then',
            '  case ":$PATH:" in',
            f'    *:"{bin_dir}":*) ;;',
            f'    *) export PATH="{bin_dir}:$PATH" ;;',
            "  esac",
            "fi",
            end,
        ]
    )
    return body


def fish_path_block(bin_dir: Path) -> str:
    begin = f"# >>> {INSTALL_MARKER} path >>>"
    end = f"# <<< {INSTALL_MARKER} path <<<"
    body = "\n".join(
        [
            begin,
            f'if test -d "{bin_dir}"',
            f'    if not contains "{bin_dir}" $PATH',
            f'        fish_add_path -m "{bin_dir}"',
            "    end",
            "end",
            end,
        ]
    )
    return body


def update_rc_file(path: Path, block: str, begin: str, end: str) -> bool:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = replace_managed_block(original, block, begin, end)
    if updated == original:
        return False
    ensure_dir(path.parent)
    path.write_text(updated, encoding="utf-8")
    return True


def remove_rc_managed_block(path: Path, begin: str, end: str) -> bool:
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    updated = remove_managed_block(original, begin, end)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def configure_shell_paths(home: Path, bin_dir: Path) -> list[Path]:
    updated_files = []
    posix_block = posix_path_block(bin_dir)
    begin = f"# >>> {INSTALL_MARKER} path >>>"
    end = f"# <<< {INSTALL_MARKER} path <<<"

    for name in POSIX_RC_FILES:
        rc_path = home / name
        if update_rc_file(rc_path, posix_block, begin, end):
            updated_files.append(rc_path)

    fish_path = home / FISH_RC_FILE
    fish_block = fish_path_block(bin_dir)
    if update_rc_file(fish_path, fish_block, begin, end):
        updated_files.append(fish_path)

    return updated_files


def remove_shell_paths(home: Path) -> list[Path]:
    updated_files = []
    begin = f"# >>> {INSTALL_MARKER} path >>>"
    end = f"# <<< {INSTALL_MARKER} path <<<"

    for name in POSIX_RC_FILES:
        rc_path = home / name
        if remove_rc_managed_block(rc_path, begin, end):
            updated_files.append(rc_path)

    fish_path = home / FISH_RC_FILE
    if remove_rc_managed_block(fish_path, begin, end):
        updated_files.append(fish_path)

    return updated_files


def print_summary(paths: dict[str, Path], launchers: list[Path], updated_files: list[Path]) -> None:
    print()
    success("安装完成。")
    print()
    note(f"主脚本位置: {paths['target_script']}")
    note(f"命令目录: {paths['bin_dir']}")
    note("可直接使用的命令:")
    for launcher in launchers:
        print(f"  - {launcher.name}")

    if updated_files:
        note("已更新的 shell 配置文件:")
        for path in updated_files:
            print(f"  - {path}")
    else:
        note("shell 配置文件无需改动。")

    print()
    note("当前脚本无法直接改写父终端环境；若要让当前终端立即生效，请执行：")
    print(f"  - {activation_command(paths['bin_dir'])}")
    print()
    note("或者执行以下任一操作后再试：")
    print("  - 重新打开一个终端窗口")
    print("  - 执行: source ~/.zshrc")
    print("  - 执行: source ~/.bashrc")
    print()
    note("安装完成后，可在任意 Git 仓库目录中直接运行：")
    print("  - dreo_branch_manager")
    print("  - branch")
    print("  - dbm")


def print_update_summary(paths: dict[str, Path], launchers: list[Path], updated_files: list[Path]) -> None:
    print()
    success("更新完成。")
    print()
    note(f"已更新主脚本: {paths['target_script']}")
    note("可继续使用以下命令:")
    for launcher in launchers:
        print(f"  - {launcher.name}")

    if updated_files:
        note("已同步修正的 shell 配置文件:")
        for path in updated_files:
            print(f"  - {path}")
    print()
    note("若希望当前终端立即生效，请执行：")
    print(f"  - {activation_command(paths['bin_dir'])}")


def print_uninstall_summary(
    removed_files: list[Path],
    updated_files: list[Path],
    source: Path,
) -> None:
    print()
    success("卸载完成。")
    print()
    if removed_files:
        note("已删除的安装文件:")
        for path in removed_files:
            print(f"  - {path}")
    else:
        note("未发现已安装文件。")

    if updated_files:
        note("已清理的 shell 配置文件:")
        for path in updated_files:
            print(f"  - {path}")
    else:
        note("shell 配置文件中未发现受管配置。")

    note(f"当前目录中的源脚本已保留不动: {source}")


def install_or_update(args: argparse.Namespace, action: str) -> None:
    ensure_supported_platform()
    paths = resolve_paths(args)
    ensure_source_exists(paths["source"])
    ensure_dir(paths["install_dir"])
    ensure_dir(paths["bin_dir"])

    copy_main_script(paths["source"], paths["target_script"])
    launchers = install_launchers(paths["bin_dir"], paths["target_script"])
    updated_files = configure_shell_paths(paths["home"], paths["bin_dir"])
    if action == ACTION_INSTALL:
        print_summary(paths, launchers, updated_files)
    else:
        print_update_summary(paths, launchers, updated_files)


def uninstall(args: argparse.Namespace) -> None:
    ensure_supported_platform()
    paths = resolve_paths(args)
    removed_files = []

    for alias in ALIASES:
        launcher = paths["bin_dir"] / alias
        if remove_file_if_exists(launcher, "启动命令"):
            removed_files.append(launcher)

    if (
        paths["target_script"].exists()
        and paths["target_script"].resolve() != paths["source"]
    ):
        if remove_file_if_exists(paths["target_script"], "主脚本"):
            removed_files.append(paths["target_script"])
    elif paths["target_script"].exists():
        warn(f"跳过删除源脚本本体: {paths['target_script']}")

    updated_files = remove_shell_paths(paths["home"])

    remove_dir_if_empty(paths["install_dir"])
    remove_dir_if_empty(paths["bin_dir"])
    remove_dir_if_empty(paths["home"] / ".local" / "share")
    remove_dir_if_empty(paths["home"] / ".local")

    print_uninstall_summary(removed_files, updated_files, paths["source"])


def prompt_action() -> str:
    print()
    print("Dreo 分支管理工具安装器")
    print("1. 安装   （将当前目录下的 dreo_branch_manager.py 安装到系统）")
    print("2. 更新   （更新已安装的 dreo_branch_manager.py 文件）")
    print("3. 卸载   （完全卸载已安装文件与配置，不删除当前目录源码）")
    print("0. 退出")
    while True:
        choice = input("\n请选择操作 [0-3]: ").strip()
        mapping = {
            "1": ACTION_INSTALL,
            "2": ACTION_UPDATE,
            "3": ACTION_UNINSTALL,
            "0": "",
        }
        if choice in mapping:
            return mapping[choice]
        warn("无效输入，请输入 0-3。")


def dispatch(args: argparse.Namespace, action: str) -> None:
    if action == ACTION_INSTALL:
        install_or_update(args, ACTION_INSTALL)
    elif action == ACTION_UPDATE:
        install_or_update(args, ACTION_UPDATE)
    elif action == ACTION_UNINSTALL:
        uninstall(args)
    else:
        note("已退出。")


def main() -> None:
    args = parse_args()
    if args.emit_activation:
        paths = resolve_paths(args)
        print(activation_command(paths["bin_dir"]))
        return
    action = args.action
    if not action:
        if not sys.stdin.isatty():
            fail("当前是非交互环境，请通过 --action 指定 install、update 或 uninstall。")
        action = prompt_action()
    dispatch(args, action)


if __name__ == "__main__":
    main()
