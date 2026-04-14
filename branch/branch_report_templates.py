#!/usr/bin/env python3
"""
分支报告模板渲染。
"""

from __future__ import annotations

from collections import defaultdict
from html import escape
import re
import unicodedata


def mermaid_safe_period(timestamp_text):
    return timestamp_text.replace(':', '-')


def mermaid_safe_text(text):
    return text.replace(':', '：')


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


def build_tracking_section_markdown(tracking_commits):
    if not tracking_commits:
        return ['- 未发现 `[DREO-MERGE]` 追踪提交。']

    lines = []
    for commit in tracking_commits:
        timestamp_text = commit['timestamp'].isoformat(sep=' ', timespec='seconds')
        lines.append(f"- {timestamp_text}  {commit['subject']} ({commit['sha'][:7]})")
    return lines


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


def _display_width(text):
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
    return width


def wrap_svg_text(text, max_width=26, max_lines=3):
    lines = []
    current = []
    current_width = 0

    for char in text:
        char_width = _display_width(char)
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


def build_report_flow_svg(base, branches, events):
    if not branches:
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
    for branch in branches:
        branch_lines = wrap_svg_text(branch, max_width=22, max_lines=2)
        branch_width = min(max(168, max(_display_width(line) for line in branch_lines) * 8 + 40), 260)
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
        card_width = min(max(220, max(_display_width(line) for line in lines) * 7 + 40), 340)
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

    for branch in branches:
        card = branch_cards[branch]
        x = card['x']
        card_y = header_y if len(card['lines']) == 1 else 14
        svg.append(f'<line x1="{x}" y1="{top_y}" x2="{x}" y2="{lane_bottom}" class="lane-line" />')
        svg.append(
            f'<rect x="{x - card["width"] / 2}" y="{card_y}" width="{card["width"]}" '
            f'height="{card["height"]}" rx="14" class="branch-card" />'
        )
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
        svg.append(
            f'<rect x="{mid_x - block["card_width"] / 2}" y="{y - block["card_height"] / 2}" '
            f'width="{block["card_width"]}" height="{block["card_height"]}" rx="12" '
            f'class="event-label-card" filter="url(#card-shadow)" />'
        )
        svg.append(svg_multiline_text(mid_x, y + 1, lines, 'event-label', line_height=16))

    svg.append('</svg>')
    return ''.join(svg)


def build_tracking_section_html(tracking_commits):
    if not tracking_commits:
        return '<p class="empty">未发现追踪提交。</p>'

    rows = ['<table class="tracking-table"><thead><tr><th>时间</th><th>提交信息</th><th>SHA</th></tr></thead><tbody>']
    for commit in tracking_commits:
        timestamp_text = commit['timestamp'].isoformat(sep=' ', timespec='seconds')
        rows.append(
            f'<tr><td>{escape(timestamp_text)}</td>'
            f'<td>{escape(commit["subject"])}</td>'
            f'<td><code>{escape(commit["sha"][:7])}</code></td></tr>'
        )
    rows.append('</tbody></table>')
    return ''.join(rows)


def render_markdown_report(*, repo, generated_at, base, current, branches, events, tracking_commits):
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
    lines.extend(build_tracking_section_markdown(tracking_commits))
    lines.append('')
    return '\n'.join(lines)


def render_html_report(
    *,
    repo,
    generated_at,
    base,
    current,
    branches,
    events,
    tracking_commits,
    feature_counts,
    integration_counts,
    all_tracking_count,
):
    branch_chips = ''.join(
        f'<span class="branch-chip">{escape(branch)}</span>' for branch in branches
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
      <article class="stat-card"><div class="label">追踪提交</div><div class="value">{all_tracking_count}</div><div class="label">含 [DREO-MERGE]</div></article>
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
        {build_tracking_section_html(tracking_commits)}
      </article>
    </section>
  </main>
</body>
</html>
"""
