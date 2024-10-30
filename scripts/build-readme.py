#!/usr/bin/env python3
"""Generate a self-contained, interactive HTML version of README.md.

Usage:
    python3 scripts/build-readme.py [input.md] [output.html]

Defaults to README.md -> README.html in the repository root.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "section"


def strip_md_inline(text: str) -> str:
    s = re.sub(r"`([^`]+)`", r"\1", text)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    return s.strip()


def split_table_row(line: str) -> list[str]:
    """Split a GFM table row into cells without splitting on pipes inside backticks."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
        elif ch == "\\" and i + 1 < len(s) and s[i + 1] == "|" and not in_code:
            buf.append("|")
            i += 1
        elif ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def is_table_sep(line: str) -> bool:
    s = line.strip()
    if "|" not in s:
        return False
    cells = split_table_row(s)
    if not cells:
        return False
    return all(bool(re.match(r"^:?-{2,}:?$", c)) for c in cells if c != "") and any(
        bool(re.match(r"^:?-{2,}:?$", c)) for c in cells
    )


def table_alignments(sep_line: str) -> list[str]:
    aligns = []
    for c in split_table_row(sep_line):
        c = c.strip()
        if c.startswith(":") and c.endswith(":"):
            aligns.append("center")
        elif c.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    return aligns


def highlight_code(code: str, lang: str) -> str:
    """Lightweight, zero-dependency syntax highlighting for bash, yaml, and jsonc."""
    escaped = html.escape(code)
    lang = (lang or "").lower()
    if lang not in ("bash", "sh", "shell", "yaml", "yml", "jsonc", "json"):
        return escaped

    out_lines: list[str] = []
    for line in escaped.splitlines():
        if lang in ("bash", "sh", "shell"):
            # Split trailing/full comments outside quotes
            m_full = re.match(r"^(\s*)(#.*)$", line)
            if m_full:
                out_lines.append(f'{m_full.group(1)}<span class="tok-com">{m_full.group(2)}</span>')
                continue
            # Find comment starting with whitespace + #
            parts = re.split(r"(\s+#.*)$", line, maxsplit=1)
            body = parts[0]
            comment = parts[1] if len(parts) > 1 else ""
            # Highlight strings
            body = re.sub(
                r"(&quot;[^&]*?&quot;|&#x27;[^&]*?&#x27;)",
                r'<span class="tok-str">\1</span>',
                body,
            )
            # Highlight flags like --update, -f
            body = re.sub(
                r"(?<=\s)(--?[a-zA-Z0-9][a-zA-Z0-9_-]*)",
                r'<span class="tok-flag">\1</span>',
                body,
            )
            # Highlight leading command/script
            body = re.sub(
                r"^(\s*)(\./\S+|[a-zA-Z0-9_./-]+)",
                r'\1<span class="tok-cmd">\2</span>',
                body,
                count=1,
            )
            if comment:
                comment = f'<span class="tok-com">{comment}</span>'
            out_lines.append(body + comment)
        elif lang in ("yaml", "yml"):
            m_full = re.match(r"^(\s*)(#.*)$", line)
            if m_full:
                out_lines.append(f'{m_full.group(1)}<span class="tok-com">{m_full.group(2)}</span>')
                continue
            m_kv = re.match(r"^(\s*)([a-zA-Z0-9_.-]+)(:)(\s*)(.*)$", line)
            if m_kv:
                indent, key, colon, sp, val = m_kv.groups()
                if val:
                    val = f'<span class="tok-str">{val}</span>'
                out_lines.append(f'{indent}<span class="tok-key">{key}</span>{colon}{sp}{val}')
            else:
                out_lines.append(line)
        elif lang in ("jsonc", "json"):
            parts = re.split(r"(//.*)$", line, maxsplit=1)
            body = parts[0]
            comment = parts[1] if len(parts) > 1 else ""
            # Keys
            body = re.sub(
                r"(&quot;[^&]+?&quot;)(\s*:)",
                r'<span class="tok-key">\1</span>\2',
                body,
            )
            # Remaining string values
            body = re.sub(
                r"(?<!>)(&quot;[^&]*?&quot;)(?!<)",
                r'<span class="tok-str">\1</span>',
                body,
            )
            if comment:
                comment = f'<span class="tok-com">{comment}</span>'
            out_lines.append(body + comment)
    return '\n'.join(out_lines)


class ReadmeConverter:
    """Convert a Markdown README into an interactive HTML document."""

    def __init__(self, md_text: str) -> None:
        self.md_text = md_text
        self.lines = md_text.splitlines()
        self.headings: list[dict[str, Any]] = []
        self.slug_by_title: dict[str, str] = {}
        self.inline_anchors: dict[str, str] = {}
        self._scan_headings()

    def _make_anchor(self, title: str) -> str:
        """Create an HTML anchor element for a heading."""
        anc = slugify(title)
        inner = strip_md_inline(title)
        return f'<strong id="{anc}" class="inline-anchor">{inner}</strong>'

    def _push_toc(self, level: int, title: str) -> dict[str, Any]:
        """Push a new TOC entry and return it."""
        slug = slugify(title)
        entry = {
            'id': slug,
            'title': strip_md_inline(title),
            'level': level,
            'children': [],
        }
        if level == 2:
            self.toc_tree.append(entry)
            self.current_section = entry
        elif level == 3 and self.current_section:
            self.current_section['children'].append(entry)
        return entry

    def _render_heading(self, level: int, text: str) -> str:
        """Render a Markdown heading as an HTML section."""
        slug = slugify(text)
        anchor = self._make_anchor(text)
        toc_entry = self._push_toc(level, text)

        if level == 2:
            return (
                f'<section class="doc-section level-2" id="{slug}" '
                f'data-title="{html.escape(text, quote=True)}">'
            )
        elif level == 3:
            return (
                f'<section class="doc-section level-3" id="{slug}" '
                f'data-title="{html.escape(text, quote=True)}">'
            )
        return f'<h{level} id="{slug}">{anchor}</h{level}>'

    def _render_table(self, lines: list[str]) -> str:
        """Render a Markdown table as an HTML table."""
        if len(lines) < 2:
            return '\n'.join(lines)

        header_parts = split_table_row(lines[0])
        sep_line = lines[1]
        alignments = table_alignments(sep_line)

        rows = []
        for line in lines[2:]:
            parts = split_table_row(line)
            rows.append(parts)

        result = '<table class="readme-table">\n<thead>\n<tr>\n'
        for i, h in enumerate(header_parts):
            align = alignments[i] if i < len(alignments) else 'left'
            result += f'<th style="text-align:{align}">{h}</th>\n'
        result += '</tr>\n</thead>\n<tbody>\n'

        for row in rows:
            result += '<tr>\n'
            for i, cell in enumerate(row):
                align = alignments[i] if i < len(alignments) else 'left'
                result += f'<td style="text-align:{align}">{cell}</td>\n'
            result += '</tr>\n'

        result += '</tbody>\n</table>'
        return result

    def parse_markdown(self, content: str) -> str:
        """Parse Markdown content and return HTML."""
        lines = content.split('\n')
        html_parts = []
        in_code_block = False
        code_lang = ''
        code_lines = []
        in_table = False
        table_lines = []

        for line in lines:
            # Handle code blocks
            if line.strip().startswith('```'):
                if in_code_block:
                    code_html = highlight_code('\n'.join(code_lines), code_lang)
                    html_parts.append(f'<pre><code class="language-{code_lang}">{code_html}</code></pre>')
                    in_code_block = False
                    code_lines = []
                    code_lang = ''
                else:
                    if in_table and table_lines:
                        html_parts.append(self._render_table(table_lines))
                        in_table = False
                        table_lines = []
                    code_lang = line.strip()[3:].strip()
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            # Handle tables
            stripped = line.strip()
            if is_table_sep(stripped):
                if not in_table:
                    in_table = True
                table_lines.append(line)
                continue
            elif in_table and stripped:
                if split_table_row(stripped):
                    table_lines.append(line)
                    continue
                else:
                    html_parts.append(self._render_table(table_lines))
                    in_table = False
                    table_lines = []

            if in_table:
                table_lines.append(line)
                continue

            # Handle headings
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if heading_match:
                level = len(heading_match.group(1))
                text = heading_match.group(2).strip()
                html_parts.append(self._render_heading(level, text))
                continue

            # Handle empty lines
            if not stripped:
                html_parts.append('')
                continue

            # Handle paragraphs
            html_parts.append(f'<p>{html.escape(line)}</p>')

        # Flush remaining content
        if in_code_block and code_lines:
            code_html = highlight_code('\n'.join(code_lines), code_lang)
            html_parts.append(f'<pre><code class="language-{code_lang}">{code_html}</code></pre>')
        if in_table and table_lines:
            html_parts.append(self._render_table(table_lines))

        return '\n'.join(html_parts)

    def build_overview_section(self, doc_title: str) -> str:
        """Build the overview section wrapping all content."""
        return (
            f'<section class="doc-section level-1" id="overview" '
            f'data-title="{html.escape(doc_title, quote=True)}">'
        )

    def render_toc_html(self, toc_tree: list[dict[str, Any]]) -> str:
        """Render the table of contents as nested HTML."""
        parts = []
        for item in toc_tree:
            parts.append('<div class="toc-group" data-group-id="' + item["id"] + '">')
            parts.append(f'<a href="#{item["id"]}">{item["title"]}</a>')
            if item.get('children'):
                for child in item['children']:
                    parts.append(f'<div class="toc-child">')
                    parts.append(f'<a href="#{child["id"]}">{child["title"]}</a>')
                    parts.append('</div>')
            parts.append('</div>')
        return '\n'.join(parts)

    def convert(self, doc_title: str = 'README') -> str:
        """Full conversion pipeline: parse Markdown, build HTML."""
        raw = self.read_input()
        body_html = self.parse_markdown(raw)
        toc_html = self.render_toc_html(self.toc_tree)
        overview = self.build_overview_section(doc_title)

        # Assemble the full document
        doc = HTML_TEMPLATE.replace('<!-- TOC_PLACEHOLDER -->', toc_html)
        doc = doc.replace('<!-- BODY_PLACEHOLDER -->', overview + '\n' + body_html + '\n</section>')
        return doc

    def write_output(self, html_content: str) -> None:
        """Write the HTML content to the output file."""
        self.output_path.write_text(html_content, encoding='utf-8')
        print(f"Generated {self.output_path}")


def main():
    """Entry point for the README-to-HTML converter."""
    input_file = sys.argv[1] if len(sys.argv) > 1 else 'README.md'
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'README.html'

    converter = ReadmeConverter(input_path=input_file, output_path=output_file)
    html_content = converter.convert(doc_title='README')
    converter.write_output(html_content)


if __name__ == '__main__':
    main()


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>README</title>
<style>
:root {
    --bg: #ffffff;
    --fg: #24292f;
    --link: #0366d6;
    --border: #e1e4e8;
    --code-bg: #f6f8fa;
    --sidebar-bg: #fdfdfd;
    --toc-hover: #f0f4f8;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 16px;
    line-height: 1.6;
    color: var(--fg);
    background: var(--bg);
}

.topbar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: 50px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    padding: 0 20px;
    z-index: 100;
}

.menu-btn {
    display: none;
    background: none;
    border: none;
    cursor: pointer;
    padding: 5px;
    margin-right: 10px;
}

.brand {
    font-weight: 600;
    font-size: 18px;
    color: var(--fg);
    text-decoration: none;
}

.brand-dot {
    color: var(--link);
}

.search-trigger {
    margin-left: auto;
    cursor: pointer;
    padding: 5px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 13px;
    color: #6a737d;
}

.search-trigger kbd {
    background: var(--code-bg);
    padding: 1px 4px;
    border-radius: 3px;
    font-family: monospace;
    font-size: 11px;
}

.topbar-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-left: 20px;
}

.tb-btn {
    background: none;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 4px 8px;
    cursor: pointer;
    font-size: 13px;
    color: var(--fg);
}

.tb-btn.active {
    background: var(--link);
    color: white;
    border-color: var(--link);
}

.layout {
    display: flex;
    margin-top: 50px;
    min-height: calc(100vh - 50px);
}

.sidebar {
    width: 280px;
    position: fixed;
    top: 50px;
    left: 0;
    bottom: 0;
    overflow-y: auto;
    background: var(--sidebar-bg);
    border-right: 1px solid var(--border);
    padding: 20px;
}

.sidebar-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
}

.toc-filter {
    width: 100%;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 13px;
    margin-bottom: 15px;
}

.doc-section {
    max-width: 800px;
    margin: 0 auto;
    padding: 20px 30px;
}

.doc-section h1, .doc-section h2, .doc-section h3 {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    cursor: pointer;
}

.inline-anchor {
    position: relative;
}

.inline-anchor::before {
    content: "#";
    opacity: 0;
    margin-right: 4px;
    color: var(--link);
    transition: opacity 0.2s;
}

.inline-anchor:hover::before {
    opacity: 1;
}

.readme-table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}

.readme-table th, .readme-table td {
    border: 1px solid var(--border);
    padding: 8px 12px;
    text-align: left;
}

.readme-table th {
    background: var(--code-bg);
    font-weight: 600;
}

pre {
    background: var(--code-bg);
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    margin: 1em 0;
}

code {
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 85%;
}

pre code {
    background: none;
    padding: 0;
}

.kw {
    color: #d73a49;
    font-weight: 500;
}

.toc-group {
    margin-bottom: 8px;
}

.toc-group a {
    display: block;
    padding: 4px 8px;
    color: var(--fg);
    text-decoration: none;
    border-radius: 4px;
    font-size: 14px;
}

.toc-group a:hover {
    background: var(--toc-hover);
}

.toc-child {
    margin-left: 16px;
}

@media (max-width: 768px) {
    .sidebar {
        transform: translateX(-100%);
        transition: transform 0.3s;
    }
    .sidebar.open {
        transform: translateX(0);
    }
    .menu-btn {
        display: block;
    }
    .layout {
        flex-direction: column;
    }
}
</style>
</head>
<body>

<div class="topbar">
    <button class="menu-btn" onclick="toggleSidebar()">&#9776;</button>
    <a href="#" class="brand">README<span class="brand-dot">.</span>html</a>
    <div class="topbar-actions">
        <div class="search-trigger" onclick="toggleSearch()" title="Search (Ctrl+K)">
            Search <kbd>Ctrl+K</kbd>
        </div>
    </div>
</div>

<div class="layout">
    <aside class="sidebar" id="sidebar">
        <div class="sidebar-top">
            <strong>Table of Contents</strong>
        </div>
        <input type="text" class="toc-filter" placeholder="Filter sections..." oninput="filterToc(this.value)">
        <!-- TOC_PLACEHOLDER -->
    </aside>

    <main class="doc-content">
        <!-- BODY_PLACEHOLDER -->
    </main>
</div>

<script>
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

function toggleSearch() {
    const query = prompt('Search README:');
    if (query) {
        window.location.hash = '';
        const sections = document.querySelectorAll('.doc-section');
        let found = false;
        sections.forEach(sec => {
            if (sec.textContent.toLowerCase().includes(query.toLowerCase())) {
                sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
                found = true;
            }
        });
        if (!found) alert('No matches found');
    }
}

function filterToc(query) {
    const groups = document.querySelectorAll('.toc-group');
    groups.forEach(group => {
        const link = group.querySelector('a');
        if (link && link.textContent.toLowerCase().includes(query.toLowerCase())) {
            group.style.display = 'block';
        } else {
            group.style.display = 'none';
        }
    });
}

document.addEventListener('keydown', function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        toggleSearch();
    }
});
</script>

</body>
</html>
"""
