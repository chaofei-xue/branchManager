#!/usr/bin/env python3
"""
在任意 Git 仓库中生成分支合并报告。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


TRACKING_RE = re.compile(r"^\[DREO-MERGE\]\s+(\S+)\s+<-\s+(.+)$")
MERGE_RE = re.compile(r"^Merge branch '(.+?)' into (.+)$")


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    sha: str
    kind: str
    description: str
    branch: str = ""
    source: str = ""
    target: str = ""


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 执行失败\n标准输出:\n{result.stdout}\n标准错误:\n{result.stderr}"
        )
    return result.stdout.strip()


def ensure_git_repo(repo: Path) -> None:
    run_git(repo, "rev-parse", "--is-inside-work-tree")


def get_current_branch(repo: Path) -> str:
    return run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def get_local_branches(repo: Path) -> list[str]:
    output = run_git(repo, "branch", "--format=%(refname:short)")
    return [line.strip() for line in output.splitlines() if line.strip()]


def get_base_branch(repo: Path) -> str:
    branches = get_local_branches(repo)
    if "master" in branches:
        return "master"
    if "main" in branches:
        return "main"
    return get_current_branch(repo)


def is_integration_branch(branch: str) -> bool:
    return branch.startswith("dev_") or branch.startswith("release_")


def parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def read_commits(repo: Path, *log_args: str) -> list[dict[str, str]]:
    output = run_git(
        repo,
        "log",
        *log_args,
        "--pretty=format:%H%x1f%ad%x1f%s",
        "--date=iso-strict",
    )
    commits = []
    for line in output.splitlines():
        sha, timestamp, subject = line.split("\x1f", 2)
        commits.append({
            "sha": sha,
            "timestamp": timestamp,
            "subject": subject,
        })
    return commits


def first_unique_commits(repo: Path, base: str, branch: str) -> list[dict[str, str]]:
    rev = f"{base}..{branch}"
    return read_commits(repo, "--reverse", "--no-merges", rev)


def base_first_parent_commits(repo: Path, base: str) -> list[dict[str, str]]:
    return read_commits(repo, "--reverse", "--first-parent", "--no-merges", base)


def merge_commits(repo: Path) -> list[dict[str, str]]:
    return read_commits(repo, "--reverse", "--merges", "--all")


def tracking_commits(repo: Path) -> list[dict[str, str]]:
    commits = read_commits(repo, "--reverse", "--all")
    return [commit for commit in commits if TRACKING_RE.match(commit["subject"])]


def collect_events(repo: Path) -> list[Event]:
    base = get_base_branch(repo)
    branches = get_local_branches(repo)
    seen: set[tuple[str, str]] = set()
    events: list[Event] = []

    for commit in base_first_parent_commits(repo, base):
        key = ("base", commit["sha"])
        if key in seen:
            continue
        seen.add(key)
        events.append(
            Event(
                timestamp=parse_timestamp(commit["timestamp"]),
                sha=commit["sha"],
                kind="base_commit",
                description=f"{base} 提交 {commit['subject']}",
                branch=base,
            )
        )

    for branch in branches:
        if branch == base:
            continue
        if is_integration_branch(branch):
            continue
        unique = first_unique_commits(repo, base, branch)
        for index, commit in enumerate(unique):
            key = ("branch", branch, commit["sha"])
            if key in seen:
                continue
            seen.add(key)
            if index == 0:
                desc = f"从 {base} 拉出 {branch}，并提交 {commit['subject']}"
            else:
                desc = f"{branch} 提交 {commit['subject']}"
            events.append(
                Event(
                    timestamp=parse_timestamp(commit["timestamp"]),
                    sha=commit["sha"],
                    kind="branch_commit",
                    description=desc,
                    branch=branch,
                )
            )

    for commit in merge_commits(repo):
        match = MERGE_RE.match(commit["subject"])
        if not match:
            continue
        source, target = match.groups()
        events.append(
            Event(
                timestamp=parse_timestamp(commit["timestamp"]),
                sha=commit["sha"],
                kind="merge",
                description=f"将 {source} 合入 {target}",
                source=source,
                target=target,
            )
        )

    for commit in tracking_commits(repo):
        match = TRACKING_RE.match(commit["subject"])
        if not match:
            continue
        target, sources = match.groups()
        events.append(
            Event(
                timestamp=parse_timestamp(commit["timestamp"]),
                sha=commit["sha"],
                kind="tracking",
                description=f"写入追踪提交 {commit['subject']}",
                source=sources,
                target=target,
            )
        )

    events.sort(key=lambda item: (item.timestamp, item.sha, item.kind))
    return events


def build_sequence(events: list[Event]) -> list[str]:
    lines = []
    for index, event in enumerate(events, 1):
        time_text = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{index}. {time_text}：{event.description}（{event.sha[:7]}）")
    return lines


def group_timeline(events: list[Event]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for event in events:
        key = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        grouped[key].append(event.description)
    return dict(grouped)


def mermaid_safe_period(timestamp: str) -> str:
    return timestamp.replace(":", "-")


def mermaid_safe_text(text: str) -> str:
    return text.replace(":", "：")


def mermaid_timeline(events: list[Event]) -> str:
    grouped = group_timeline(events)
    lines = [
        "```mermaid",
        "timeline",
        "    title 分支处理时间线",
    ]
    for timestamp, items in grouped.items():
        first = True
        for item in items:
            period = mermaid_safe_period(timestamp) if first else " " * len(mermaid_safe_period(timestamp))
            lines.append(f"    {period} : {mermaid_safe_text(item)}")
            first = False
    lines.append("```")
    return "\n".join(lines)


def safe_node_id(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"n_{sanitized}"
    return sanitized


def mermaid_flowchart(base: str, branches: list[str], events: list[Event]) -> str:
    lines = [
        "```mermaid",
        "flowchart LR",
    ]
    all_nodes = [base] + [branch for branch in branches if branch != base]
    for branch in all_nodes:
        lines.append(f'    {safe_node_id(branch)}["{branch}"]')

    added_edges: set[tuple[str, str, str]] = set()
    for event in events:
        if event.kind == "branch_commit" and event.branch and event.description.startswith(f"从 {base} 拉出 "):
            edge = (base, event.branch, "创建分支")
            if edge not in added_edges:
                added_edges.add(edge)
                lines.append(
                    f'    {safe_node_id(base)} -->|创建分支| {safe_node_id(event.branch)}'
                )
        elif event.kind == "merge" and event.source and event.target:
            edge = (event.source, event.target, event.timestamp.strftime("%H:%M:%S"))
            if edge not in added_edges:
                added_edges.add(edge)
                lines.append(
                    f'    {safe_node_id(event.source)} -->|{event.timestamp.strftime("%H:%M:%S")} merge| {safe_node_id(event.target)}'
                )

    lines.append("```")
    return "\n".join(lines)


def raw_tracking_section(repo: Path) -> list[str]:
    commits = tracking_commits(repo)
    if not commits:
        return ["- 未发现 `[DREO-MERGE]` 追踪提交。"]
    lines = []
    for commit in commits:
        lines.append(
            f"- {commit['timestamp'].replace('T', ' ')}  {commit['subject']} ({commit['sha'][:7]})"
        )
    return lines


def build_report(repo: Path) -> str:
    ensure_git_repo(repo)
    base = get_base_branch(repo)
    current = get_current_branch(repo)
    branches = get_local_branches(repo)
    events = collect_events(repo)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# Git 分支合并报告",
        "",
        f"- 仓库路径：`{repo}`",
        f"- 生成时间：`{generated_at}`",
        f"- 基线分支：`{base}`",
        f"- 当前分支：`{current}`",
        "",
        "## 分支概览",
        "",
    ]
    for branch in branches:
        lines.append(f"- `{branch}`")

    lines.extend([
        "",
        "## 推断的处理顺序",
        "",
    ])
    lines.extend(build_sequence(events) or ["1. 未识别到可分析的提交记录。"])

    lines.extend([
        "",
        "## 时间线图",
        "",
        mermaid_timeline(events),
        "",
        "## 分支流转图",
        "",
        mermaid_flowchart(base, branches, events),
        "",
        "## 追踪提交",
        "",
    ])
    lines.extend(raw_tracking_section(repo))
    lines.append("")
    return "\n".join(lines)


def generate_report(repo: Path, output: Path) -> Path:
    report = build_report(repo)
    output.write_text(report, encoding="utf-8")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 Git 分支合并报告。")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Git 仓库路径，默认使用当前目录。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出文件路径，默认写入 <repo>/branch_merge_report.md。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.expanduser().resolve()
    output = (
        args.output.expanduser().resolve()
        if args.output
        else repo / "branch_merge_report.md"
    )
    path = generate_report(repo, output)
    print(f"已生成分支合并报告: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
