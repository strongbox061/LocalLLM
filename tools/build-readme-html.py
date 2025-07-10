#!/usr/bin/env python3
"""Generate a self-contained, interactive HTML version of README.md.

Usage:
    python3 tools/build-readme-html.py [input.md] [output.html]

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
    return "\n".join(out_lines)


class ReadmeConverter:
    def __init__(self, md_text: str) -> None:
        self.md_text = md_text
        self.lines = md_text.splitlines()
        self.headings: list[dict[str, Any]] = []
        self.slug_by_title: dict[str, str] = {}
        self.inline_anchors: dict[str, str] = {}
        self._scan_headings()

    def _scan_headings(self) -> None:
        in_code = False
        used_slugs: dict[str, int] = {}
        current_h2 = ""
        for idx, line in enumerate(self.lines):
            if re.match(r"^\s*```", line):
                in_code = not in_code
                continue
            if in_code:
                continue
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                level = len(m.group(1))
                raw_title = m.group(2).strip()
                clean_title = strip_md_inline(raw_title)
                base_slug = slugify(clean_title)
                count = used_slugs.get(base_slug, 0)
                used_slugs[base_slug] = count + 1
                slug = base_slug if count == 0 else f"{base_slug}-{count + 1}"
                if level == 2:
                    current_h2 = slug
                self.headings.append(
                    {
                        "line": idx,
                        "level": level,
                        "title": clean_title,
                        "raw": raw_title,
                        "slug": slug,
                        "parent": current_h2 if level == 3 else "",
                    }
                )
                self.slug_by_title[clean_title.lower()] = slug
            else:
                # Track bold paragraph lead-ins like "**Signing in to Context7.**" for cross-refs
                mb = re.match(r"^\*\*([^*]{4,60}?)\.\*\*\s", line)
                if mb:
                    lead = mb.group(1).strip()
                    self.inline_anchors[lead.lower()] = slugify(lead)

    def resolve_ref(self, ref_text: str) -> str | None:
        key = ref_text.strip().lower()
        if key in self.slug_by_title:
            return self.slug_by_title[key]
        if key in self.inline_anchors:
            return self.inline_anchors[key]
        # Prefix match on heading titles (e.g., "Protected chats" -> "Protected chats in Open WebUI")
        for title_lower, slug in self.slug_by_title.items():
            if title_lower.startswith(key):
                return slug
        # Substring match fallback
        for title_lower, slug in self.slug_by_title.items():
            if key in title_lower:
                return slug
        return None

    def format_inline(self, text: str) -> str:
        # 1. Extract inline code spans first so nothing inside them gets touched
        code_tokens: list[str] = []

        def _stash_code(m: re.Match[str]) -> str:
            code_val = m.group(1)
            idx = len(code_tokens)
            # Mark likely shell commands/paths subtly for readability
            code_tokens.append(f"<code>{html.escape(code_val)}</code>")
            return f"\x00CODE{idx}\x00"

        s = re.sub(r"`([^`]+)`", _stash_code, text)

        # 2. Extract markdown links [label](url)
        link_tokens: list[str] = []

        def _stash_link(m: re.Match[str]) -> str:
            label = m.group(1)
            url = m.group(2)
            idx = len(link_tokens)
            # Only make real http(s) links clickable; placeholder links like https://... stay text
            if url.startswith(("http://", "https://")) and "..." not in url:
                link_tokens.append(
                    f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'
                )
            else:
                link_tokens.append(html.escape(m.group(0)))
            return f"\x00LINK{idx}\x00"

        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _stash_link, s)

        # 3. Escape remaining HTML
        s = html.escape(s, quote=False)

        # 4. Auto-link bare http(s) URLs (not containing <...> placeholders)
        def _autolink(m: re.Match[str]) -> str:
            url = m.group(0)
            trailing = ""
            while url and url[-1] in ".,;:!?)":
                if url[-1] == ")" and url.count("(") >= url.count(")"):
                    break
                trailing = url[-1] + trailing
                url = url[:-1]
            if "&lt;" in url or "&gt;" in url or "..." in url:
                return m.group(0)
            return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{url}</a>{trailing}'

        s = re.sub(r"https?://[^\s<>\"'\x00]+", _autolink, s)

        # 5. Internal cross-references: see "Section Name" / see also "Section Name"
        def _xref(m: re.Match[str]) -> str:
            prefix = m.group(1)
            ref = m.group(2)
            slug = self.resolve_ref(ref)
            if slug:
                return f'{prefix}<a class="xref" href="#{slug}" data-target="{slug}">&ldquo;{ref}&rdquo;</a>'
            return m.group(0)

        s = re.sub(r'\b(see\s+(?:also\s+)?)"([^"]+)"', _xref, s, flags=re.IGNORECASE)

        # 6. Bold (**...**) with special badge styling for "By hand"
        def _bold(m: re.Match[str]) -> str:
            inner = m.group(1)
            if inner.lower().startswith("by hand"):
                return f'<span class="badge-manual">{inner}</span>'
            # Check if this bold lead-in has an inline anchor
            lead_key = inner.rstrip(".").strip().lower()
            if lead_key in self.inline_anchors:
                anc = self.inline_anchors[lead_key]
                return f'<strong id="{anc}" class="inline-anchor">{inner}</strong>'
            return f"<strong>{inner}</strong>"

        s = re.sub(r"\*\*([^*]+)\*\*", _bold, s)

        # 7. Italic (*...*) — avoid matching globs like *.csproj
        s = re.sub(r"(?<![\w*])\*([^\s*](?:[^*]*?[^\s*])?)\*(?![\w*])", r"<em>\1</em>", s)

        # 8. Restore links and inline code
        for idx, tok in enumerate(link_tokens):
            s = s.replace(f"\x00LINK{idx}\x00", tok)
        for idx, tok in enumerate(code_tokens):
            s = s.replace(f"\x00CODE{idx}\x00", tok)

        return s

    def render_blocks(self, lines: list[str], base_indent: int = 0) -> str:
        """Render a slice of markdown lines (no headings) into HTML."""
        out: list[str] = []
        n = len(lines)
        i = 0

        while i < n:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            # Fenced code block
            m_fence = re.match(r"^(\s*)```(\S*)\s*$", line)
            if m_fence:
                fence_indent = len(m_fence.group(1))
                lang = m_fence.group(2)
                code_lines: list[str] = []
                i += 1
                while i < n and not re.match(r"^\s*```\s*$", lines[i]):
                    cl = lines[i]
                    if fence_indent > 0 and cl.startswith(" " * fence_indent):
                        cl = cl[fence_indent:]
                    code_lines.append(cl)
                    i += 1
                if i < n:
                    i += 1  # consume closing ```
                raw_code = "\n".join(code_lines)
                highlighted = highlight_code(raw_code, lang)
                lang_label = html.escape(lang) if lang else "text"
                out.append(
                    f'<div class="code-block" data-lang="{lang_label}">'
                    f'<div class="code-header">'
                    f'<span class="code-lang">{lang_label}</span>'
                    f'<button type="button" class="copy-btn" aria-label="Copy code">Copy</button>'
                    f"</div>"
                    f"<pre><code>{highlighted}</code></pre>"
                    f"</div>"
                )
                continue

            # GFM Table
            if "|" in stripped and i + 1 < n and is_table_sep(lines[i + 1]):
                headers = split_table_row(line)
                aligns = table_alignments(lines[i + 1])
                i += 2
                rows: list[list[str]] = []
                while i < n and lines[i].strip() and "|" in lines[i]:
                    rows.append(split_table_row(lines[i]))
                    i += 1
                thead_cells = "".join(
                    f'<th style="text-align:{aligns[c] if c < len(aligns) else "left"}">'
                    f"{self.format_inline(h)}</th>"
                    for c, h in enumerate(headers)
                )
                tbody_rows = []
                for row in rows:
                    tds = "".join(
                        f'<td style="text-align:{aligns[c] if c < len(aligns) else "left"}">'
                        f"{self.format_inline(cell)}</td>"
                        for c, cell in enumerate(row)
                    )
                    tbody_rows.append(f"<tr>{tds}</tr>")
                out.append(
                    '<div class="table-wrap">'
                    f"<table><thead><tr>{thead_cells}</tr></thead>"
                    f'<tbody>{"".join(tbody_rows)}</tbody></table>'
                    "</div>"
                )
                continue

            # Unordered or Ordered List
            m_ul = re.match(r"^(\s*)([-*])\s+(.*)$", line)
            m_ol = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
            if m_ul or m_ol:
                is_ol = bool(m_ol)
                list_indent = len((m_ol or m_ul).group(1))
                start_num = int(m_ol.group(2)) if m_ol else 1
                tag = "ol" if is_ol else "ul"
                start_attr = f' start="{start_num}"' if (is_ol and start_num != 1) else ""
                items_html: list[str] = []

                while i < n:
                    cur = lines[i]
                    if not cur.strip():
                        # Look ahead to see if list continues at same indent
                        j = i + 1
                        while j < n and not lines[j].strip():
                            j += 1
                        if j < n:
                            nxt = lines[j]
                            if is_ol and re.match(rf"^ {{{list_indent}}}\d+\.\s+", nxt):
                                i = j
                                continue
                            if not is_ol and re.match(rf"^ {{{list_indent}}}[-*]\s+", nxt):
                                i = j
                                continue
                        break

                    if is_ol:
                        m_item = re.match(rf"^ {{{list_indent}}}(\d+)\.\s+(.*)$", cur)
                    else:
                        m_item = re.match(rf"^ {{{list_indent}}}[-*]\s+(.*)$", cur)
                    if not m_item:
                        break

                    item_text_lines = [m_item.group(2) if is_ol else m_item.group(1) if False else m_item.group(0)]
                    # Extract first line content properly
                    first_content = m_item.group(2) if is_ol else re.sub(r"^\s*[-*]\s+", "", cur)
                    item_sublines: list[str] = [first_content]
                    i += 1

                    # Collect continuation lines belonging to this list item
                    in_item_code = False
                    while i < n:
                        nxt = lines[i]
                        if not nxt.strip():
                            if in_item_code:
                                item_sublines.append("")
                                i += 1
                                continue
                            # Check if next non-blank line is indented further (continuation block)
                            j = i + 1
                            while j < n and not lines[j].strip():
                                j += 1
                            if j < n and (len(lines[j]) - len(lines[j].lstrip(" "))) > list_indent:
                                item_sublines.append("")
                                i += 1
                                continue
                            break

                        nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                        if in_item_code:
                            if re.match(r"^\s*```\s*$", nxt):
                                in_item_code = False
                            item_sublines.append(nxt)
                            i += 1
                            continue

                        # Another sibling item at same indent ends this item
                        if nxt_indent <= list_indent:
                            break

                        if re.match(r"^\s*```", nxt):
                            in_item_code = True
                            item_sublines.append(nxt)
                            i += 1
                            continue

                        item_sublines.append(nxt)
                        i += 1

                    # Normalize indentation of sublines (excluding first line)
                    rendered_item = self._render_list_item(item_sublines)
                    items_html.append(f"<li>{rendered_item}</li>")

                out.append(f"<{tag}{start_attr}>{''.join(items_html)}</{tag}>")
                continue

            # Paragraph (collect contiguous plain lines)
            para_lines: list[str] = [stripped]
            i += 1
            while i < n:
                nxt = lines[i]
                nxt_s = nxt.strip()
                if not nxt_s:
                    break
                if re.match(r"^\s*```", nxt):
                    break
                if re.match(r"^\s*[-*]\s+", nxt) or re.match(r"^\s*\d+\.\s+", nxt):
                    break
                if "|" in nxt_s and i + 1 < n and is_table_sep(lines[i + 1]):
                    break
                para_lines.append(nxt_s)
                i += 1
            out.append(f"<p>{self.format_inline(' '.join(para_lines))}</p>")

        return "\n".join(out)

    def _render_list_item(self, sublines: list[str]) -> str:
        """Render the body of a list item, which may contain prose, nested lists, and code blocks."""
        # Separate leading inline text lines from nested blocks (code fences, sub-lists, blank-separated paragraphs)
        blocks_html: list[str] = []
        lead_prose: list[str] = []
        i = 0
        n = len(sublines)

        # Find common continuation indent for lines after line 0
        cont_indents = [
            len(l) - len(l.lstrip(" "))
            for l in sublines[1:]
            if l.strip()
        ]
        min_cont = min(cont_indents) if cont_indents else 0

        def dedent(line: str) -> str:
            if min_cont > 0 and line.startswith(" " * min_cont):
                return line[min_cont:]
            return line.lstrip(" ") if not line.startswith(" " * min_cont) else line

        # Consume initial paragraph text until a code block, nested list, or blank line
        while i < n:
            line = sublines[i]
            if i == 0:
                lead_prose.append(line.strip())
                i += 1
                continue
            if not line.strip():
                i += 1
                break
            d = dedent(line)
            if re.match(r"^\s*```", d) or re.match(r"^\s*[-*]\s+", d) or re.match(r"^\s*\d+\.\s+", d):
                break
            lead_prose.append(d.strip())
            i += 1

        if lead_prose:
            blocks_html.append(self.format_inline(" ".join(lead_prose)))

        if i < n:
            rest = [dedent(l) if l.strip() else "" for l in sublines[i:]]
            blocks_html.append(self.render_blocks(rest))

        return "\n".join(blocks_html)

    def build_document(self) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """Build structured HTML sections, TOC tree, and search index."""
        # Split lines by heading positions
        heading_indices = [h["line"] for h in self.headings]
        slices: list[tuple[dict[str, Any] | None, list[str]]] = []

        if heading_indices and heading_indices[0] > 0:
            slices.append((None, self.lines[: heading_indices[0]]))

        for pos, h in enumerate(self.headings):
            start = h["line"] + 1
            end = heading_indices[pos + 1] if pos + 1 < len(heading_indices) else len(self.lines)
            slices.append((h, self.lines[start:end]))

        # Group into intro (level 1), level-2 sections, and nested level-3 subsections
        intro_html = ""
        doc_title = "LocalLLM"
        sections_html: list[str] = []
        toc_tree: list[dict[str, Any]] = []
        search_index: list[dict[str, Any]] = []

        def add_search_entry(slug: str, title: str, parent_title: str, level: int, raw_lines: list[str]) -> None:
            # Extract clean plain text and commands for fast client-side search
            plain_parts: list[str] = []
            commands: list[str] = []
            in_code = False
            for ln in raw_lines:
                if re.match(r"^\s*```", ln):
                    in_code = not in_code
                    continue
                if in_code:
                    commands.append(ln.strip())
                else:
                    plain_parts.append(strip_md_inline(ln))
            search_index.append(
                {
                    "id": slug,
                    "title": title,
                    "parent": parent_title,
                    "level": level,
                    "text": re.sub(r"\s+", " ", " ".join(plain_parts)).strip(),
                    "code": re.sub(r"\s+", " ", " ".join(commands)).strip(),
                }
            )

        open_h2 = False
        current_h2_toc: dict[str, Any] | None = None

        for h, body_lines in slices:
            if h is None:
                intro_html += self.render_blocks(body_lines)
                continue

            level = h["level"]
            slug = h["slug"]
            title = h["title"]

            if level == 1:
                doc_title = title
                intro_html += self.render_blocks(body_lines)
                add_search_entry("overview", f"{title} (Overview)", "", 1, body_lines)
            elif level == 2:
                if open_h2:
                    sections_html.append("</div></section>")
                body_html = self.render_blocks(body_lines)
                sections_html.append(
                    f'<section class="doc-section level-2" id="{slug}" data-title="{html.escape(title, quote=True)}">'
                    f'<div class="section-header">'
                    f'<button type="button" class="collapse-toggle" aria-expanded="true" aria-label="Toggle section">'
                    f'<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M4.427 6.427l3.396 3.396a.25.25 0 00.354 0l3.396-3.396A.25.25 0 0011.396 6H4.604a.25.25 0 00-.177.427z"/></svg>'
                    f"</button>"
                    f'<h2><a class="heading-text" href="#{slug}">{html.escape(title)}</a></h2>'
                    f'<button type="button" class="anchor-copy" data-hash="#{slug}" title="Copy link to section">#</button>'
                    f"</div>"
                    f'<div class="section-body">'
                    f"{body_html}"
                )
                open_h2 = True
                current_h2_toc = {"id": slug, "title": title, "children": []}
                toc_tree.append(current_h2_toc)
                add_search_entry(slug, title, "", 2, body_lines)
            elif level == 3:
                body_html = self.render_blocks(body_lines)
                sections_html.append(
                    f'<section class="doc-section level-3" id="{slug}" data-title="{html.escape(title, quote=True)}">'
                    f'<div class="section-header">'
                    f'<button type="button" class="collapse-toggle" aria-expanded="true" aria-label="Toggle subsection">'
                    f'<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor"><path d="M4.427 6.427l3.396 3.396a.25.25 0 00.354 0l3.396-3.396A.25.25 0 0011.396 6H4.604a.25.25 0 00-.177.427z"/></svg>'
                    f"</button>"
                    f'<h3><a class="heading-text" href="#{slug}">{html.escape(title)}</a></h3>'
                    f'<button type="button" class="anchor-copy" data-hash="#{slug}" title="Copy link to section">#</button>'
                    f"</div>"
                    f'<div class="section-body">'
                    f"{body_html}"
                    f"</div></section>"
                )
                parent_title = current_h2_toc["title"] if current_h2_toc else ""
                if current_h2_toc is not None:
                    current_h2_toc["children"].append({"id": slug, "title": title})
                add_search_entry(slug, title, parent_title, 3, body_lines)

        if open_h2:
            sections_html.append("</div></section>")

        full_body = (
            f'<section class="doc-section level-1" id="overview" data-title="{html.escape(doc_title, quote=True)}">'
            f'<div class="hero">'
            f'<div class="hero-top">'
            f"<h1>{html.escape(doc_title)}</h1>"
            f'<span class="hero-badge">Interactive Guide</span>'
            f"</div>"
            f'<div class="quick-pills" aria-label="Quick jumps">'
            f'<a href="#starting-and-stopping" class="pill">Start &amp; Stop</a>'
            f'<a href="#model" class="pill">Model</a>'
            f'<a href="#memory" class="pill">Memory</a>'
            f'<a href="#voice-in-open-webui" class="pill">Voice</a>'
            f'<a href="#web-search-in-pi-and-open-webui-the-search-gateway" class="pill">Search Gateway</a>'
            f'<a href="#two-pc-setup" class="pill">Two-PC Split</a>'
            f'<a href="#backing-up-and-restoring-settings" class="pill">Backups</a>'
            f'<a href="#reinstalling-from-scratch" class="pill">Reinstall</a>'
            f'<a href="#tuning-llama-server" class="pill">Tuning</a>'
            f'<a href="#layout" class="pill">Layout</a>'
            f"</div>"
            f'<div class="section-body">{intro_html}</div>'
            f"</div></section>\n"
            + "\n".join(sections_html)
        )
        return full_body, toc_tree, search_index


def render_toc_html(toc_tree: list[dict[str, Any]]) -> str:
    parts = [
        '<div class="toc-group">',
        '<a class="toc-link toc-h2" href="#overview" data-target="overview">Overview</a>',
        "</div>",
    ]
    for item in toc_tree:
        has_children = bool(item["children"])
        parts.append('<div class="toc-group" data-group-id="' + item["id"] + '">')
        parts.append('<div class="toc-h2-row">')
        if has_children:
            parts.append(
                '<button type="button" class="toc-caret" aria-expanded="true" aria-label="Toggle group">'
                '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><path d="M4.427 6.427l3.396 3.396a.25.25 0 00.354 0l3.396-3.396A.25.25 0 0011.396 6H4.604a.25.25 0 00-.177.427z"/></svg>'
                "</button>"
            )
        else:
            parts.append('<span class="toc-caret-spacer"></span>')
        parts.append(
            f'<a class="toc-link toc-h2" href="#{item["id"]}" data-target="{item["id"]}">'
            f'{html.escape(item["title"])}</a>'
        )
        parts.append("</div>")
        if has_children:
            parts.append('<div class="toc-children">')
            for child in item["children"]:
                parts.append(
                    f'<a class="toc-link toc-h3" href="#{child["id"]}" data-target="{child["id"]}">'
                    f'{html.escape(child["title"])}</a>'
                )
            parts.append("</div>")
        parts.append("</div>")
    return "\n".join(parts)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LocalLLM — Interactive Documentation</title>
<style>
:root {
  --bg: #0d1117;
  --bg-elev: #161b22;
  --bg-subtle: #1c222b;
  --bg-hover: #242c37;
  --border: #30363d;
  --border-subtle: #21262d;
  --text: #e6edf3;
  --text-muted: #8b949e;
  --accent: #58a6ff;
  --accent-soft: rgba(88, 166, 255, 0.14);
  --accent-strong: #79c0ff;
  --green: #3fb950;
  --amber: #d29922;
  --amber-bg: rgba(210, 153, 34, 0.16);
  --code-bg: #11161d;
  --inline-code-bg: rgba(110, 118, 129, 0.22);
  --mark-bg: rgba(242, 204, 96, 0.32);
  --mark-active: rgba(255, 166, 87, 0.65);
  --shadow: 0 12px 36px rgba(0, 0, 0, 0.45);
  --tok-com: #7d8590;
  --tok-str: #a5d6ff;
  --tok-cmd: #7ee787;
  --tok-flag: #ffa657;
  --tok-key: #79c0ff;
  --sidebar-w: 310px;
  --topbar-h: 54px;
}

[data-theme="light"] {
  --bg: #ffffff;
  --bg-elev: #f6f8fa;
  --bg-subtle: #f0f3f6;
  --bg-hover: #e7ecf0;
  --border: #d0d7de;
  --border-subtle: #e5e9ee;
  --text: #1f2328;
  --text-muted: #59636e;
  --accent: #0969da;
  --accent-soft: rgba(9, 105, 218, 0.1);
  --accent-strong: #0550ae;
  --green: #1a7f37;
  --amber: #9a6700;
  --amber-bg: rgba(212, 167, 44, 0.18);
  --code-bg: #f6f8fa;
  --inline-code-bg: rgba(175, 184, 193, 0.24);
  --mark-bg: rgba(255, 212, 59, 0.45);
  --mark-active: rgba(255, 146, 43, 0.65);
  --shadow: 0 12px 32px rgba(31, 35, 40, 0.16);
  --tok-com: #6e7781;
  --tok-str: #0a3069;
  --tok-cmd: #116329;
  --tok-flag: #953800;
  --tok-key: #0550ae;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: calc(var(--topbar-h) + 18px); }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Fira Sans", sans-serif;
  font-size: 15px;
  line-height: 1.65;
  background: var(--bg);
  color: var(--text);
}

/* Top bar */
.topbar {
  position: fixed;
  top: 0; left: 0; right: 0;
  height: var(--topbar-h);
  background: var(--bg-elev);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  z-index: 40;
}
.menu-btn {
  display: none;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 6px;
  padding: 5px 8px;
  cursor: pointer;
}
.brand {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.01em;
  color: var(--text);
  text-decoration: none;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 8px;
}
.brand-dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent);
}
.search-trigger {
  flex: 1;
  max-width: 480px;
  margin-left: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 13.5px;
  transition: border-color .15s, background .15s;
}
.search-trigger:hover { border-color: var(--accent); color: var(--text); }
.search-trigger kbd {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 6px;
  color: var(--text-muted);
}
.topbar-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
}
.tb-btn {
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 6px;
  padding: 5px 10px;
  font-size: 12.5px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
  transition: border-color .15s, background .15s;
}
.tb-btn:hover { background: var(--bg-hover); border-color: var(--accent); }
.tb-btn.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent-strong);
}

/* Layout */
.layout {
  display: flex;
  padding-top: var(--topbar-h);
  min-height: 100vh;
}
.sidebar {
  position: fixed;
  top: var(--topbar-h);
  bottom: 0;
  left: 0;
  width: var(--sidebar-w);
  background: var(--bg-elev);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  z-index: 30;
  transition: transform .2s ease;
}
.sidebar-top {
  padding: 12px 12px 8px;
  border-bottom: 1px solid var(--border-subtle);
}
.toc-filter {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.toc-filter:focus { border-color: var(--accent); }
.toc-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 7px;
  font-size: 11.5px;
  color: var(--text-muted);
}
.toc-controls button {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 2px 4px;
  font-size: 11.5px;
  border-radius: 4px;
}
.toc-controls button:hover { color: var(--accent); background: var(--bg-hover); }

.toc-nav {
  flex: 1;
  overflow-y: auto;
  padding: 8px 8px 24px;
}
.toc-group { margin-bottom: 2px; }
.toc-h2-row { display: flex; align-items: center; }
.toc-caret {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  width: 20px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  border-radius: 4px;
  transition: transform .15s;
}
.toc-caret:hover { background: var(--bg-hover); color: var(--text); }
.toc-caret[aria-expanded="false"] svg { transform: rotate(-90deg); }
.toc-caret-spacer { width: 20px; flex-shrink: 0; }
.toc-link {
  display: block;
  flex: 1;
  text-decoration: none;
  color: var(--text-muted);
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.35;
  transition: background .12s, color .12s;
  overflow: hidden;
  text-overflow: ellipsis;
}
.toc-h2 { font-weight: 600; color: var(--text); }
.toc-h3 { padding-left: 28px; font-size: 12.5px; }
.toc-link:hover { background: var(--bg-hover); color: var(--text); }
.toc-link.active {
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-weight: 600;
}
.toc-children.collapsed { display: none; }
.toc-hidden { display: none !important; }

/* Main content */
.main {
  margin-left: var(--sidebar-w);
  flex: 1;
  min-width: 0;
  padding: 28px 40px 96px;
}
.content {
  max-width: 900px;
  margin: 0 auto;
}

/* Hero */
.hero {
  padding-bottom: 18px;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
.hero-top {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.hero h1 {
  margin: 0;
  font-size: 32px;
  letter-spacing: -0.02em;
}
.hero-badge {
  font-size: 11.5px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent-strong);
  border: 1px solid rgba(88, 166, 255, 0.3);
}
.quick-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 14px 0 18px;
}
.pill {
  font-size: 12.5px;
  padding: 4px 11px;
  border-radius: 999px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  color: var(--text);
  text-decoration: none;
  transition: border-color .15s, background .15s, color .15s;
}
.pill:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-strong);
}

/* Sections */
.doc-section.level-2 {
  margin-top: 30px;
  padding-top: 6px;
}
.doc-section.level-3 {
  margin-top: 22px;
  padding-left: 14px;
  border-left: 2px solid var(--border-subtle);
}
.section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 6px;
  margin-bottom: 12px;
}
.level-2 > .section-header { border-bottom-color: var(--border); }
.section-header h2, .section-header h3 {
  margin: 0;
  flex: 1;
  min-width: 0;
}
.section-header h2 { font-size: 22px; letter-spacing: -0.015em; }
.section-header h3 { font-size: 17px; letter-spacing: -0.01em; }
.heading-text { color: inherit; text-decoration: none; }
.heading-text:hover { color: var(--accent); }

.collapse-toggle {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  width: 24px; height: 24px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 5px;
  transition: transform .15s, background .15s;
  flex-shrink: 0;
}
.collapse-toggle:hover { background: var(--bg-hover); color: var(--text); }
.collapse-toggle[aria-expanded="false"] svg { transform: rotate(-90deg); }

.anchor-copy {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  opacity: 0;
  padding: 2px 7px;
  border-radius: 5px;
  font-size: 14px;
  transition: opacity .15s, background .15s;
}
.section-header:hover .anchor-copy, .anchor-copy:focus { opacity: 1; }
.anchor-copy:hover { background: var(--bg-hover); color: var(--accent); }

.doc-section.collapsed > .section-body { display: none; }

/* Flash highlight on jump */
@keyframes flashHighlight {
  0% { background: var(--accent-soft); }
  100% { background: transparent; }
}
.jump-flash {
  animation: flashHighlight 1.6s ease-out;
  border-radius: 8px;
}

/* Typography & elements */
p { margin: 0 0 12px; }
ul, ol { margin: 0 0 14px; padding-left: 24px; }
li { margin-bottom: 6px; }
li > p:last-child, li > ul:last-child, li > ol:last-child { margin-bottom: 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a.xref {
  color: var(--accent-strong);
  background: var(--accent-soft);
  padding: 0 5px;
  border-radius: 4px;
  font-weight: 500;
}
a.xref:hover { text-decoration: none; filter: brightness(1.15); }

code {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.88em;
  background: var(--inline-code-bg);
  padding: 1.5px 5px;
  border-radius: 5px;
}

.badge-manual {
  display: inline-block;
  font-size: 0.85em;
  font-weight: 700;
  color: var(--amber);
  background: var(--amber-bg);
  border: 1px solid rgba(210, 153, 34, 0.35);
  padding: 0 6px;
  border-radius: 5px;
}

/* Code blocks */
.code-block {
  margin: 12px 0 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--code-bg);
  overflow: hidden;
}
.code-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 12px;
  background: var(--bg-elev);
  border-bottom: 1px solid var(--border-subtle);
  font-size: 11.5px;
  color: var(--text-muted);
}
.code-lang {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.copy-btn {
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 5px;
  padding: 2px 9px;
  font-size: 11.5px;
  cursor: pointer;
}
.copy-btn:hover { border-color: var(--accent); color: var(--accent); }
.copy-btn.copied { border-color: var(--green); color: var(--green); }
.code-block pre {
  margin: 0;
  padding: 12px 14px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.5;
}
.code-block pre code {
  background: none;
  padding: 0;
  font-size: inherit;
}
.tok-com { color: var(--tok-com); font-style: italic; }
.tok-str { color: var(--tok-str); }
.tok-cmd { color: var(--tok-cmd); }
.tok-flag { color: var(--tok-flag); }
.tok-key { color: var(--tok-key); }

/* Tables */
.table-wrap {
  overflow-x: auto;
  margin: 12px 0 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}
th, td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-subtle);
  vertical-align: top;
}
thead th {
  background: var(--bg-elev);
  font-weight: 600;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: var(--bg-subtle); }

/* Cheat-sheet mode: Commands & Tables only */
body.cheatsheet-mode .doc-section p,
body.cheatsheet-mode .doc-section ul:not(:has(.code-block, .table-wrap)),
body.cheatsheet-mode .doc-section ol:not(:has(.code-block, .table-wrap)),
body.cheatsheet-mode .doc-section li:not(:has(.code-block, .table-wrap)) {
  display: none;
}
body.cheatsheet-mode .doc-section.no-cheatsheet {
  display: none;
}

/* Search Modal (Ctrl+K / /) */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.58);
  backdrop-filter: blur(3px);
  display: none;
  align-items: flex-start;
  justify-content: center;
  padding-top: 9vh;
  z-index: 100;
}
.modal-backdrop.open { display: flex; }
.search-modal {
  width: min(680px, 92vw);
  max-height: 76vh;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.search-input-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.search-input {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text);
  font-size: 15.5px;
  outline: none;
}
.search-meta {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
}
.search-results {
  overflow-y: auto;
  padding: 8px;
  flex: 1;
}
.sr-item {
  display: block;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 9px 12px;
  color: var(--text);
  cursor: pointer;
  margin-bottom: 4px;
}
.sr-item:hover, .sr-item.selected {
  background: var(--bg-hover);
  border-color: var(--accent);
}
.sr-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
}
.sr-title { font-weight: 600; font-size: 14px; color: var(--accent-strong); }
.sr-parent { font-size: 11.5px; color: var(--text-muted); }
.sr-snippet {
  font-size: 12.5px;
  color: var(--text-muted);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sr-snippet mark, mark.inpage-hit {
  background: var(--mark-bg);
  color: var(--text);
  border-radius: 3px;
  padding: 0 2px;
}
mark.inpage-hit.current { background: var(--mark-active); }
.sr-empty {
  padding: 28px 16px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13.5px;
}
.search-footer {
  padding: 8px 14px;
  border-top: 1px solid var(--border);
  font-size: 11.5px;
  color: var(--text-muted);
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.search-footer kbd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0 5px;
}

/* Floating match bar when highlighting in page */
.find-bar {
  position: fixed;
  bottom: 18px;
  right: 22px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 9px;
  box-shadow: var(--shadow);
  padding: 7px 10px;
  display: none;
  align-items: center;
  gap: 8px;
  z-index: 50;
  font-size: 12.5px;
}
.find-bar.visible { display: flex; }
.find-bar button {
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 5px;
  padding: 2px 7px;
  cursor: pointer;
  font-size: 12px;
}
.find-bar button:hover { border-color: var(--accent); }

.top-btn {
  position: fixed;
  bottom: 20px;
  right: 22px;
  width: 38px; height: 38px;
  border-radius: 50%;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  color: var(--text);
  cursor: pointer;
  display: none;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow);
  z-index: 35;
}
.top-btn.visible { display: flex; }
.top-btn:hover { border-color: var(--accent); color: var(--accent); }

.footer-note {
  margin-top: 48px;
  padding-top: 16px;
  border-top: 1px solid var(--border-subtle);
  font-size: 12px;
  color: var(--text-muted);
}

/* Responsive */
.sidebar-scrim {
  display: none;
  position: fixed;
  inset: var(--topbar-h) 0 0 0;
  background: rgba(0,0,0,0.45);
  z-index: 25;
}
@media (max-width: 960px) {
  .menu-btn { display: inline-flex; }
  .sidebar { transform: translateX(-100%); }
  .sidebar.open { transform: translateX(0); }
  .sidebar-scrim.open { display: block; }
  .main { margin-left: 0; padding: 20px 18px 80px; }
  .tb-label { display: none; }
}
</style>
</head>
<body>

<header class="topbar">
  <button type="button" class="menu-btn" id="menuBtn" aria-label="Toggle navigation">&#9776;</button>
  <a href="#overview" class="brand"><span class="brand-dot"></span>LocalLLM</a>
  <button type="button" class="search-trigger" id="openSearchBtn">
    <span>Search sections, commands, paths&hellip;</span>
    <kbd>Ctrl K</kbd>
  </button>
  <div class="topbar-actions">
    <button type="button" class="tb-btn" id="cheatBtn" title="Show only commands and tables">
      <span>&#9889;</span><span class="tb-label">Commands &amp; Tables</span>
    </button>
    <button type="button" class="tb-btn" id="foldAllBtn" title="Collapse or expand all sections">
      <span id="foldAllIcon">&#8722;</span><span class="tb-label" id="foldAllLabel">Collapse all</span>
    </button>
    <button type="button" class="tb-btn" id="themeBtn" title="Toggle light/dark theme">
      <span id="themeIcon">&#9788;</span>
    </button>
  </div>
</header>

<div class="sidebar-scrim" id="scrim"></div>

<div class="layout">
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-top">
      <input type="search" class="toc-filter" id="tocFilter" placeholder="Filter contents..." autocomplete="off">
      <div class="toc-controls">
        <span id="tocCount">__SECTION_COUNT__ sections</span>
        <div>
          <button type="button" id="tocExpandAll">Expand</button>
          <button type="button" id="tocCollapseAll">Collapse</button>
        </div>
      </div>
    </div>
    <nav class="toc-nav" id="tocNav" aria-label="Table of contents">
      __TOC_HTML__
    </nav>
  </aside>

  <main class="main">
    <article class="content" id="docContent">
      __BODY_HTML__
      <div class="footer-note">
        Generated from <code>README.md</code> on __GENERATED_AT__ via <code>python3 tools/build-readme-html.py</code>.
      </div>
    </article>
  </main>
</div>

<div class="modal-backdrop" id="searchModal" role="dialog" aria-modal="true" aria-label="Search documentation">
  <div class="search-modal">
    <div class="search-input-wrap">
      <input type="text" class="search-input" id="searchInput" placeholder="Type to search (e.g. tailscale, kokoro, 8090, backup, subagent)..." autocomplete="off">
      <span class="search-meta" id="searchMeta"></span>
    </div>
    <div class="search-results" id="searchResults"></div>
    <div class="search-footer">
      <span><kbd>&uarr;</kbd> <kbd>&darr;</kbd> navigate</span>
      <span><kbd>Enter</kbd> jump &amp; highlight</span>
      <span><kbd>Esc</kbd> close</span>
    </div>
  </div>
</div>

<div class="find-bar" id="findBar">
  <span id="findLabel">0 / 0</span>
  <button type="button" id="findPrev" title="Previous match">&uarr;</button>
  <button type="button" id="findNext" title="Next match">&darr;</button>
  <button type="button" id="findClear" title="Clear highlights">&times;</button>
</div>

<button type="button" class="top-btn" id="topBtn" title="Back to top">&uarr;</button>

<script>
const SEARCH_INDEX = __SEARCH_INDEX_JSON__;

(() => {
  // --- Theme ---
  const root = document.documentElement;
  const themeBtn = document.getElementById("themeBtn");
  const themeIcon = document.getElementById("themeIcon");
  function applyTheme(t) {
    root.setAttribute("data-theme", t);
    themeIcon.textContent = t === "light" ? "\u263E" : "\u2600";
    try { localStorage.setItem("localllm-readme-theme", t); } catch {}
  }
  const savedTheme = (() => { try { return localStorage.getItem("localllm-readme-theme"); } catch { return null; } })();
  const initTheme = savedTheme || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  applyTheme(initTheme);
  themeBtn.addEventListener("click", () => applyTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light"));

  // --- Mobile sidebar ---
  const sidebar = document.getElementById("sidebar");
  const scrim = document.getElementById("scrim");
  const closeMobileNav = () => { sidebar.classList.remove("open"); scrim.classList.remove("open"); };
  document.getElementById("menuBtn").addEventListener("click", () => {
    sidebar.classList.toggle("open");
    scrim.classList.toggle("open");
  });
  scrim.addEventListener("click", closeMobileNav);

  // --- Section collapse / expand ---
  function setSectionCollapsed(sec, collapsed) {
    sec.classList.toggle("collapsed", collapsed);
    const btn = sec.querySelector(":scope > .section-header > .collapse-toggle");
    if (btn) btn.setAttribute("aria-expanded", String(!collapsed));
  }
  function expandAncestors(el) {
    let cur = el;
    while (cur && cur !== document.body) {
      if (cur.classList && cur.classList.contains("doc-section") && cur.classList.contains("collapsed")) {
        setSectionCollapsed(cur, false);
      }
      cur = cur.parentElement;
    }
  }
  document.querySelectorAll(".collapse-toggle").forEach(btn => {
    btn.addEventListener("click", () => {
      const sec = btn.closest(".doc-section");
      setSectionCollapsed(sec, !sec.classList.contains("collapsed"));
    });
  });

  const foldAllBtn = document.getElementById("foldAllBtn");
  const foldAllLabel = document.getElementById("foldAllLabel");
  const foldAllIcon = document.getElementById("foldAllIcon");
  let allCollapsed = false;
  foldAllBtn.addEventListener("click", () => {
    allCollapsed = !allCollapsed;
    document.querySelectorAll(".doc-section.level-2, .doc-section.level-3").forEach(s => setSectionCollapsed(s, allCollapsed));
    foldAllLabel.textContent = allCollapsed ? "Expand all" : "Collapse all";
    foldAllIcon.textContent = allCollapsed ? "+" : "\u2212";
  });

  // --- Cheat-sheet mode (Commands & Tables only) ---
  const cheatBtn = document.getElementById("cheatBtn");
  document.querySelectorAll(".doc-section.level-2, .doc-section.level-3").forEach(sec => {
    if (!sec.querySelector(".code-block, .table-wrap")) {
      sec.classList.add("no-cheatsheet");
    }
  });
  cheatBtn.addEventListener("click", () => {
    const active = document.body.classList.toggle("cheatsheet-mode");
    cheatBtn.classList.toggle("active", active);
  });

  // --- Copy buttons (code blocks & section anchors) ---
  async function copyText(text, btn, doneText) {
    const orig = btn.textContent;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    btn.textContent = doneText;
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 1400);
  }
  document.querySelectorAll(".copy-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const code = btn.closest(".code-block").querySelector("pre code").textContent;
      copyText(code, btn, "Copied!");
    });
  });
  document.querySelectorAll(".anchor-copy").forEach(btn => {
    btn.addEventListener("click", () => {
      const url = location.href.split("#")[0] + btn.dataset.hash;
      copyText(url, btn, "\u2713");
    });
  });

  // --- Jump helper (handles collapsed sections + flash) ---
  function jumpToId(id) {
    const target = document.getElementById(id);
    if (!target) return;
    if (document.body.classList.contains("cheatsheet-mode") && target.classList.contains("no-cheatsheet")) {
      document.body.classList.remove("cheatsheet-mode");
      cheatBtn.classList.remove("active");
    }
    expandAncestors(target);
    history.replaceState(null, "", "#" + id);
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.classList.remove("jump-flash");
    void target.offsetWidth;
    target.classList.add("jump-flash");
    closeMobileNav();
  }

  document.addEventListener("click", e => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    const id = a.getAttribute("href").slice(1);
    if (document.getElementById(id)) {
      e.preventDefault();
      jumpToId(id);
    }
  });

  if (location.hash) {
    const initTarget = document.getElementById(location.hash.slice(1));
    if (initTarget) expandAncestors(initTarget);
  }

  // --- TOC collapsible groups & filter ---
  document.querySelectorAll(".toc-caret").forEach(btn => {
    btn.addEventListener("click", () => {
      const expanded = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!expanded));
      const children = btn.closest(".toc-group").querySelector(".toc-children");
      if (children) children.classList.toggle("collapsed", expanded);
    });
  });
  document.getElementById("tocExpandAll").addEventListener("click", () => {
    document.querySelectorAll(".toc-caret").forEach(b => b.setAttribute("aria-expanded", "true"));
    document.querySelectorAll(".toc-children").forEach(c => c.classList.remove("collapsed"));
  });
  document.getElementById("tocCollapseAll").addEventListener("click", () => {
    document.querySelectorAll(".toc-caret").forEach(b => b.setAttribute("aria-expanded", "false"));
    document.querySelectorAll(".toc-children").forEach(c => c.classList.add("collapsed"));
  });

  const tocFilter = document.getElementById("tocFilter");
  const tocCount = document.getElementById("tocCount");
  const allTocGroups = Array.from(document.querySelectorAll(".toc-group"));
  tocFilter.addEventListener("input", () => {
    const q = tocFilter.value.trim().toLowerCase();
    let visibleLinks = 0;
    allTocGroups.forEach(group => {
      const h2 = group.querySelector(".toc-h2");
      const h3s = Array.from(group.querySelectorAll(".toc-h3"));
      const h2Match = h2.textContent.toLowerCase().includes(q);
      let anyChildMatch = false;
      h3s.forEach(c => {
        const m = !q || h2Match || c.textContent.toLowerCase().includes(q);
        c.classList.toggle("toc-hidden", !m);
        if (m) { anyChildMatch = true; visibleLinks++; }
      });
      const showGroup = !q || h2Match || anyChildMatch;
      group.classList.toggle("toc-hidden", !showGroup);
      if (showGroup) visibleLinks++;
      if (q && anyChildMatch) {
        const ch = group.querySelector(".toc-children");
        const caret = group.querySelector(".toc-caret");
        if (ch) ch.classList.remove("collapsed");
        if (caret) caret.setAttribute("aria-expanded", "true");
      }
    });
    tocCount.textContent = q ? `${visibleLinks} matching` : `${SEARCH_INDEX.length} sections`;
  });

  // --- Scrollspy ---
  const sections = Array.from(document.querySelectorAll(".doc-section[id]"));
  const tocLinksById = new Map();
  document.querySelectorAll(".toc-link[data-target]").forEach(a => tocLinksById.set(a.dataset.target, a));
  let activeId = "";
  function updateScrollspy() {
    const y = window.scrollY + 90;
    let cur = sections[0]?.id || "";
    for (const s of sections) {
      if (s.offsetParent === null) continue;
      if (s.offsetTop <= y) cur = s.id;
      else break;
    }
    if (cur && cur !== activeId) {
      if (activeId && tocLinksById.has(activeId)) tocLinksById.get(activeId).classList.remove("active");
      activeId = cur;
      const link = tocLinksById.get(activeId);
      if (link) {
        link.classList.add("active");
        link.scrollIntoView({ block: "nearest" });
      }
    }
    topBtn.classList.toggle("visible", window.scrollY > 400 && !findBar.classList.contains("visible"));
  }
  let spyTick = false;
  window.addEventListener("scroll", () => {
    if (!spyTick) {
      spyTick = true;
      requestAnimationFrame(() => { updateScrollspy(); spyTick = false; });
    }
  }, { passive: true });

  const topBtn = document.getElementById("topBtn");
  topBtn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

  // --- Full-text Search Modal + In-page Highlighting ---
  const searchModal = document.getElementById("searchModal");
  const searchInput = document.getElementById("searchInput");
  const searchResults = document.getElementById("searchResults");
  const searchMeta = document.getElementById("searchMeta");
  const findBar = document.getElementById("findBar");
  const findLabel = document.getElementById("findLabel");
  let selectedIdx = 0;
  let currentResults = [];
  let inpageHits = [];
  let inpageIdx = -1;

  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function escapeRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function clearInpageHighlights() {
    document.querySelectorAll("mark.inpage-hit").forEach(m => {
      const parent = m.parentNode;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();
    });
    inpageHits = [];
    inpageIdx = -1;
    findBar.classList.remove("visible");
    updateScrollspy();
  }

  function highlightInSection(sectionEl, query) {
    clearInpageHighlights();
    const terms = query.trim().split(/\s+/).filter(t => t.length >= 2);
    if (!terms.length) return;
    const re = new RegExp("(" + terms.map(escapeRegex).join("|") + ")", "gi");

    const walker = document.createTreeWalker(sectionEl, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        const p = node.parentElement;
        if (!p || p.closest("button, script, style")) return NodeFilter.FILTER_REJECT;
        return re.test(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const textNodes = [];
    let n;
    while ((n = walker.nextNode())) textNodes.push(n);

    textNodes.forEach(tn => {
      re.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let last = 0;
      const val = tn.nodeValue;
      let m;
      while ((m = re.exec(val)) !== null) {
        if (m.index > last) frag.appendChild(document.createTextNode(val.slice(last, m.index)));
        const mark = document.createElement("mark");
        mark.className = "inpage-hit";
        mark.textContent = m[0];
        frag.appendChild(mark);
        inpageHits.push(mark);
        last = m.index + m[0].length;
      }
      if (last < val.length) frag.appendChild(document.createTextNode(val.slice(last)));
      tn.parentNode.replaceChild(frag, tn);
    });

    if (inpageHits.length) {
      inpageIdx = 0;
      focusInpageHit(0);
      findBar.classList.add("visible");
      topBtn.classList.remove("visible");
    }
  }

  function focusInpageHit(idx) {
    if (!inpageHits.length) return;
    inpageHits.forEach(h => h.classList.remove("current"));
    inpageIdx = (idx + inpageHits.length) % inpageHits.length;
    const el = inpageHits[inpageIdx];
    el.classList.add("current");
    expandAncestors(el);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    findLabel.textContent = `${inpageIdx + 1} / ${inpageHits.length}`;
  }

  document.getElementById("findPrev").addEventListener("click", () => focusInpageHit(inpageIdx - 1));
  document.getElementById("findNext").addEventListener("click", () => focusInpageHit(inpageIdx + 1));
  document.getElementById("findClear").addEventListener("click", clearInpageHighlights);

  function buildSnippet(entry, terms, rawQuery) {
    const combined = entry.text + (entry.code ? "  " + entry.code : "");
    const lower = combined.toLowerCase();
    let pos = lower.indexOf(rawQuery.toLowerCase());
    if (pos === -1) {
      for (const t of terms) {
        pos = lower.indexOf(t);
        if (pos !== -1) break;
      }
    }
    const start = Math.max(0, (pos === -1 ? 0 : pos) - 55);
    const end = Math.min(combined.length, start + 190);
    let snip = (start > 0 ? "\u2026" : "") + combined.slice(start, end) + (end < combined.length ? "\u2026" : "");
    snip = escapeHtml(snip);
    if (terms.length) {
      const re = new RegExp("(" + terms.map(escapeRegex).join("|") + ")", "gi");
      snip = snip.replace(re, "<mark>$1</mark>");
    }
    return snip;
  }

  function runSearch(query) {
    const q = query.trim();
    if (!q) {
      currentResults = SEARCH_INDEX.slice(0, 14);
      searchMeta.textContent = `${SEARCH_INDEX.length} sections`;
      renderResults(currentResults, [], "");
      return;
    }
    const qLower = q.toLowerCase();
    const terms = qLower.split(/\s+/).filter(Boolean);
    const scored = [];
    for (const entry of SEARCH_INDEX) {
      const tLow = entry.title.toLowerCase();
      const bodyLow = entry.text.toLowerCase();
      const codeLow = entry.code.toLowerCase();
      const allLow = tLow + " " + bodyLow + " " + codeLow;
      if (!terms.every(t => allLow.includes(t))) continue;

      let score = 0;
      if (tLow === qLower) score += 200;
      else if (tLow.includes(qLower)) score += 110;
      else if (terms.every(t => tLow.includes(t))) score += 75;
      if (bodyLow.includes(qLower)) score += 45;
      if (codeLow.includes(qLower)) score += 40;
      for (const t of terms) {
        if (tLow.includes(t)) score += 20;
        if (codeLow.includes(t)) score += 10;
        if (bodyLow.includes(t)) score += 5;
      }
      scored.push({ entry, score });
    }
    scored.sort((a, b) => b.score - a.score);
    currentResults = scored.slice(0, 25).map(s => s.entry);
    searchMeta.textContent = `${scored.length} match${scored.length === 1 ? "" : "es"}`;
    renderResults(currentResults, terms, q);
  }

  function renderResults(items, terms, rawQuery) {
    selectedIdx = 0;
    if (!items.length) {
      searchResults.innerHTML = '<div class="sr-empty">No matching sections or commands found.</div>';
      return;
    }
    searchResults.innerHTML = items.map((item, idx) => {
      const parentHtml = item.parent ? `<span class="sr-parent">in ${escapeHtml(item.parent)}</span>` : "";
      const snip = buildSnippet(item, terms, rawQuery);
      return `<button type="button" class="sr-item${idx === 0 ? " selected" : ""}" data-idx="${idx}">`
        + `<div class="sr-title-row"><span class="sr-title">${escapeHtml(item.title)}</span>${parentHtml}</div>`
        + `<div class="sr-snippet">${snip}</div>`
        + `</button>`;
    }).join("");
  }

  function openSearch() {
    searchModal.classList.add("open");
    searchInput.focus();
    searchInput.select();
    runSearch(searchInput.value);
  }
  function closeSearch() {
    searchModal.classList.remove("open");
  }
  function selectResult(idx) {
    const item = currentResults[idx];
    if (!item) return;
    const q = searchInput.value.trim();
    closeSearch();
    jumpToId(item.id);
    if (q) {
      const sec = document.getElementById(item.id);
      if (sec) setTimeout(() => highlightInSection(sec, q), 60);
    }
  }

  document.getElementById("openSearchBtn").addEventListener("click", openSearch);
  searchModal.addEventListener("click", e => { if (e.target === searchModal) closeSearch(); });
  searchInput.addEventListener("input", () => runSearch(searchInput.value));
  searchResults.addEventListener("click", e => {
    const btn = e.target.closest(".sr-item");
    if (btn) selectResult(Number(btn.dataset.idx));
  });

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (searchModal.classList.contains("open")) closeSearch();
      else openSearch();
      return;
    }
    if (e.key === "/" && !searchModal.classList.contains("open") && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      openSearch();
      return;
    }
    if (searchModal.classList.contains("open")) {
      if (e.key === "Escape") { closeSearch(); return; }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        selectedIdx = Math.min(currentResults.length - 1, selectedIdx + 1);
        updateSelectedResult();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        selectedIdx = Math.max(0, selectedIdx - 1);
        updateSelectedResult();
      } else if (e.key === "Enter") {
        e.preventDefault();
        selectResult(selectedIdx);
      }
    } else if (e.key === "Escape" && inpageHits.length) {
      clearInpageHighlights();
    }
  });

  function updateSelectedResult() {
    const els = searchResults.querySelectorAll(".sr-item");
    els.forEach((el, i) => el.classList.toggle("selected", i === selectedIdx));
    if (els[selectedIdx]) els[selectedIdx].scrollIntoView({ block: "nearest" });
  }

  updateScrollspy();
})();
</script>
</body>
</html>
"""


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    in_path = Path(sys.argv[1]) if len(sys.argv) > 1 else repo_root / "README.md"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else repo_root / "README.html"

    md_text = in_path.read_text(encoding="utf-8")
    conv = ReadmeConverter(md_text)
    body_html, toc_tree, search_index = conv.build_document()
    toc_html = render_toc_html(toc_tree)

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    page = (
        HTML_TEMPLATE.replace("__TOC_HTML__", toc_html)
        .replace("__BODY_HTML__", body_html)
        .replace("__SEARCH_INDEX_JSON__", json.dumps(search_index, ensure_ascii=False))
        .replace("__SECTION_COUNT__", str(len(search_index)))
        .replace("__GENERATED_AT__", generated_at)
    )

    out_path.write_text(page, encoding="utf-8")
    print(f"Generated {out_path} ({len(page):,} bytes, {len(search_index)} indexed sections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
