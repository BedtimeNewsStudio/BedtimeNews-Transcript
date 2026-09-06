#!/usr/bin/env python3
"""
bedtimenews-md builder
把 bedtimenews-archive-contents integration 分支上「多步校对完成」的睡前消息文稿
转换为本仓库的干净 Markdown 版本：
  - 4 位 padding 期号，每 100 期一个文件夹（0001-0100 …）；无期号 → misc/
  - 顶部 B站/YouTube 嵌入改为纯 URL 链接（官方优先，非官方补档标注）
  - 正文只保留纯 Markdown 文本（去图片、font 标签、wiki 类标记、HTML 注释）
  - 末尾附录：取自校对 PR 描述的「联网事实核对及信息来源 / 事实订正 / 待核对」
    （错别字、标点、格式、音频复核过程记录一律不入附录；同音错录直接修复不提及）

Usage:
  python3 build.py                # 全量生成（已存在且不强制则跳过）
  python3 build.py --force        # 覆盖重生成
  python3 build.py --only 116     # 只生成文件名含 116 的集
"""
import re
import sys
import json
import argparse
import subprocess
from pathlib import Path

ARCHIVE = Path('/Users/dongziyu/code/bedtimenews-archive-contents')
INTEG = ARCHIVE / '.claude/worktrees/integ-cp'
INTEG_BRANCH = 'origin/integration/proofread-all'
REPO = Path(__file__).resolve().parent
OUT = REPO / 'contents' / 'ShuiQianXiaoXi'
PRS_CACHE = REPO / '.localonly' / 'prs-all.json'
UPSTREAM = 'bedtimenews/bedtimenews-archive-contents'

SECTION = 'main'
SRC_DIRS = ['1-100', '101-200', '201-300', '301-400', '401-500', '501-600',
            '601-700', '801-900', '901-1000', '1001-1100',
            'prequel', 'summerbreak', 'winterbreak2023']

# 无期号文件 → misc/ 下的名字（含期号的番外 x.5 不在此表）
MISC_MAP = {
    'main/prequel/0.md': '0',
    'main/prequel/l1.md': 'l1', 'main/prequel/l2.md': 'l2',
    'main/prequel/l3.md': 'l3', 'main/prequel/l4.md': 'l4',
    'main/prequel/l5.md': 'l5', 'main/prequel/l6.md': 'l6',
    'main/summerbreak/1.md': 'summerbreak-1',
    'main/summerbreak/2.md': 'summerbreak-2',
    'main/winterbreak2023/1.md': 'winterbreak2023-1',
    'main/winterbreak2023/2.md': 'winterbreak2023-2',
    'main/winterbreak2023/3.md': 'winterbreak2023-3',
    'main/winterbreak2023/4.md': 'winterbreak2023-4',
    'main/winterbreak2023/5.md': 'winterbreak2023-5',
    'main/winterbreak2023/6.md': 'winterbreak2023-6',
    'main/winterbreak2023/essay.md': 'winterbreak2023-essay',
    'main/winterbreak2023/speech2023.md': 'speech2023',
    'main/1-100/speech2019.md': 'speech2019',
    'main/501-600/thewordof2022.md': 'thewordof2022',
    'main/601-700/thewordof2023.md': 'thewordof2023',
    'main/901-1000/2025interview-1.md': '2025interview-1',
    'main/901-1000/2025interview-2.md': '2025interview-2',
}
# 有期号但文件名带后缀的番外（N-M.md → 期号 N.5）
X5_RE = re.compile(r'^(\d+)-(\d+)\.md$')

# ---------------------------------------------------------------- source side

def parse_tabs_zone(raw):
    """返回 (各 tab 的 [(label, notes, bvids, ytid)], 是否有 Tabs)。"""
    stripped = re.sub(r'<!--.*?-->', '', raw, flags=re.S)
    if '# Tabs' not in stripped:
        return [], False
    lines = stripped.split('\n')
    i = next(i for i, l in enumerate(lines) if l.startswith('# Tabs'))
    tabs = []           # (label, [note lines])
    cur = None
    for l in lines[i + 1:]:
        s = l.strip()
        if not s:
            continue
        if s.startswith('## '):
            cur = (s[3:].strip(), [])
            tabs.append(cur)
            continue
        if cur is None:
            continue
        if s.startswith(('<div', '<iframe', '</div', '{.is-')) or re.match(r'^#\s*$', s):
            continue
        if s.startswith('>'):
            cur[1].append(s.lstrip('> ').strip())
            continue
        break  # 正文开始
    # 逐 tab 提取 iframe（在 stripped 文本上按 label 定位）
    result = []
    pos = stripped.index('# Tabs')
    for idx, (label, notes) in enumerate(tabs):
        start = stripped.index(f'## {label}', pos)
        if idx + 1 < len(tabs):
            end = stripped.index(f'## {tabs[idx + 1][0]}', start)
        else:
            end = len(stripped)
        seg = stripped[start:end]
        bvids = [b for b in re.findall(r'bvid=([A-Za-z0-9]+)', seg) if b != 'BVID']
        yts = [y for y in re.findall(r'youtube-nocookie\.com/embed/([A-Za-z0-9_-]+)', seg)
               if y != 'YouTubeVID']
        result.append((label, notes, bvids, yts))
        pos = start
    return result, True


def clean_body(raw):
    lines = raw.split('\n')
    # 去 frontmatter
    start = 0
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                start = i + 1
                break
    raw = '\n'.join(lines[start:])
    raw = re.sub(r'<!--.*?-->', '', raw, flags=re.S)   # 先去注释（西瓜视频块等）
    # 去 Tabs 区（含其内说明性 blockquote）
    zone, has_tabs = parse_tabs_zone(raw)
    if has_tabs:
        lines = raw.split('\n')
        i = next(i for i, l in enumerate(lines) if l.startswith('# Tabs'))
        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if not s or s.startswith(('## ', '<div', '<iframe', '</div', '>', '{.is-')) \
                    or re.match(r'^#\s*$', s):
                j += 1
                continue
            break
        raw = '\n'.join(lines[:i] + lines[j:])
    # 正文清理
    raw = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', raw)                 # 图片
    raw = re.sub(r'<font[^>]*>|</font>', '', raw)                  # 字体标签
    raw = re.sub(r'\s*\{\.is-[a-z]+\}', '', raw)                   # wiki class
    raw = re.sub(r'\s*\{\.(tabset|links-list|tab)\}', '', raw)
    raw = re.sub(r'<(https?://[^>\s]+)>', r'\1', raw)              # 尖括号链接
    raw = re.sub(r'^> ?以下文本为音频转录结果.*$', '', raw, flags=re.M)  # 整理状态警示
    # 正文中残留的嵌入播放器 → 纯 URL
    def iframe_url(m):
        src = m.group(1)
        bm = re.search(r'bvid=([A-Za-z0-9]+)', src)
        if bm:
            return f'https://www.bilibili.com/video/{bm.group(1)}'
        ym = re.search(r'youtube[^/]*\.com/embed/([A-Za-z0-9_-]+)', src)
        if ym:
            return f'https://www.youtube.com/watch?v={ym.group(1)}'
        return src if src.startswith('http') else ''
    raw = re.sub(r'<iframe[^>]*src="([^"]*)"[^>]*>\s*(?:</iframe>)?', lambda m: iframe_url(m), raw)
    raw = re.sub(r'^<div[^>]*>.*</div>\s*$', '', raw, flags=re.M)
    raw = re.sub(r'^</?div[^>]*>\s*$', '', raw, flags=re.M)
    # wiki/Pandoc 类标记：[文字]{.underline} → 文字
    raw = re.sub(r'\[([^\]]+)\]\{\.[a-zA-Z-]+\}', r'\1', raw)
    raw = re.sub(r'\s*\{\.[a-zA-Z-]+\}', '', raw)
    # 链接文字与地址相同的自链接 → 裸 URL
    raw = re.sub(r'\[((?:https?|//)[^\]]*)\]\(([^)\s]+)\)',
                 lambda m: m.group(2) if norm(m.group(1)) == norm(m.group(2)) else m.group(0), raw)
    raw = re.sub(r'^#\s*$', '', raw, flags=re.M)                   # 空标题
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    return raw.strip()

# ------------------------------------------------------------ appendix side

RE_CHANGE = re.compile(r'→|改为|改成|订正|修正|勘误|补回|补为|删[除去]|改作')
RE_CONF = re.compile(r'未改|无需改动|不需改动|无误|属实|一致|正确|保留|未动|维持原稿|与原文相符|不改')
RE_TRANSCRIPTION = re.compile(
    r'同音|转写|错别字|错字|赘字|衍字|脱字|漏字|叠字|标点|断行|空行|空格|'
    r'字体|font|全角|半角|引号|格式|的/得|地/得')
RE_FACTUAL = re.compile(
    r'http|经核实|核实为|核实：|史料|来源|通行|对应|互证|查证|据.*报道|'
    r'史实|纪年|享年|日期|年份|年代|年龄|省份|省界|口误|口播.*实为|实为')
RE_BOILER = re.compile(
    r'iframe|占位符|管理员补充|YouTubeVID|BVID|留待|维持原编号|敬请谅解|'
    r'本校对仅|校对规则|敏感.*(未|免).*(检索|核对)|按惯例.*(检索|核对)')
RE_BOILER_TAIL = re.compile(r'B站/?YouTube iframe|YouTube嵌入地址|嵌入地址中的|本页视频区|留待管理员补充|占位符未?动')
RE_PENDING_HEAD = re.compile(r'待核实|待核对|存疑|待确认|遗留')
RE_FACT_HEAD = re.compile(r'事实核对|事实订正|系统性|事实性|来源|核实|核对说明|核对（|已核实|订正|勘误|补充')
RE_SKIP_HEAD = re.compile(
    r'错别字|标点|格式|重排|审计|difflib|组段|音频复核|二次复核|二次校对|第二轮|'
    r'语音转写|源文核对|其他|其它')
RE_PROCESS_HEAD = re.compile(r'音频复核|二次复核|二次校对|第二轮|复核追加|复核仍|全库音频')
CONTAINER_HEAD2 = re.compile(r'修改说明|修改内容|修改概要')
CONTAINER_HEAD = re.compile(r'修改内容|修改说明|修改概要')

def split_sections(body):
    """按 ##/### 切分，返回 [(level, heading, content)]，body 前导记为 (0,'',…)。"""
    parts = []
    cur_lvl, cur_head, buf = 0, '', []
    for line in body.split('\n'):
        m2 = re.match(r'^## (?!#)\s*(.+?)\s*$', line)
        m3 = re.match(r'^###\s*(.+?)\s*$', line)
        if m2 or m3:
            parts.append((cur_lvl, cur_head, '\n'.join(buf)))
            if m2:
                cur_lvl, cur_head = 2, m2.group(1)
            else:
                cur_lvl, cur_head = 3, m3.group(1)
            buf = []
        else:
            buf.append(line)
    parts.append((cur_lvl, cur_head, '\n'.join(buf)))
    return parts


def bullets(content):
    """把一段内容拆成条目（-、*、1. 开头；非列表行并入上一条）。"""
    out = []
    for line in content.split('\n'):
        s = line.strip()
        if not s or re.match(r'^-{2,}$', s):
            continue
        if re.match(r'^([-*]|\d+[.、])\s*', s):
            s = re.sub(r'^([-*]|\d+[.、])\s*', '', s)
            s = RE_BOILER_TAIL.split(s)[0].strip()
            if not s or RE_BOILER.search(s):
                continue
            out.append(s)
        elif out:
            out[-1] += ' ' + s
        elif not RE_BOILER.search(s):
            out.append(s)
    return [t for t in (RE_BOILER_TAIL.split(x)[0].strip() for x in out) if t]


def norm(text):
    return re.sub(r'\s+', '', text)


class Appendix:
    def __init__(self):
        self.checks = []      # 联网事实核对及信息来源（含确认与带来源订正）
        self.corr = []        # 事实订正（脚注），(bullet, old, new)
        self.pending = []     # 待核对
        self._seen = set()

    def _dedup(self, bucket, key, store=None):
        k = norm(key)
        if k in self._seen:
            return False
        self._seen.add(k)
        bucket.append(store if store is not None else key)
        return True

    def add_check(self, item):
        self._dedup(self.checks, item)

    def add_pending(self, item):
        self._dedup(self.pending, item)

    def add_corr(self, item):
        # item = (bullet, old, new)；按 bullet 文本去重
        self._dedup(self.corr, item[0], item)

    def empty(self):
        return not (self.checks or self.corr or self.pending)


def classify_pr_body(body, app):
    for lvl, head, content in split_sections(body):
        head_clean = re.sub(r'^[一二三四五六七八九十0-9]+、\s*', '', head or '')
        head_clean = re.sub(r'（[^）]*）', '', head_clean).strip()
        if lvl == 0:  # PR 描述开头、无标题 → 按容器处理
            if not content.strip():
                continue
            for b in bullets(content):
                classify_bullet(b, app)
            continue
        if RE_PROCESS_HEAD.search(head_clean) and not CONTAINER_HEAD2.search(head_clean):
            # 音频复核等过程记录：只提取口误勘误订正与真正未决的存疑
            for b in bullets(content):
                if re.search(r'口误勘误|口播.*实为|实为', b) and RE_CHANGE.search(b):
                    classify_bullet(b, app)
                elif re.search(r'分歧不定|无法定案|证据不足', b):
                    app.add_pending(b)
        elif RE_PENDING_HEAD.search(head_clean):
            for b in bullets(content):
                app.add_pending(b)
        elif RE_SKIP_HEAD.search(head_clean):
            # 纯过程/格式记录：不进附录
            pass
        elif RE_FACT_HEAD.search(head_clean) or CONTAINER_HEAD.search(head_clean):
            for b in bullets(content):
                classify_bullet(b, app)
        # 其余标题（如“审计”）不进附录


RE_NOCHANGE = re.compile(r'无[^。；]{0,14}需?订正|无需?修正|无日期.{0,8}错误|未发现.{0,8}(错误|订正)')

def classify_bullet(b, app):
    is_change = bool(RE_CHANGE.search(b))
    is_conf = bool(RE_CONF.search(b))
    has_url = bool(re.search(r'https?://', b))
    # 证据/评注语言在引号之外；引号内的字面内容不参与关键词判断
    meta = re.sub(r'「[^」]*」|“[^”]*”|`[^`]*`', '', b)
    if re.search(RE_NOCHANGE, meta):
        is_change, is_conf = False, True
    if not is_change and not is_conf:
        # 无变动说明、但带来源的核查记录也算事实核对
        if has_url or re.search(r'核实|核对|查证', meta):
            app.add_check(b)
        return
    if is_change:
        transcription = bool(RE_TRANSCRIPTION.search(meta))
        factual = bool(RE_FACTUAL.search(meta))
        overt = bool(re.search(r'口误勘误|口播.*实为|史实|实为', meta))
        if overt or (factual and not transcription):
            old, new = extract_old_new(b)
            app.add_corr((b, old, new))
            if has_url or factual:
                app.add_check(b)
        elif has_url:
            # 带来源但属同音/转写类：修复已在正文，不进附录（用户规则）
            pass
    else:  # confirmation
        if has_url or re.search(r'核实|与.*一致|互证|有据', b):
            app.add_check(b)


def extract_old_new(b):
    """从条目里抽取（改前，改后）文本，用于正文脚注锚定。"""
    for q in ('「', '“'):
        close = '」' if q == '「' else '”'
        quotes = re.findall(re.escape(q) + r'([^' + re.escape(close) + r']+)' + re.escape(close), b)
        if not quotes:
            continue
        m = re.search(re.escape(q) + r'([^' + re.escape(close) + r']+)' + re.escape(close)
                      + r'\s*(?:→|改为|改成|改作|订正为|修正为)\s*'
                      + re.escape(q) + r'([^' + re.escape(close) + r']+)' + re.escape(close), b)
        if m:
            return m.group(1), m.group(2)
        if len(quotes) >= 2:
            # “A”→“B” 之外的形态：取前两个引号分别作改前/改后
            return quotes[0], quotes[1]
        return (quotes[0] if quotes else None), None
    return None, None


def render_appendix(app):
    out = []
    out += ['---', '', '## 附录', '']
    if app.checks:
        out += ['### 联网事实核对及信息来源', '']
        out += [f'- {c}' for c in app.checks]
        out.append('')
    if app.corr:
        out += ['### 事实订正', '']
        for idx, (b, _old, _new) in enumerate(app.corr, 1):
            text = re.sub(r'\s+', ' ', b).strip()
            out.append(f'[^{idx}]: {text}')
        out.append('')
    if app.pending:
        out += ['### 待核对', '']
        out += [f'- {p}' for p in app.pending]
        out.append('')
    return '\n'.join(out)

# ---------------------------------------------------------------- PR mapping

def load_prs():
    if not PRS_CACHE.exists():
        print('fetching PRs …')
        PRS_CACHE.parent.mkdir(exist_ok=True)
        r = subprocess.run(['gh', 'pr', 'list', '--repo', UPSTREAM, '--state', 'all',
                            '--limit', '3000', '--json',
                            'number,title,headRefName,state,body'],
                           capture_output=True, text=True, check=True)
        PRS_CACHE.write_text(r.stdout)
    return json.loads(PRS_CACHE.read_text())


def pr_map():
    """episode key → 按 PR 号排序的 body 列表。key: int 期号 / '13-2' / 分支后缀。"""
    prs = load_prs()
    m = {}
    for p in prs:
        b = p['headRefName']
        if not b.startswith('proofread/') or b.startswith('proofread/ref'):
            continue
        x = b.split('/', 1)[1]
        key = None
        mm = re.match(r'^(?:transcribe-)?(\d+)([bc]|-retry)?$', x)
        if mm:
            key = int(mm.group(1))
        elif re.match(r'^\d+-\d+$', x):
            key = x
        else:
            key = x  # prequel-l1 / speech2019 / wb2023-2 / …
        m.setdefault(key, []).append((p['number'], p['body'] or ''))
    for v in m.values():
        v.sort()
    return m

# ---------------------------------------------------------------- build

def target_for(src_rel):
    """返回 (folder, filename)，misc 文件夹直接给 (None, name)。"""
    if src_rel in MISC_MAP:
        return ('misc', MISC_MAP[src_rel])
    base = src_rel.split('/')[-1]
    m = X5_RE.match(base)
    if m:
        n = int(m.group(1))
        folder = f'{((n - 1) // 100) * 100 + 1:04d}-{((n - 1) // 100) * 100 + 100:04d}'
        return (folder, f'{n:04d}.5')
    m = re.match(r'^(\d+)\.md$', base)
    if m:
        n = int(m.group(1))
        folder = f'{((n - 1) // 100) * 100 + 1:04d}-{((n - 1) // 100) * 100 + 100:04d}'
        return (folder, f'{n:04d}')
    return None


def pr_key_for(src_rel):
    if src_rel in MISC_MAP:
        name = MISC_MAP[src_rel]
    base = src_rel.split('/')[-1]
    if src_rel in MISC_MAP:
        name = MISC_MAP[src_rel]
        alias = {'l1': 'prequel-l1', 'l2': 'prequel-l2', 'l3': 'prequel-l3',
                 'l4': 'prequel-l4', 'l5': 'prequel-l5', 'l6': 'prequel-l6',
                 'summerbreak-1': 'summerbreak-1', 'summerbreak-2': 'summerbreak-2',
                 'winterbreak2023-1': 'winterbreak2023-1',
                 'winterbreak2023-2': 'wb2023-2', 'winterbreak2023-3': 'wb2023-3',
                 'winterbreak2023-4': 'wb2023-4', 'winterbreak2023-5': 'wb2023-5',
                 'winterbreak2023-6': 'winterbreak2023-6',
                 'winterbreak2023-essay': 'winterbreak2023-essay',
                 'speech2023': 'speech2023', 'speech2019': 'speech2019',
                 'thewordof2022': 'thewordof2022', 'thewordof2023': 'thewordof2023',
                 '2025interview-1': '2025interview-1',
                 '2025interview-2': '2025interview-2', '0': None}
        return alias.get(name, name)
    m = X5_RE.match(base)
    if m:
        return f'{m.group(1)}-{m.group(2)}'
    m = re.match(r'^(\d+)\.md$', base)
    if m:
        return int(m.group(1))
    return None


def video_links(tabs):
    lines, notes = [], []
    for label, tnotes, bvids, yts in tabs:
        note_text = ' '.join(tnotes)
        if label == 'B站':
            if bvids:
                tag = '（非官方补档）' if re.search(r'补档|其它用户上传|其他用户上传', note_text) else ''
                for b in bvids:
                    lines.append(f'- [Bilibili{tag}](https://www.bilibili.com/video/{b})')
            if re.search(r'已被删除|未找到原视频', note_text):
                notes.append('B站官方视频已删除。')
        elif label == 'YouTube':
            for y in yts:
                if re.search(r'尚未发布|此处不适用|^无$', note_text):
                    continue
                tag = '（非官方补档）' if re.search(r'补档|其它用户上传|其他用户上传', note_text) else ''
                lines.append(f'- [YouTube{tag}](https://www.youtube.com/watch?v={y})')
            if re.search(r'尚未发布', note_text):
                notes.append('YouTube 官方频道尚未发布本集。')
    if not lines and not notes:
        return ''
    out = ['## 视频链接', '']
    out += lines
    for n in notes:
        out += ['', n]
    out.append('')
    return '\n'.join(out)


def build_one(src_rel, raw, bodies):
    title = re.search(r'^title:\s*(.+)', raw, re.M)
    title = title.group(1).strip() if title else src_rel
    tabs, _ = parse_tabs_zone(raw)
    vlinks = video_links(tabs)
    body = clean_body(raw)

    app = Appendix()
    for _num, b in bodies:
        classify_pr_body(b, app)

    # 脚注锚定：在正文里找“改后文本”，插入 [^N]
    anchored = []
    if app.corr:
        for idx, (b, old, new) in enumerate(app.corr, 1):
            placed = False
            for cand in filter(None, [new]):
                cand_n = norm(cand)
                if len(cand_n) < 2:
                    continue
                # 在去空白文本上定位，再映射回原位置
                body_n = norm(body)
                pos = body_n.find(cand_n)
                if pos >= 0:
                    # 找原文中该片段结束位置
                    acc, ci = 0, 0
                    for ci, ch in enumerate(body):
                        if not ch.isspace():
                            acc += 1
                        if acc == pos + len(cand_n):
                            at = ci + 1
                            body = body[:at] + f'[^{idx}]' + body[at:]
                            placed = True
                            break
                if placed:
                    break
            anchored.append(placed)

    out = [f'# {title}', '']
    if vlinks:
        out.append(vlinks)
    out.append(body)
    if not app.empty():
        out.append('')
        out.append(render_appendix(app))
        # 把脚注编号与 corr 对齐（render 时按同一顺序编号）
    text = '\n'.join(out).rstrip() + '\n'
    return text, app, anchored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only', default=None)
    ap.add_argument('--refresh-prs', action='store_true')
    args = ap.parse_args()

    if args.refresh_prs and PRS_CACHE.exists():
        PRS_CACHE.unlink()

    # integration worktree 就位检查
    r = subprocess.run(['git', 'rev-parse', INTEG_BRANCH],
                       cwd=INTEG, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f'integration worktree 不可用: {r.stderr}')

    pmap = pr_map()
    report_missing_pr, report_no_app = [], []
    count = 0
    for d in SRC_DIRS:
        for f in sorted((INTEG / SECTION / d).glob('*.md')):
            src_rel = f'{SECTION}/{d}/{f.name}'
            tgt = target_for(src_rel)
            if not tgt:
                continue
            if args.only and args.only not in f.name and args.only not in src_rel:
                continue
            folder, name = tgt
            out_dir = OUT / folder
            out_path = out_dir / f'{name}.md'
            if out_path.exists() and not args.force:
                continue
            raw = f.read_text()
            key = pr_key_for(src_rel)
            bodies = pmap.get(key, []) if key is not None else []
            if key is not None and not bodies:
                report_missing_pr.append(src_rel)
            text, app, anchored = build_one(src_rel, raw, bodies)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text)
            if app.empty():
                report_no_app.append(src_rel)
            count += 1
            print(f'  ✓ {folder}/{name}.md  '
                  f'(核对{len(app.checks)} 订正{len(app.corr)} 待核{len(app.pending)}'
                  f' 锚定{sum(anchored)}/{len(anchored)})')
    print(f'\nwritten: {count}')
    if report_missing_pr:
        print('无对应 PR:', *report_missing_pr, sep='\n  ')
    if report_no_app:
        print(f'无附录内容（{len(report_no_app)} 篇）: 首例',
              report_no_app[:5])


if __name__ == '__main__':
    main()
