#!/usr/bin/env python3
import os
import sys
import zipfile
import re
import time
import shutil
import html
import hashlib
from pathlib import Path
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote


# ==============================
# 常量
# ==============================

SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:')
SAFE_SCHEMES = {'http', 'https', 'mailto', 'ftp', 'data'}

# 仅保留清理 HTML 注释的正则，废弃危险的 script/style 正则
COMMENT_RE = re.compile(r'(?s)<!--.*?-->')

HEAD_TAGS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
BAD_CHARS = set(r'\/:*?"<>|#%')
CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
MOJIBAKE_RE = re.compile(r'[^\x00-\x7f]{2,}')

WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
}

BACKTICK = chr(96)

# 需要完全忽略的 HTML 标签（包括其内部文本）
IGNORE_TAGS = {'script', 'style', 'noscript', 'template', 'head', 'title', 'meta', 'link'}


# ==============================
# 通用工具
# ==============================

def max_backtick_run(text: str) -> int:
    max_run = 0
    current = 0
    for ch in text:
        if ch == BACKTICK:
            current += 1
            if current > max_run: max_run = current
        else:
            current = 0
    return max_run

def make_code_fence(code: str) -> str:
    return BACKTICK * max(3, max_backtick_run(code) + 1)

def sanitize_path_segment(segment: str) -> str:
    segment = CONTROL_RE.sub('_', segment)
    segment = ''.join('_' if c in BAD_CHARS else c for c in segment)
    segment = segment.rstrip('. ')
    
    if not segment: return '_'
    if segment in ('.', '..'): return '_'
    
    base = segment.split('.', 1)[0]
    if base.upper() in WINDOWS_RESERVED_NAMES:
        segment = '_' + segment
    return segment

def make_attachment_dir_name(stem: str, identity: str = '') -> str:
    raw = stem or ''
    name = sanitize_path_segment(raw)
    name = re.sub(r'\s+', '_', name)
    name = name.strip('._')
    if not name: name = 'attachment'

    need_hash = False
    if not raw: need_hash = True
    if re.search(r'\s', raw): need_hash = True
    if name != re.sub(r'\s+', '_', raw): need_hash = True

    if need_hash and identity:
        digest = hashlib.sha256(identity.encode('utf-8', 'ignore')).hexdigest()[:8]
        return f'{name}_files_{digest}'
    return name + '_files'

def sanitize_local_src(src: str, attachment_prefix: str = 'index_files') -> str:
    if not src: return ''
    src = src.strip()
    if src.startswith('#'): return src
    if src.startswith('//'): return src

    m = SCHEME_RE.match(src)
    if m:
        scheme = m.group(0)[:-1].lower()
        if scheme not in SAFE_SCHEMES: return '#'
        return src

    src = src.split('#', 1)[0].split('?', 1)[0]
    src = unquote(src)
    while src.startswith('./'): src = src[2:]
    src = src.lstrip('/')

    parts = []
    for part in src.split('/'):
        if not part: continue
        parts.append(sanitize_path_segment(part))

    if parts and parts[0].lower() == 'index_files':
        parts[0] = attachment_prefix
    return '/'.join(parts)

def markdown_destination(url: str) -> str:
    if not url: return ''
    url = CONTROL_RE.sub(' ', url)
    url = re.sub(r'%(?![0-9A-Fa-f]{2})', '%25', url)
    
    replacements = {
        ' ': '%20', '(': '%28', ')': '%29', '<': '%3C', '>': '%3E',
        '[': '%5B', ']': '%5D', '"': '%22', "'": '%27', '|': '%7C',
    }
    for ch, rep in replacements.items():
        url = url.replace(ch, rep)
    return url

def escape_markdown_text(text: str) -> str:
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    for ch in ('\\', BACKTICK, '*', '_', '[', ']'):
        text = text.replace(ch, '\\' + ch)
    return text

def escape_leading_block_chars(text: str) -> str:
    if not text: return text
    text = re.sub(r'^(#{1,6})(\s)', r'\\\1\2', text)
    text = re.sub(r'^([-+])(\s)', r'\\\1\2', text)
    text = re.sub(r'^(\d{1,9})([.)])(\s)', r'\1\\\2\3', text)
    text = re.sub(r'^(-+)\s*$', r'\\\1', text)
    text = re.sub(r'^(=+)\s*$', r'\\\1', text)
    return text

def clean_html_comments(html_fragment: str) -> str:
    """仅清理 HTML 注释，不再使用正则清理 script/style，交由 Parser 状态机处理"""
    if not html_fragment: return ''
    return COMMENT_RE.sub('', html_fragment)

def decode_html_bytes(data: bytes) -> str:
    if data.startswith(b'\xef\xbb\xbf'):
        return data.decode('utf-8-sig', errors='replace').lstrip('\ufeff')
    if data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff'):
        return data.decode('utf-16', errors='replace').lstrip('\ufeff')
    try:
        text = data.decode('utf-8')
        if '\x00' not in text[:5000]: return text
    except UnicodeDecodeError: pass

    if len(data) % 2 == 0:
        try:
            text = data.decode('utf-16le')
            if '<' in text[:5000]: return text.lstrip('\ufeff')
        except UnicodeDecodeError: pass
        try:
            text = data.decode('utf-16be')
            if '<' in text[:5000]: return text.lstrip('\ufeff')
        except UnicodeDecodeError: pass

    try:
        text = data.decode('gb18030')
        if '<' in text[:5000]: return text
    except UnicodeDecodeError: pass

    return data.decode('utf-8', errors='replace')

def get_note_name(ziw_path: str) -> str:
    name = os.path.basename(ziw_path)
    if name.lower().endswith('.ziw'): name = name[:-4]
    if name.lower().endswith('.md'): name = name[:-3]
    return name or 'note'

def escape_yaml_string(value) -> str:
    if value is None: return ''
    s = str(value)
    s = CONTROL_RE.sub(' ', s)
    s = s.replace('\\', '\\\\').replace('"', '\\"')
    return s

def fix_zip_entry_name(name: str) -> str:
    if not MOJIBAKE_RE.search(name): return name
    try: raw = name.encode('cp437')
    except UnicodeEncodeError: return name
    for enc in ('utf-8', 'gb18030'):
        try:
            decoded = raw.decode(enc)
            if any(ord(ch) > 127 for ch in decoded): return decoded
        except UnicodeDecodeError: pass
    return name

def extract_body_html(html_content: str) -> str:
    """
    多重 Fallback 提取正文 HTML：
    1. 标准 <body>
    2. <html> 内剔除 <head>
    3. 全量返回（依赖 Parser 过滤）
    """
    m = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
    if m: return m.group(1)
    
    m = re.search(r'<html[^>]*>(.*?)</html>', html_content, re.DOTALL | re.IGNORECASE)
    if m:
        content = m.group(1)
        content = re.sub(r'(?is)<head[^>]*>.*?</head>', '', content)
        return content
        
    return html_content


# ==============================
# HTML -> Markdown 解析器
# ==============================

class HTMLToMarkdown(HTMLParser):
    def __init__(self, attachment_prefix: str = 'index_files'):
        super().__init__(convert_charrefs=True)
        self.attachment_prefix = attachment_prefix
        self.result = []
        
        # 核心修复：使用状态机栈忽略 script/style/head，彻底杜绝正则误杀正文
        self._ignore_stack = [] 
        
        self.list_stack = []
        self.list_counters = []
        self.container_stack = []
        self._pending_href = None
        self._after_li_marker = False
        self._li_just_closed = False
        self._block_boundary = True
        self._bold_count = 0
        self._em_count = 0
        self._in_inline_code = False
        self._inline_code_parts = []
        self._in_pre = False
        self._pre_parts = []
        self._pre_language = ''
        self._code_blocks = []
        self._table_stack = []
        self._suppressed_table_depth = 0

    def _block_prefix(self) -> str:
        return ''.join('> ' if c == 'quote' else '    ' for c in self.container_stack)

    def _marker_prefix(self) -> str:
        containers = self.container_stack
        if containers and containers[-1] == 'list': containers = containers[:-1]
        return ''.join('> ' if c == 'quote' else '    ' for c in containers)

    def _pop_container(self, kind: str):
        for i in range(len(self.container_stack) - 1, -1, -1):
            if self.container_stack[i] == kind:
                self.container_stack.pop(i)
                return

    def _start_line_with_prefix(self, prefix: str):
        if not self.result:
            self.result.append(prefix)
            return
        last = self.result[-1]
        if last.endswith('\n' + prefix): return
        if last.endswith('\n'):
            self.result[-1] = last + prefix
        else:
            self.result.append('\n' + prefix)

    def _append_block_newline(self):
        prefix = self._block_prefix()
        if not self.result:
            self.result.append('\n' + prefix)
            return
        last = self.result[-1]
        if self.container_stack:
            target = '\n' + prefix
            if last.endswith(target): return
            if last.endswith('\n'):
                self.result[-1] = last + prefix + '\n' + prefix
            else:
                self.result.append('\n' + prefix + '\n' + prefix)
        else:
            if last.endswith('\n\n'): return
            if last.endswith('\n'):
                self.result[-1] = last + '\n'
            else:
                self.result.append('\n\n')

    def _append_smart_space(self):
        if not self.result:
            self.result.append(' ')
            return
        last = self.result[-1]
        if isinstance(last, str) and (last.endswith(' ') or last.endswith('\n')):
            return
        self.result.append(' ')

    def _move_leading_space_after_opening_marker(self, data: str) -> str:
        if not data.startswith(' ') or not self.result: return data
        last = self.result[-1]
        if (last == '**' and self._bold_count > 0) or (last == '*' and self._em_count > 0):
            i = 0
            while i < len(data) and data[i] == ' ': i += 1
            spaces, rest = data[:i], data[i:]
            self.result[-1] = spaces + last
            return rest
        return data

    def _append_closing_marker(self, marker: str):
        moved = ''
        if self.result and isinstance(self.result[-1], str):
            tail = self.result[-1]
            stripped = tail.rstrip(' ')
            if stripped != tail:
                moved = tail[len(stripped):]
                if stripped:
                    self.result[-1] = stripped
                else:
                    self.result.pop()
        self.result.append(marker)
        if moved: self.result.append(moved)

    def _flush_inline_code(self):
        if not self._in_inline_code: return
        code = ''.join(self._inline_code_parts)
        self._in_inline_code = False
        self._inline_code_parts = []
        if not code: return
        run = max_backtick_run(code)
        delimiter = BACKTICK * max(1, run + 1)
        rendered = f'{delimiter} {code} {delimiter}' if code.startswith(BACKTICK) or code.endswith(BACKTICK) else f'{delimiter}{code}{delimiter}'
        if self._table_stack: rendered = rendered.replace('|', '&#124;')
        self.result.append(rendered)
        self._block_boundary = self._after_li_marker = self._li_just_closed = False

    def _flush_pre(self):
        if not self._in_pre: return
        code = ''.join(self._pre_parts).replace('\r\n', '\n').replace('\r', '\n')
        if code.startswith('\n'): code = code[1:]
        if code.endswith('\n'): code = code[:-1]
        fence, prefix, lang = make_code_fence(code), self._block_prefix(), self._pre_language or ''
        block = '\n' + prefix + fence + lang + '\n' + ''.join(prefix + line + '\n' for line in code.split('\n')) + prefix + fence + '\n'
        idx = len(self._code_blocks)
        self._code_blocks.append(block)
        self.result.append(f'\n\x00CODEBLOCK{idx}\x00\n')
        self._in_pre, self._pre_parts, self._pre_language, self._block_boundary = False, [], '', True

    def _close_pending_link(self):
        if self._pending_href is None: return
        self.result.append(f']({markdown_destination(self._pending_href)})')
        self._pending_href = None
        self._block_boundary = self._after_li_marker = self._li_just_closed = False

    def handle_starttag(self, tag, attrs):
        # 核心修复：状态机忽略 script/style/head 等标签，防止正则误杀
        if tag in IGNORE_TAGS:
            self._ignore_stack.append(tag)
            return
        if self._ignore_stack:
            return

        attrs_dict = dict(attrs)
        if self._in_pre:
            if tag == 'br': self._pre_parts.append('\n')
            elif tag == 'code' and not self._pre_language:
                m = re.search(r'language-([\w+.\-]+)', attrs_dict.get('class') or '')
                if m: self._pre_language = m.group(1)
            return
        if self._in_inline_code:
            if tag == 'br': self._inline_code_parts.append(' ')
            return

        if self._suppressed_table_depth > 0:
            if tag == 'table': self._suppressed_table_depth += 1
            elif tag in ('tr', 'td', 'th', 'br'): self._append_smart_space()
            return

        if self._table_stack and tag == 'table':
            self._suppressed_table_depth = 1
            self._append_smart_space()
            return

        if self._table_stack and (tag in ('p', 'div', 'ul', 'ol', 'li', 'blockquote', 'pre', 'hr') or tag in HEAD_TAGS):
            self._append_smart_space()
            return

        if tag != 'li': self._li_just_closed = False
        if tag in ('div', 'p', 'br', 'hr', 'ul', 'ol', 'li', 'blockquote', 'pre', 'table', 'tr', 'td', 'th') or tag in HEAD_TAGS:
            self._block_boundary = True
        if tag in ('br', 'hr', 'blockquote', 'pre') or tag in HEAD_TAGS:
            self._after_li_marker = False

        if tag == 'div':
            if self.list_stack and self._after_li_marker: self._after_li_marker = False
            elif self.list_stack: self._start_line_with_prefix(self._block_prefix())
            else: self._append_block_newline()
        elif tag == 'br':
            self.result.append(' ' if self._table_stack else '\n' + self._block_prefix())
        elif tag == 'p':
            if self.list_stack and self._after_li_marker: self._after_li_marker = False
            else: self._append_block_newline()
        elif tag in HEAD_TAGS:
            self._append_block_newline()
            self.result.append('#' * int(tag[1]) + ' ')
        elif tag in ('strong', 'b'):
            self._bold_count += 1
            self.result.append('**')
        elif tag in ('em', 'i'):
            self._em_count += 1
            self.result.append('*')
        elif tag == 'code':
            self._in_inline_code, self._inline_code_parts, self._after_li_marker = True, [], False
        elif tag == 'blockquote':
            self.container_stack.append('quote')
            self._start_line_with_prefix(self._block_prefix())
        elif tag == 'a':
            if self._pending_href is not None: self._close_pending_link()
            href = re.sub(r'\s+', ' ', attrs_dict.get('href') or '').strip()
            href = sanitize_local_src(href, self.attachment_prefix)
            if href:
                self.result.append('[')
                self._pending_href = href
            else: self._pending_href = None
        elif tag == 'img':
            src = sanitize_local_src(attrs_dict.get('src') or '', self.attachment_prefix)
            alt = escape_markdown_text(re.sub(r'\s+', ' ', attrs_dict.get('alt') or '').strip())
            if self._table_stack: alt = alt.replace('|', '\\|')
            self.result.append(f'![{alt}]({markdown_destination(src)})')
            self._block_boundary = self._after_li_marker = self._li_just_closed = False
        elif tag in ('ul', 'ol'):
            self._after_li_marker = False
            if self.list_stack: self._start_line_with_prefix(self._block_prefix())
            else: self._append_block_newline()
            self.list_stack.append(tag)
            self.list_counters.append(0 if tag == 'ol' else None)
            self.container_stack.append('list')
        elif tag == 'li':
            self._li_just_closed = False
            prefix = self._marker_prefix()
            self._start_line_with_prefix(prefix)
            if self.list_stack and self.list_stack[-1] == 'ol':
                self.list_counters[-1] = (self.list_counters[-1] or 0) + 1
                marker = f'{self.list_counters[-1]}. '
            else: marker = '- '
            self.result.append(marker)
            self._after_li_marker = True
        elif tag == 'hr':
            self._append_block_newline()
            self.result.append('***\n' + self._block_prefix())
        elif tag == 'pre':
            self._in_pre, self._pre_parts, self._pre_language = True, [], ''
            m = re.search(r'language-([\w+.\-]+)', attrs_dict.get('class') or '')
            if m: self._pre_language = m.group(1)
        elif tag == 'table':
            self._after_li_marker = False
            if self.list_stack: self._start_line_with_prefix(self._block_prefix())
            else: self._append_block_newline()
            self._table_stack.append({'header_done': False, 'cell_count': 0, 'in_row': False})
        elif tag == 'tr':
            if self._table_stack:
                self._table_stack[-1]['cell_count'] = 0
                self._table_stack[-1]['in_row'] = True
                self._start_line_with_prefix(self._block_prefix())
                self.result.append('|')
        elif tag in ('td', 'th'):
            if self._table_stack and self._table_stack[-1]['in_row']:
                self._table_stack[-1]['cell_count'] += 1
                self.result.append(' ')

    def handle_endtag(self, tag):
        # 核心修复：状态机出栈
        if self._ignore_stack:
            if self._ignore_stack[-1] == tag:
                self._ignore_stack.pop()
            return

        if self._in_pre:
            if tag == 'pre': self._flush_pre()
            return
        if self._in_inline_code:
            if tag == 'code': self._flush_inline_code()
            return

        if self._suppressed_table_depth > 0:
            if tag == 'table': self._suppressed_table_depth -= 1
            elif tag in ('tr', 'td', 'th'): self._append_smart_space()
            return

        if self._table_stack and (tag in ('p', 'div', 'li', 'ul', 'ol', 'blockquote', 'pre', 'hr') or tag in HEAD_TAGS):
            return

        if tag in HEAD_TAGS or tag == 'p' or tag == 'div':
            self.result.append('\n')
            self._block_boundary = True
        elif tag in ('strong', 'b'):
            if self._bold_count > 0:
                self._bold_count -= 1
                self._append_closing_marker('**')
        elif tag in ('em', 'i'):
            if self._em_count > 0:
                self._em_count -= 1
                self._append_closing_marker('*')
        elif tag == 'a':
            self._close_pending_link()
        elif tag == 'li':
            self.result.append('\n')
            self._li_just_closed, self._after_li_marker, self._block_boundary = True, False, True
        elif tag in ('ul', 'ol'):
            if self.list_stack: self.list_stack.pop()
            if self.list_counters: self.list_counters.pop()
            self._pop_container('list')
            self._after_li_marker, self._li_just_closed = False, False
            if self.list_stack: self._start_line_with_prefix(self._block_prefix())
            else: self._append_block_newline()
            self._block_boundary = True
        elif tag == 'blockquote':
            self._pop_container('quote')
            self._append_block_newline()
            self._block_boundary = True
        elif tag in ('td', 'th'):
            if self._table_stack and self._table_stack[-1]['in_row']:
                self.result.append(' |')
            self._block_boundary = True
        elif tag == 'tr':
            if self._table_stack:
                current = self._table_stack[-1]
                if current['in_row'] and not current['header_done'] and current['cell_count'] > 0:
                    self.result.append('\n' + self._block_prefix() + '|' + ' --- |' * current['cell_count'])
                    current['header_done'] = True
                current['in_row'] = False
            self._block_boundary = True
        elif tag == 'table':
            if self._table_stack: self._table_stack.pop()
            if not self._table_stack: self._suppressed_table_depth = 0
            self._append_block_newline()
            self._block_boundary = True

    def handle_data(self, data):
        # 核心修复：忽略区域内的文本直接丢弃
        if self._ignore_stack: return
        if not data: return
        
        if self._in_pre:
            self._pre_parts.append(data)
            return
        
        data = data.replace('\xa0', ' ')
        
        if self._in_inline_code:
            data = data.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
            if data:
                if not self._inline_code_parts and self._block_boundary:
                    data = data.lstrip()
                if data:
                    self._inline_code_parts.append(data)
                self._block_boundary = self._after_li_marker = self._li_just_closed = False
            return

        data = re.sub(r'\s+', ' ', data)
        if not data.strip():
            if self._block_boundary or self._after_li_marker or self._li_just_closed: return
            self.result.append(' ')
            return

        if self._block_boundary:
            data = data.lstrip()
            if not data: return

        data = self._move_leading_space_after_opening_marker(data)
        if not data: return

        data = escape_markdown_text(data)
        if self._block_boundary: data = escape_leading_block_chars(data)
        if self._table_stack: data = data.replace('|', '\\|')

        self.result.append(data)
        self._block_boundary = self._after_li_marker = self._li_just_closed = False

    def get_markdown(self) -> str:
        if self._in_pre: self._flush_pre()
        if self._in_inline_code: self._flush_inline_code()
        if self._bold_count > 0:
            for _ in range(self._bold_count): self._append_closing_marker('**')
            self._bold_count = 0
        if self._em_count > 0:
            for _ in range(self._em_count): self._append_closing_marker('*')
            self._em_count = 0
        if self._pending_href is not None: self._close_pending_link()

        result = ''.join(self.result).replace('\r\n', '\n').replace('\r', '\n')
        result = re.sub(r'[ \t]+\n', '\n', result)
        result = re.sub(r'\n{3,}', '\n\n', result)
        result = re.sub(r'\n[ \t]*(?:>[ \t]*)+\n(?=\n)', '\n', result)

        lines = result.split('\n')
        while lines and re.match(r'^[ \t]*(?:>[ \t]*)*$', lines[-1]):
            lines.pop()
        result = '\n'.join(lines)
        
        for i, code_block in enumerate(self._code_blocks):
            result = result.replace(f'\x00CODEBLOCK{i}\x00', code_block)
        return result.strip()


def convert_html_to_markdown(html_content: str, attachment_prefix: str = 'index_files') -> str:
    parser = HTMLToMarkdown(attachment_prefix=attachment_prefix)
    parser.feed(html_content)
    parser.close()
    return parser.get_markdown()


def extract_metadata(html_content: str, ziw_path: str) -> dict:
    metadata = {'title': '', 'created': '', 'modified': '', 'guid': ''}
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html_content, re.IGNORECASE | re.DOTALL)
    if title_match: metadata['title'] = html.unescape(title_match.group(1)).strip()

    for pattern in [
        r'<meta[^>]*name=["\']?CreationTime["\']?[^>]*content=["\']([^"\']+)["\']',
        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']?CreationTime["\']?'
    ]:
        m = re.search(pattern, html_content, re.IGNORECASE)
        if m:
            dm = re.search(r'\d{4}-\d{2}-\d{2}', m.group(1))
            if dm:
                metadata['created'] = dm.group(0)
                break

    # 使用增强的 Fallback 提取正文用于 metadata 兜底
    body_html = extract_body_html(html_content)
    text_content = html.unescape(re.sub(r'<[^>]+>', '\n', body_html)).replace('\xa0', ' ')
    
    for line in text_content.split('\n'):
        line = re.sub(r'\s+', ' ', line).strip()
        if not line: continue
        if not metadata['title']: metadata['title'] = line[:100]
        if not metadata['created']:
            dm = re.search(r'\d{4}-\d{2}-\d{2}', line)
            if dm: metadata['created'] = dm.group(0)
        break

    if not metadata['title']: metadata['title'] = get_note_name(ziw_path)
    try: metadata['modified'] = datetime.fromtimestamp(os.path.getmtime(ziw_path)).strftime('%Y-%m-%d %H:%M:%S')
    except Exception: pass
    metadata['guid'] = get_note_name(ziw_path)
    return metadata


def process_ziw_file(ziw_path: str, output_base_dir: str, source_base_dir: str):
    try:
        with zipfile.ZipFile(ziw_path, 'r') as zf:
            all_files = zf.namelist()
            index_name = next((f for f in all_files if f.lower() == 'index.html'), None)
            if not index_name:
                print(f"Warning: No index.html in {ziw_path}", file=sys.stderr)
                return None, False

            html_content = decode_html_bytes(zf.read(index_name))
            cleaned_html = clean_html_comments(html_content)
            metadata = extract_metadata(cleaned_html, ziw_path)

            rel_path = os.path.relpath(ziw_path, start=source_base_dir)
            output_rel = re.sub(r'\.ziw$', '.md', rel_path, flags=re.IGNORECASE)
            output_path = Path(output_base_dir) / Path(output_rel)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            att_dir_name = make_attachment_dir_name(output_path.stem, identity=output_rel)

            # 使用多重 Fallback 提取正文 HTML
            body_html = extract_body_html(cleaned_html)
            markdown_content = convert_html_to_markdown(body_html, attachment_prefix=att_dir_name)

            meta_lines = [
                '---',
                f'title: "{escape_yaml_string(metadata["title"])}"',
                f'created: "{escape_yaml_string(metadata["created"])}"',
                f'modified: "{escape_yaml_string(metadata["modified"])}"',
                f'guid: "{escape_yaml_string(metadata["guid"])}"',
                f'source: "{escape_yaml_string(f"WizNote ({os.path.basename(ziw_path)})")}"',
                '---', ''
            ]
            final_markdown = '\n'.join(meta_lines) + markdown_content

            att_files = []
            for f in all_files:
                norm_f = f.replace('\\', '/')
                if norm_f.lower().startswith('index_files/') and not norm_f.endswith('/'):
                    att_files.append((f, norm_f))

            has_atts = len(att_files) > 0
            if has_atts:
                att_dir = output_path.parent / att_dir_name
                att_dir.mkdir(parents=True, exist_ok=True)
                for att_orig, att_norm in att_files:
                    fixed_att = fix_zip_entry_name(att_orig)
                    fixed_norm = fixed_att.replace('\\', '/')
                    if not fixed_norm.lower().startswith('index_files/'): continue
                    
                    rel_att = fixed_norm[len('index_files/'):].lstrip('/')
                    if not rel_att: continue
                    
                    parts = [sanitize_path_segment(p) for p in rel_att.split('/') if p]
                    if not parts: continue

                    target_path = att_dir.joinpath(*parts)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with zf.open(att_orig) as src, open(target_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    except Exception as e:
                        print(f"  Warning: Failed to extract {att_orig}: {e}", file=sys.stderr)

            with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(final_markdown)
            return str(output_path), has_atts

    except Exception as e:
        print(f"Error processing {ziw_path}: {e}", file=sys.stderr)
        return None, False


def main():
    if len(sys.argv) >= 3:
        source_dir, output_dir = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    elif len(sys.argv) == 2:
        source_dir = Path(sys.argv[1]).expanduser()
        output_dir = source_dir.parent / 'ziw2md_output'
    else:
        source_dir, output_dir = Path('.'), Path('./ziw2md_output')
        print("Notice: Using current directory. Press Ctrl+C to cancel, or wait 3s...")
        time.sleep(3)

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"Error: Invalid source directory: {source_dir}")
        sys.exit(1)

    ziw_files = sorted(p for p in source_dir.rglob('*') if p.is_file() and p.suffix.lower() == '.ziw')
    print(f"Found {len(ziw_files)} .ziw files\nSource: {source_dir.resolve()}\nOutput: {output_dir.resolve()}\n" + "-"*50)
    output_dir.mkdir(parents=True, exist_ok=True)

    success = error = with_atts = 0
    total = len(ziw_files)
    for i, ziw_file in enumerate(ziw_files):
        if i == 0 or (i + 1) % 50 == 0: print(f"Processing {i + 1}/{total}...")
        res = process_ziw_file(str(ziw_file), str(output_dir), str(source_dir))
        if res[0]:
            success += 1
            if res[1]: with_atts += 1
        else: error += 1

    print("="*50 + f"\nConversion complete!\nSuccess: {success}\nErrors/Skipped: {error}\nWith attachments: {with_atts}\nOutput: {output_dir.resolve()}")

if __name__ == '__main__':
    main()
