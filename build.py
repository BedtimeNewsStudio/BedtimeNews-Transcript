#!/usr/bin/env python3
"""
bedtimenews-md builder
把 bedtimenews-archive-contents integration 分支上「多步校对完成」的五个栏目文稿
转换为本仓库的干净 Markdown 版本：
  - 4 位 padding 期号，每 100 期一个文件夹（0001-0100 …）；无期号 → misc/
  - 顶部 B站/YouTube 嵌入改为纯 URL 链接（官方优先，非官方补档标注）
  - 正文只保留纯 Markdown 文本（去图片、font 标签、wiki 类标记、HTML 注释）
  - 末尾附录：取自校对 PR 描述的「联网事实核对及信息来源 / 事实订正 / 待核对」
    （错别字、标点、格式、音频复核过程记录一律不入附录；同音错录直接修复不提及）

Usage:
  python3 build.py                      # 全栏目全量生成（已存在且不强制则跳过）
  python3 build.py --force              # 覆盖重生成
  python3 build.py --section CanKaoXinXi --force
  python3 build.py --only 116 --force   # 只生成文件名含 116 的集
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
OUT = REPO / 'contents'
PRS_CACHE = REPO / '.localonly' / 'prs-all.json'
UPSTREAM = 'bedtimenews/bedtimenews-archive-contents'

# 栏目 → integration 源目录与扫描子目录（dirs=None 表示平铺扫描整个栏目目录）
CONFIGS = {
    'ShuiQianXiaoXi': {
        'src': 'main', 'bsec': 'main',
        'dirs': ['1-100', '101-200', '201-300', '301-400', '401-500', '501-600',
                 '601-700', '801-900', '901-1000', '1001-1100',
                 'prequel', 'summerbreak', 'winterbreak2023'],
    },
    'CanKaoXinXi': {
        'src': 'reference', 'bsec': 'ref',
        'dirs': ['1-100', '101-200', '201-300', '301-400', '401-500',
                 '501-600', '601-700'],
    },
    'ChanJingPoBiJi': {'src': 'business', 'bsec': 'biz', 'dirs': None},
    'GaoJian':        {'src': 'opinion', 'bsec': 'op', 'dirs': None},
    'JiangDianHeiHua': {'src': 'commercial', 'bsec': 'comm', 'dirs': None},
}

# 特殊文件表：(src, subdir|None, basename) → (去向 misc/ 的名字, PR 分支后缀或 None)
SPECIAL = {
    ('main', 'prequel', '0.md'):            ('0', None),
    ('main', 'prequel', 'l1.md'):           ('l1', 'prequel-l1'),
    ('main', 'prequel', 'l2.md'):           ('l2', 'prequel-l2'),
    ('main', 'prequel', 'l3.md'):           ('l3', 'prequel-l3'),
    ('main', 'prequel', 'l4.md'):           ('l4', 'prequel-l4'),
    ('main', 'prequel', 'l5.md'):           ('l5', 'prequel-l5'),
    ('main', 'prequel', 'l6.md'):           ('l6', 'prequel-l6'),
    ('main', 'summerbreak', '1.md'):        ('summerbreak-1', 'summerbreak-1'),
    ('main', 'summerbreak', '2.md'):        ('summerbreak-2', 'summerbreak-2'),
    ('main', 'winterbreak2023', '1.md'):    ('winterbreak2023-1', 'winterbreak2023-1'),
    ('main', 'winterbreak2023', '2.md'):    ('winterbreak2023-2', 'wb2023-2'),
    ('main', 'winterbreak2023', '3.md'):    ('winterbreak2023-3', 'wb2023-3'),
    ('main', 'winterbreak2023', '4.md'):    ('winterbreak2023-4', 'wb2023-4'),
    ('main', 'winterbreak2023', '5.md'):    ('winterbreak2023-5', 'wb2023-5'),
    ('main', 'winterbreak2023', '6.md'):    ('winterbreak2023-6', 'winterbreak2023-6'),
    ('main', 'winterbreak2023', 'essay.md'): ('winterbreak2023-essay', 'winterbreak2023-essay'),
    ('main', 'winterbreak2023', 'speech2023.md'): ('speech2023', 'speech2023'),
    ('main', '1-100', 'speech2019.md'):     ('speech2019', 'speech2019'),
    ('main', '501-600', 'thewordof2022.md'): ('thewordof2022', 'thewordof2022'),
    ('main', '601-700', 'thewordof2023.md'): ('thewordof2023', 'thewordof2023'),
    ('main', '901-1000', '2025interview-1.md'): ('2025interview-1', '2025interview-1'),
    ('main', '901-1000', '2025interview-2.md'): ('2025interview-2', '2025interview-2'),
    ('biz', None, 'yu7.md'):           ('yu7', 'biz-yu7'),
    ('biz', None, '-1.md'):            ('biz-001', 'biz-neg1'),
    ('biz', None, '-2.md'):            ('biz-002', 'biz-neg2'),
}

# ---------------------------------------------------------------- source side

RE_TAB_HEAD = re.compile(r'^#{1,3}\s*(Tabs|B站|YouTube|西瓜视频|微博|播客)(\s*\{[^}]*\})?\s*$')

def fm_end(lines):
    """frontmatter 结束行号（容错双块 frontmatter，如 937 期）。"""
    k = 0
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                k = i + 1
                break
    while k < len(lines):
        s = lines[k].strip()
        if s == '---' or re.match(r'^[A-Za-z][\w-]*:(\s|$)', s):
            k += 1
            continue
        break
    return k


def zone_start(lines):
    """frontmatter 后，跳过空行/前置说明（blockquote、class 行），第一个候选行号。"""
    k = fm_end(lines)
    while k < len(lines):
        s = lines[k].strip()
        if not s or s.startswith('>') or s.startswith('{.is-'):
            k += 1
            continue
        break
    return k if k < len(lines) else None


def parse_tabs_zone(raw):
    """返回 (各 tab 的 (label, notes, bvids, ytid), 是否有嵌入区)。"""
    stripped = re.sub(r'<!--.*?-->', '', raw, flags=re.S)
    lines = stripped.split('\n')
    i = zone_start(lines)
    if i is None or not RE_TAB_HEAD.match(lines[i].strip()):
        return [], False
    tabs = []           # (label, [note lines])
    cur = None
    for l in lines[i:]:
        s = l.strip()
        if not s or re.match(r'^#\s*$', s):
            continue
        if s.startswith('## ') or s.startswith('### '):
            label = s.lstrip('#').strip()
            if not label:
                continue
            cur = (label, [])
            tabs.append(cur)
            continue
        if cur is None:
            continue
        if s.startswith(('<div', '<iframe', '</div', '{.is-')):
            continue
        if s.startswith('>'):
            cur[1].append(s.lstrip('> ').strip())
            continue
        break  # 正文开始
    # 逐 tab 提取 iframe（在 stripped 文本上按 label 定位）
    def _find(needle, frm):
        try:
            return stripped.index(needle, frm)
        except ValueError:
            return len(stripped)

    result = []
    pos = 0
    for idx, (label, notes) in enumerate(tabs):
        start = min(_find(f'## {label}', pos), _find(f'### {label}', pos))
        if idx + 1 < len(tabs):
            nxt = tabs[idx + 1][0]
            end = min(_find(f'## {nxt}', start), _find(f'### {nxt}', start))
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
    lines = raw.split('\n')
    k = fm_end(lines)
    zone, has_tabs = parse_tabs_zone(raw)
    if has_tabs:
        j = zone_start(lines)
        while j < len(lines):
            s = lines[j].strip()
            if not s or s.startswith(('## ', '### ', '<div', '<iframe', '</div', '>', '{.is-')) \
                    or re.match(r'^#\s*$', s) or RE_TAB_HEAD.match(s):
                j += 1
                continue
            break
        lines = lines[:k] + lines[j:]
    else:
        lines = lines[k:]
    raw = '\n'.join(lines)
    # 正文清理
    raw = re.sub(r'!\[(?:[^\]\\]|\\.)*\]\([^)]+\)', '', raw)        # 图片（容忍转义括号）
    raw = re.sub(r'<font[^>]*>|</font>', '', raw)                  # 字体标签
    raw = re.sub(r'\s*\{\.is-[a-z]+\}', '', raw)                   # wiki class
    raw = re.sub(r'\s*\{\.(tabset|links-list|tab)\}', '', raw)
    raw = re.sub(r'<(https?://[^>\s]+)>', r'\1', raw)              # 尖括号链接
    raw = re.sub(r'^> ?以下文本为音频转录结果.*$', '', raw, flags=re.M)  # 整理状态警示
    # 正文中残留的嵌入播放器 → 纯 URL
    def iframe_url(m):
        src = m.group(1)
        bm = re.search(r'bvid=([A-Za-z0-9]+)', src)
        if bm and bm.group(1) != 'BVID':
            return f'https://www.bilibili.com/video/{bm.group(1)}'
        ym = re.search(r'youtube[^/]*\.com/embed/([A-Za-z0-9_-]+)', src)
        if ym and ym.group(1) != 'YouTubeVID':
            return f'https://www.youtube.com/watch?v={ym.group(1)}'
        return ''  # 占位符与第三方嵌入（西瓜/微博等）不留 URL
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
    # 全局清理残留的嵌入区标题行（如 995 期正文中段的第二视频 Tabs 块）
    raw = re.sub(r'^#{1,3}\s*(Tabs|B站|YouTube|西瓜视频|微博|播客)(\s*\{[^}]*\})?\s*\n', '', raw, flags=re.M)
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
RE_BOILER_TAIL = re.compile(
    r'B站/?YouTube iframe|YouTube嵌入地址|嵌入地址中的|本页视频区|留待管理员补充|占位符未?动')
RE_NOCHANGE = re.compile(r'无[^。；]{0,14}需?订正|无需?修正|无日期.{0,8}错误|未发现.{0,8}(错误|订正)')
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
        if has_url or re.search(r'核实|与.*一致|互证|有据', meta):
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


def render_appendix(app, refs=None, p3_resolved=()):
    out = []
    out += ['## 附录', '']
    def _strip_proc(t):
        t = re.sub(r'音频[^，。；）]*?(?:无法定案|不能定案)[，。；）]?\s*', '', t)
        t = re.sub(r'维持(?:原文|原稿|口播)[。]?', '', t)
        t = re.sub(r'[，,]?\s*未改[动。]?', '', t)
        return t.strip()
    if app.checks:
        out += ['### 信息来源', '']
        out += [f'- {_strip_proc(retitle_bullets(c))}' for c in app.checks]
        out.append('')
    if refs:
        out += ['### 本文链接', '']
        seen = set()
        for title, url in refs.values():
            k = norm(url)
            if k in seen:
                continue
            seen.add(k)
            out.append(f'- [{title}]({url})')
        out.append('')
    if app.corr:
        out += ['### 事实订正', '']
        for idx, (b, _old, _new) in enumerate(app.corr, 1):
            text = retitle_bullets(re.sub(r'\s+', ' ', b).strip())
            out.append(f'[^{idx}]: {text}')
        out.append('')
    if p3_resolved:
        out += ['### 已核对', '']
        for r in p3_resolved:
            line = retitle_bullets(r)
            line = re.sub(r'^-\s*', '', line)
            line = re.sub(r'^.*?(?:→\s*\*\*已核实\*\*：|已核实：)', '', line)
            line = re.sub(r'核实有误：', '', line)
            line = re.sub(r'[（(][^（）()]*上游[^（）()]*[）)]', '', line)
            line = strip_upstream_refs(line)
            line = re.sub(r'[，,]\s*未改[动]。?$', '。', line)
            line = re.sub(r'[，,]?\s*已在原稿订正[^，。；）]*', '', line)
            line = re.sub(r'\[([^\[]+)\]\(\[([^\]]+)\]\(([^)]+)\)\)', r'[\2](\3)', line)
            out.append('- ' + line.strip())
        out.append('')
    if app.pending:
        out += ['### 待核对', '']
        out += [f'- {_strip_proc(retitle_bullets(p))}' for p in app.pending]
        out.append('')
    txt = '\n'.join(out)
    txt = strip_upstream_refs(txt)
    txt = fold_links(txt)
    return txt


# ------------------------------------------------- 正文转换（链接/分段/标题）

DOMAIN_TITLES = {
    'zhihu.com': '知乎', 'wikipedia.org': '维基百科', 'thepaper.cn': '澎湃新闻',
    'people.com.cn': '人民网', 'xinhuanet.com': '新华网', 'news.cn': '新华网',
    'cctv.com': '央视网', 'guancha.cn': '观察者网', 'jiemian.com': '界面新闻',
    'caixin.com': '财新网', 'yicai.com': '第一财经', '21jingji.com': '21世纪经济报道',
    'nbd.com.cn': '每日经济新闻', 'cb.com.cn': '中国经营报', 'gov.cn': '中国政府网',
    'mee.gov.cn': '生态环境部', 'mof.gov.cn': '财政部', 'stats.gov.cn': '国家统计局',
    'sasac.gov.cn': '国资委', 'nhc.gov.cn': '国家卫健委', 'court.gov.cn': '最高人民法院',
    'cnbc.com': 'CNBC', 'reuters.com': '路透社', 'bbc.com': 'BBC', 'nytimes.com': '纽约时报',
    'nature.com': 'Nature', 'science.org': 'Science', 'doi.gov': '美国内政部',
    'singstat.gov.sg': '新加坡统计局', 'mongabay.com': 'Mongabay', 'spgchinaratings.cn': '标普信评',
    'issafrica.org': '非洲安全研究所', 'weibo.com': '微博', 'weibo.cn': '微博',
    'bilibili.com': '哔哩哔哩', 'youtube.com': 'YouTube', 'github.com': 'GitHub',
    'news.sina.com.cn': '新浪新闻', 'finance.sina.com.cn': '新浪财经', 'ifeng.com': '凤凰网',
    'huanqiu.com': '环球网', 'cyol.com': '中国青年报', 'gmw.cn': '光明网',
    'workercn.cn': '工人日报', 'chinanews.com': '中国新闻网', 'ce.cn': '中国经济网',
}
TITLE_PLANS = None
USTAT = None  # urlstatus 由 main() 载入
BEDTIME_RE = re.compile(r'https?://[^\s)」』】]*(?:bedtime\.news|archive\.bedtime\.news)[^\s)」』】]*')
WIKI_LINK_RE = re.compile(r'\[([^\]]+)\]\((?:/[^)]*|[^)]*\.md|https?://[^)]*bedtime\.news[^)]*)\)')
EXT_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')
BARE_URL_RE = re.compile(r'(?<![\w.])https?://[^\s）)」』】。，；、]+')
HEAD_NUM_RE = re.compile(r'^(#{2,3})\s*(?:\d{1,3}\s*[.、:：]\s*|[一二三四五六七八九十]{1,3}\s*[、.:：]\s*|第\s*[0-9一二三四五六七八九十百]{1,4}\s*[节章部分集]\s*[、.:：]?\s*)(.+?)\s*$', re.M)


def link_title(url, anchor=''):
    if not anchor:
        st = USTAT.get(url) if USTAT else None
        if st and st.get('title'):
            t = re.sub(r'\s*[|\-–].*$', '', st['title']).strip()
            if t and not t.lower().startswith(('http', 'www.')):
                return t[:40]
    if anchor:
        a = anchor.strip()
        if a and not a.lower().startswith(('http', 'www.')):
            return a[:40]
    m = re.match(r'https?://([^/]+)', url)
    dom = m.group(1).lower().removeprefix('www.') if m else url[:30]
    for k, v in DOMAIN_TITLES.items():
        if dom == k or dom.endswith('.' + k) or dom.endswith(k):
            return v
    return dom[:30]


RE_UPSTREAM_REF = re.compile(
    r'[（(][^（）()]*?(?:上游|提交上游|已提交上游)\s*PR[^（）()]*?[）)]'
    r'|上游\s*PR[^，。；）)\s]*|(?<=[（(，、])PR[#：]?\d{2,4}(?=[）)，。])'
    r'|bedtimenews-archive-contents#\d+')
# 短语级上游引用改写：先于通用删除执行，保持句子可读
RE_UP_PHRASES = [
    (re.compile(r'已在上游订正(?:正文)?（[^）]*PR[^）]*）'), '已订正'),
    (re.compile(r'已在上游订正(?:正文)?'), '已订正'),
    (re.compile(r'已在\s*PR\s*#?\d+\s*分支订正'), '已订正'),
    (re.compile(r'勘误见\s*PR\s*#?\d+\s*评论\s*[；;]?'), ''),
    (re.compile(r'[（(]PR\s*#?\d+\s*追加\s*commit[^）]*[）)]'), ''),
    (re.compile(r'[（(]新\s*PR\s*#?\d+[^）]*[）)]'), ''),
    (re.compile(r'上游\s*[A-Za-z]+/\S+?\.md'), ''),
    (re.compile(r'追加\s*commit'), ''),
    # 上游仓库 GitHub 链接整链删除（含显示名）；PR 编号兜底删除（字母边界防 GPR75 类误伤）
    (re.compile(r'\[[^\]]*\]\(https?://[^\s)]*(?:github\.com/bedtimenews|bedtimenews-archive-contents)[^\s)]*\)'), ''),
    (re.compile(r'\[[^\]]*\]\([^()]*bedtimenews-archive-contents[^()]*\)'), ''),
    (re.compile(r'库内\s*(?:main|reference|business|commercial|opinion)/\S+?\.md'), ''),
    (re.compile(r'上游仓库（bedtimenews-archive-contents）'), '源库'),
    (re.compile(r'bedtimenews-archive-contents'), ''),
    (re.compile(r'(?<![A-Za-z])PR\s*#?\s*\d{2,4}'), ''),
]


def _tidy_punct(t):
    t = re.sub(r'（\s*）', '', t)
    t = re.sub(r'\(\s*\)', '', t)
    t = re.sub(r'[[ \t]+([，。；）])', r'\1', t)
    t = re.sub(r'([，。；])\s*([，。；])+', r'\1', t)
    t = re.sub(r'。\s*。+', '。', t)
    return t


def _match_link_at(t, i):
    """从 t[i]=='[' 起解析一个 markdown 链接，返回 (end, display, target)；失败返回 None。
    display 允许一层方括号嵌套；target 允许平衡括号（URL 带括号不炸）。"""
    n = len(t)
    j = i + 1
    depth = 1
    while j < n and depth:
        if t[j] == '[':
            depth += 1
        elif t[j] == ']':
            depth -= 1
        j += 1
    if depth or j >= n or t[j] != '(':
        return None
    disp = t[i + 1:j - 1]
    k = j + 1
    d = 1
    while k < n and d:
        if t[k] == '(':
            d += 1
        elif t[k] == ')':
            d -= 1
        k += 1
    if d:
        return None
    return k, disp, t[j + 1:k - 1].strip()


RE_FB_FOLD = re.compile(r'\[([^\]]+)\]\(\s*\[([^\]]+)\]\(([^)\s]+)\)[^)]*\)')


def fold_links(text, passes=4):
    """嵌套链接折叠至不动点（扫描器实现）：[X]([Y](url))→[X](url)（target 尾部
    仅剩标点杂字符也折叠）；脚注目标链接 []([^refN])→[^refN]；空锚链接删除。"""
    for _ in range(passes):
        changed = False
        out = []
        i, n = 0, len(text)
        while i < n:
            if text[i] == '[':
                m = _match_link_at(text, i)
                if not m:
                    # 容错回退：target 括号不平衡（标题含半角括号等残缺形态）
                    fb = RE_FB_FOLD.match(text, i)
                    if fb:
                        out.append(f'[{fb.group(1)}]({fb.group(3)})')
                        changed = True
                        i = fb.end()
                        continue
                if m:
                    end, disp, target = m
                    inner = (re.match(r'\[([\s\S]*)\]\(([\s\S]*)\)', target)
                             if target.startswith('[') else None)
                    if inner and not _match_link_at(inner.group(2), 0) \
                            and not re.search(r'[\[\]]', inner.group(2)):
                        out.append(f'[{disp}]({inner.group(2)})')
                        changed = True
                        i = end
                        continue
                    if re.fullmatch(r'\[\^(?:ref)?\d+\][ \t]?', target):
                        out.append((disp if disp.strip() else '') + target.strip())
                        changed = True
                        i = end
                        continue
                    if not disp.strip():
                        out.append('')
                        changed = True
                        i = end
                        continue
            out.append(text[i])
            i += 1
        text = ''.join(out)
        if not changed:
            break
    return text


def strip_upstream_refs(text):
    """删除附录中对上游 PR/仓库的引用（干净稿 standalone）。"""
    t = text
    for pat, rep in RE_UP_PHRASES:
        t = pat.sub(rep, t)
    t = RE_UPSTREAM_REF.sub('', t)
    t = fold_links(t)
    return _tidy_punct(t)


def render_link(url, anchor=''):
    """按 urlstatus 渲染：失效保留并标注（已失效），存活用页面标题。"""
    url = url.rstrip('.,;、')
    st = USTAT.get(url) if USTAT else None
    if st and st.get('status') == 'dead':
        m = re.match(r'https?://([^/]+)', url)
        dom = m.group(1).removeprefix('www.') if m else url[:30]
        return f'[{dom}（已失效）]({url})'
    return f'[{link_title(url, anchor)}]({url})'


def collect_refs_and_strip(body, refs):
    """正文：内链/bedtime.news 链接移除（留文字）；行中 URL → 脚注标记 [^refN]；
    独立成行的 URL → 移出正文登记到 refs（本文链接）。返回 (body, 脚注定义列表)。"""
    def wiki_sub(m):
        return m.group(1)
    body = WIKI_LINK_RE.sub(wiki_sub, body)
    body = BEDTIME_RE.sub('', body)

    refnotes = []
    counter = [0]

    def make_note(anchor, url):
        counter[0] += 1
        label = f'ref{counter[0]}'
        refnotes.append(f'[^{label}]: {render_link(url, anchor)}')
        return f'[^{label}]'

    out_lines = []
    for line in body.split('\n'):
        stripped = line.strip()
        # 独立 URL 行：若上一输出段是正文段落 → 脚注锚到该段末；否则悬空进「本文链接」
        if re.fullmatch(r'https?://[^\s）)」』】。，；、]+', stripped):
            j = len(out_lines) - 1
            while j >= 0 and not out_lines[j].strip():
                j -= 1
            if j >= 0 and not out_lines[j].strip().startswith(('#', '>', '- ', 'http')) \
                    and re.search(r'[。！？：；]', out_lines[j]):
                out_lines[j] = out_lines[j].rstrip() + make_note('', stripped)
                continue
            refs[norm(stripped)] = (render_link(stripped), stripped)
            continue
        # 行中 markdown 外链 [text](url) → 脚注
        def ext_sub(m):
            anchor, url = m.group(1).strip(), m.group(2)
            return make_note(anchor, url)
        line = EXT_LINK_RE.sub(ext_sub, line)
        # 行中裸 URL → 脚注
        def bare_sub(m):
            url = m.group(0).rstrip('.,;、')
            return make_note('', url)
        line = BARE_URL_RE.sub(bare_sub, line)
        out_lines.append(line)
    body = '\n'.join(out_lines)
    body = re.sub(r'\n{3,}', '\n\n', body)
    return body.strip(), refnotes


def _is_protect_line(l):
    return (not l.strip() or l.strip().startswith(('#', '>', '- ', '- ', 'http', '《')
            ) or re.match(r'^-{3,}$', l.strip()))


def _reflow_block(b, cap=380):
    """字幕式块：连续短行合并为段落。句子末尾（。！？；）处优先断段。"""
    lines = [l.strip() for l in b.split('\n') if l.strip()]
    if len(lines) < 2:
        return b
    out, buf = [], ''
    for l in lines:
        if _is_protect_line(l):
            if buf:
                out.append(buf)
                buf = ''
            out.append(l)
            continue
        if buf and ((len(buf) + len(l) > cap and buf[-1] in '。！？；；！')
                    or len(buf) > cap + 120):
            out.append(buf)
            buf = l
        else:
            buf += l
    if buf:
        out.append(buf)
    return '\n\n'.join(out)


def merge_short_paragraphs(body, cap=380):
    """散文组重新分段：连续的纯文本块（含单句成段与字幕式碎行）合并后
    按句界切分为 ≤cap 字的段落；标题/引用/列表/URL 行与问句尾块保留边界。"""
    def is_protect(l):
        return (not l.strip() or l.strip().startswith(('#', '>', '- ', 'http'))
                or re.match(r'^-{3,}$', l.strip()))

    def para_split(text):
        out, buf = [], ''
        for sent in re.split(r'(?<=[。！？])', text):
            s2 = sent.strip()
            if not s2:
                continue
            if buf and len(buf) + len(s2) > cap:
                out.append(buf)
                buf = s2
            else:
                buf += s2
        if buf:
            out.append(buf)
        return out

    blocks = re.split(r'\n\n+', body)
    out, group = [], []

    def flush_group():
        nonlocal group
        if not group:
            return
        joined = ''.join(group)
        for p in para_split(joined):
            out.append(p)
        group.clear()

    for blk in blocks:
        lines = [l for l in blk.split('\n') if l.strip()]
        if len(lines) == 1:
            l = lines[0].strip() if lines else ''
            if (not is_protect(l) and not l.endswith('？')
                    and not re.match(r'^-{3,}$', l)):
                group.append(l)
                continue
        flush_group()
        if len(lines) >= 3 and all(not is_protect(l) for l in lines) \
                and not blk.startswith(('#', '>')):
            for p in para_split(''.join(l.strip() for l in lines)):
                out.append(p)
        else:
            out.append(blk)
    flush_group()
    return '\n\n'.join(out)


def denumber_headings(body):
    return HEAD_NUM_RE.sub(lambda m: f'{m.group(1)} {m.group(2)}', body)


def retitle_bullets(text):
    """附录条目里的裸 URL → 带标题的 markdown 链接；bedtime.news 链接删除。"""
    text = BEDTIME_RE.sub('', text)
    def sub(m):
        url = m.group(0).rstrip('.,;、)')
        return f'[{link_title(url)}]({url})'
    return BARE_URL_RE.sub(sub, text)

# ---------------------------------------------------------------- PR mapping

SEC_PFX = (('ref-', 'ref'), ('biz-', 'biz'), ('comm-', 'comm'), ('op-', 'op'))


def branch_key(x):
    """分支后缀 → (栏目src, key)。key 为 int 期号或字符串（N-M/neg1/yu7/prequel-l1…）。"""
    if x.startswith('transcribe-'):
        x = x[len('transcribe-'):]
    sec = 'main'
    for pfx, s in SEC_PFX:
        if x.startswith(pfx):
            sec, x = s, x[len(pfx):]
            break
    dm = re.match(r'^(\d+)([bc]|-retry)?$', x)
    if dm:
        return (sec, int(dm.group(1)))
    return (sec, x)


COMMIT_SKIP_HEAD = re.compile(r'组段|重排|格式规范|格式统一|同步|tip对齐|对齐|合并|回退|字体|font|链接清理')


def harvest_commits(force=False):
    """遍历 fork 全部 proofread 分支，收集 每个文稿文件 的 commit 信息全文。"""
    cache = REPO / '.localonly' / 'commit-msgs.json'
    if cache.exists() and not force:
        return json.loads(cache.read_text())
    refs = subprocess.run(
        ['git', 'for-each-ref', '--format=%(refname)', 'refs/remotes/origin-pr/proofread/'],
        cwd=INTEG, capture_output=True, text=True, check=True).stdout.split()
    print(f'harvesting commit messages from {len(refs)} branches …')
    r = subprocess.run(
        ['git', 'log', '--no-merges', '--format=%x00%B%x01', '--name-only'] + refs,
        cwd=INTEG, capture_output=True, text=True)
    data = {}
    for blk in r.stdout.split('\x00')[1:]:
        body, _, rest = blk.partition('\x01')
        body = body.strip()
        subj = body.split('\n')[0].strip()
        if not body or COMMIT_SKIP_HEAD.search(subj):
            continue
        for f in (l.strip() for l in rest.split('\n')):
            if f.endswith('.md') and '/' in f:
                data.setdefault(f, []).append(body)
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False))
    print(f'  commit messages for {len(data)} files')
    return data


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
    """(src栏目, key) → 按 PR 号排序的 body 列表。"""
    prs = load_prs()
    m = {}
    for p in prs:
        b = p['headRefName']
        if not b.startswith('proofread/') or b.startswith('proofread/ref-') and False:
            continue
        x = b.split('/', 1)[1]
        if x.startswith('ref') and not x.startswith('ref-'):
            continue  # 旧 ref 整理类分支，无对应栏目文件
        key = branch_key(x)
        m.setdefault(key, []).append((p['number'], p['body'] or ''))
    for v in m.values():
        v.sort()
    return m

# ---------------------------------------------------------------- routing

def num_folder(n):
    lo = max(((n - 1) // 100) * 100 + 1, 1)
    return f'{lo:04d}-{lo + 99:04d}'


def resolve(cfg_name, cfg, subdir, base, title):
    """→ (folder, filename, pr_key)。pr_key 为 branch_key 元组或 None。"""
    bsec = cfg['bsec']
    sp = SPECIAL.get((bsec, subdir, base))
    if sp:
        return ('misc', sp[0], branch_key(sp[1]) if sp[1] else None)
    m = re.match(r'^(\d+)\.md$', base)
    if m:
        n = int(m.group(1))
        return (num_folder(n), f'{n:04d}', (bsec, n))
    m = re.match(r'^(\d+)-(\d+)\.md$', base)
    if m:
        n, sub = int(m.group(1)), m.group(2)
        # 标题里有 N.5 → 番外命名；否则保留 N-M（重号期）
        name = f'{n:04d}.5' if re.search(rf'{n}\.5', title or '') else f'{n:04d}-{sub}'
        return (num_folder(n), name, (bsec, f'{n}-{sub}'))
    return None


def video_links(tabs, linkstatus=None, epnum=None):
    lines, notes = [], []
    had_yt = False
    for label, tnotes, bvids, yts in tabs:
        note_text = ' '.join(tnotes)
        if label == 'B站':
            if bvids:
                tag = '（非官方补档）' if re.search(r'补档|其它用户上传|其他用户上传', note_text) else ''
                for bv in bvids:
                    st = (linkstatus or {}).get('bili', {}).get(bv, {}).get('status')
                    if st == 'dead':
                        notes.append('B站视频已失效。')
                        lines.append(f'- [Bilibili（已失效）](https://www.bilibili.com/video/{bv})')
                        continue
                    lines.append(f'- [Bilibili{tag}](https://www.bilibili.com/video/{bv})')
            if re.search(r'已被删除|未找到原视频', note_text):
                notes.append('B站官方视频已删除。')
        elif label == 'YouTube':
            for y in yts:
                if re.search(r'尚未发布|此处不适用|^无$', note_text):
                    continue
                st = (linkstatus or {}).get('yt', {}).get(y, {})
                if st.get('status') == 'dead':
                    lines.append(f'- [YouTube（已失效）](https://www.youtube.com/watch?v={y})')
                    continue
                had_yt = True
                # 有已校验作者时以作者为准（观视频工作室等早期官方渠道，上游"补档"注记已过时）
                if st.get('author'):
                    tag = '' if st.get('official') else '（非官方补档）'
                else:
                    tag = '（非官方补档）' if re.search(r'补档|其它用户上传|其他用户上传', note_text) else ''
                lines.append(f'- [YouTube{tag}](https://www.youtube.com/watch?v={y})')
            if re.search(r'尚未发布', note_text):
                notes.append('YouTube 官方频道尚未发布本集。')
    if epnum is not None and not had_yt:
        disc = (linkstatus or {}).get('discover', {})
        pick = disc.get(epnum) or disc.get(str(epnum))
        if pick:
            tag = '' if pick.get('official') else '（非官方补档）'
            lines.append(f'- [YouTube{tag}](https://www.youtube.com/watch?v={pick["ytid"]})')
    if not lines and not notes:
        return ''
    out = []
    out += lines
    for n in notes:
        out += ['', n]
    out.append('')
    return '\n'.join(out)


def p4_overlay(cfg_name, folder, name, body):
    """P4 重排版覆盖层（.localonly/p4-reflow/）：上游源文本无句读的期，由代理恢复
    标点+分段后存覆盖层；构建时替换正文。仅当内容等价（忽略空白与标点后逐字一致）
    才生效，防止覆盖层与上游内容漂移。"""
    p = REPO / '.localonly' / 'p4-reflow' / cfg_name / (folder or 'misc') / f'{name}.md'
    if not p.exists():
        return body
    ov = p.read_text().strip()
    if not ov:
        return body
    key = lambda s: re.sub(r'[\W\s]', '', re.sub(r'^#{1,6}[^\n]*$', '', s, flags=re.M))
    if key(ov) != key(body):
        print(f'  [p4] 覆盖层与正文内容不一致，跳过: {cfg_name}/{folder or "misc"}/{name}')
        return body
    return ov


def build_one(raw, bodies, commit_msgs=(), linkstatus=None, epnum=None, cfg_name=None, folder=None, name=None):
    title = re.search(r'^title:\s*(.+)', raw, re.M)
    title = title.group(1).strip() if title else ''
    tabs, _ = parse_tabs_zone(raw)
    vlinks = video_links(tabs, linkstatus, epnum)
    body = clean_body(raw)
    cfg_name = cfg_name  # noqa

    app = Appendix()
    for _num, b in bodies:
        classify_pr_body(b, app)
    for cm in commit_msgs:  # commit 信息里的验证/勘误
        classify_pr_body(cm, app)


    # 微博转发链等非正文段落删除
    body = re.sub(r'^.*?转发动态[：:].*\n?', '', body, flags=re.M)
    body = re.sub(r'^.*//@[^：:]{1,30}[：:].*\n?', '', body, flags=re.M)

    # P3 联网核对结果：resolved 条目 → 已核对小节，并从待核对中移除对应项
    p3 = P3_RESULTS.get((cfg_name, folder, name)) if P3_RESULTS else None
    p3_resolved = []
    if p3:
        for r in p3.get('resolved', []):
            item = re.sub(r'\s+', '', r.get('item', ''))
            concl = re.sub(r'\s+', ' ', r.get('conclusion', '')).strip()
            src = r.get('source', '')
            src_part = f'（来源 [link_title]({src})）' if src else ''
            src_part = src_part.replace('link_title', link_title(src))
            p3_resolved.append(f'- {r.get("item", "").strip()} → **已核实**：{concl}{src_part}')
        def is_resolved(b):
            nb = re.sub(r'^[-*]\s*', '', b.strip())
            nb = re.sub(r'\s+', '', nb)
            return any(nb.startswith(norm(r.get('item', ''))[:20]) for r in p3.get('resolved', []))
        app.pending = [p for p in app.pending if not is_resolved(p)]

    refs = {}
    body, refnotes = collect_refs_and_strip(body, refs)  # 内链移除；行中URL→脚注；独立URL→本文链接

    # 日期行 → 摘要（有话题列表才提取；纯日期行保留）
    summary = ''
    blocks = re.split(r'\n\n+', body)
    for bi, blk in enumerate(blocks):
        m = re.match(r'^睡前消息\s*(?:\d{1,4}|本期话题|今日话题)?\s*[：:]?\s*(.{15,})$', blk.strip())
        if m and '；' in m.group(1):
            summary = f'{m.group(1).strip()}'
            blocks.pop(bi)
            break
    body = '\n\n'.join(blocks)

    # 正文开头的孤立书名号行（镜像频道标题，如41期《存款不到50万…》）删除——
    # 必须在转发链/日期行移除之后执行，否则它不是首块
    blocks = re.split(r'\n\n+', body)
    while blocks and re.match(r'^-{3,}$', blocks[0].strip()):
        blocks.pop(0)
    body = '\n\n'.join(blocks)
    # 全文级：纯书名号独立块（镜像频道标题）与剧照图注残留删除
    body = re.sub(r'^《[^》]{1,80}》\s*$', '', body, flags=re.M)
    body = re.sub(r'^《[^》]+》剧照\s*$', '', body, flags=re.M)
    body = merge_short_paragraphs(body)            # 字幕式/单句小段合并
    body = denumber_headings(body)                 # 小节标题去编号

    def apply_titles(tbl):
        nonlocal body
        for item in tbl:
            t = (item.get('title') or '').strip()
            anchor = norm(item.get('anchor', ''))
            if not t or len(anchor) < 8:
                continue
            if re.search(rf'^## {re.escape(t)}\s*$', body, re.M):
                continue  # 幂等：标题已存在
            key = anchor[:20]
            bn = norm(body)
            pos = bn.find(key)
            if pos < 0:
                key = anchor[:12]
                pos = bn.find(key)
            if pos < 0:
                continue  # 锚点在成稿中不存在，丢弃
            # 块级定位：norm 空间逐块累计（分隔符为纯空白，norm 长度 0）
            segs = re.split(r'(\n\n+)', body)
            spans = []  # (si, norm_start, norm_end)
            c = 0
            for si, seg in enumerate(segs):
                if re.fullmatch(r'\n+', seg or ''):
                    continue
                sn = norm(seg)
                spans.append((si, c, c + len(sn)))
                c += len(sn)
            hit = None
            for si, s0, s1 in spans:
                if s0 <= pos < s1 or si == spans[-1][0]:
                    hit = (si, s0, s1)
                    break
            if hit is None:
                continue
            si, s0, s1 = hit
            seg = segs[si]
            if seg.strip().startswith('#'):
                # 落在标题行块：插到该块之后、下一个内容块之前
                if si + 2 < len(segs):
                    segs[si + 2] = f'## {t}\n\n' + segs[si + 2]
                    body = ''.join(segs)
                continue
            if pos <= s0:
                segs[si] = f'## {t}\n\n' + seg
                body = ''.join(segs)
                continue
            # 块内 raw 切点：块内局部 norm 走到 pos - s0
            target = pos - s0
            acc = 0
            cut = None
            for cj, ch in enumerate(seg):
                if not ch.isspace():
                    if acc == target:
                        cut = cj
                        break
                    acc += 1
            if cut is None or cut == 0:
                segs[si] = f'## {t}\n\n' + seg
            else:
                segs[si] = seg[:cut].rstrip() + f'\n\n## {t}\n\n' + seg[cut:].lstrip()
            body = ''.join(segs)

    # 脚注锚定：在正文里找“改后文本”，插入 [^N]
    anchored = []
    if app.corr:
        for idx, (b, old, new) in enumerate(app.corr, 1):
            placed = False
            for cand in filter(None, [new]):
                cand_n = norm(cand)
                if len(cand_n) < 2:
                    continue
                body_n = norm(body)
                pos = body_n.find(cand_n)
                if pos >= 0:
                    acc = 0
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

    body = fold_links(body)
    body = re.sub(r'\]\(\[\^(?:ref)?\d+\]\)', '', body)    # 孤儿脚注链接残段
    body = re.sub(r'^#{1,6}\s*\n', '', body, flags=re.M)    # 空标题行
    body = p4_overlay(cfg_name, folder, name, body)

    # 标题插入放在 P4 覆盖层之后：锚点按成稿（含覆盖层）段落写，幂等护栏防重复
    plan = TITLE_PLANS.get((cfg_name, folder, name)) if TITLE_PLANS else None
    if p3 and p3.get('titles'):
        plan = (plan or []) + p3['titles']
    if plan:
        apply_titles(plan)

    out = [f'# {title}', '']
    if vlinks:
        out += ['## 视频', '', vlinks]
    if summary:
        out += ['## 摘要', '', summary, '']
    out += ['## 正文', '']
    out.append(re.sub(r'^(?:-{3,}\n+)+', '', body))
    if not app.empty() or refs or refnotes or p3_resolved:
        out.append('')
        out.append(render_appendix(app, refs, p3_resolved))
    if refnotes:
        out.append('')
        out += refnotes
    text = '\n'.join(out).rstrip() + '\n'
    return text, app, anchored

# ---------------------------------------------------------------- build

def iter_source_files(cfg):
    src, dirs = cfg['src'], cfg['dirs']
    base = INTEG / src
    if dirs is None:
        for f in sorted(base.glob('*.md')):
            yield (None, f)
    else:
        for d in dirs:
            for f in sorted((base / d).glob('*.md')):
                yield (d, f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only', default=None)
    ap.add_argument('--section', default=None, help='ShuiQianXiaoXi/CanKaoXinXi/…，默认全部')
    ap.add_argument('--refresh-prs', action='store_true')
    args = ap.parse_args()

    if args.refresh_prs:
        if PRS_CACHE.exists():
            PRS_CACHE.unlink()
        cmc = REPO / '.localonly' / 'commit-msgs.json'
        if cmc.exists():
            cmc.unlink()

    r = subprocess.run(['git', 'rev-parse', INTEG_BRANCH],
                       cwd=INTEG, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f'integration worktree 不可用: {r.stderr}')

    pmap = pr_map()
    cmap = harvest_commits(force=args.refresh_prs)
    ls_path = REPO / '.localonly' / 'linkstatus.json'
    linkstatus = json.loads(ls_path.read_text()) if ls_path.exists() else None
    tp_dir = REPO / '.localonly' / 'section-titles'
    tp = {}
    if tp_dir.exists():
        for p in tp_dir.rglob('*.json'):
            parts = p.relative_to(tp_dir).with_suffix('').parts
            try:
                tp[tuple(parts)] = json.loads(p.read_text())
            except Exception:
                pass  # 并发写盘容错
    globals()['TITLE_PLANS'] = tp
    us_path = REPO / '.localonly' / 'urlstatus.json'
    globals()['USTAT'] = json.loads(us_path.read_text()) if us_path.exists() else {}
    p3_dir = REPO / '.localonly' / 'p3-results'
    p3r = {}
    if p3_dir.exists():
        for p in p3_dir.rglob('*.json'):
            parts = p.relative_to(p3_dir).with_suffix('').parts
            if len(parts) == 3:
                try:
                    p3r[tuple(parts)] = json.loads(p.read_text())
                except Exception:
                    pass  # 代理并发写盘中，跳过本轮
    globals()['P3_RESULTS'] = p3r
    configs = {k: v for k, v in CONFIGS.items()
               if args.section is None or k == args.section}
    missing_pr, no_app, unrouted = [], [], []
    count = 0
    for cfg_name, cfg in configs.items():
        out_base = OUT / cfg_name
        for subdir, f in iter_source_files(cfg):
            raw = f.read_text()
            tmatch = re.search(r'^title:\s*(.+)', raw, re.M)
            title = tmatch.group(1).strip() if tmatch else ''
            tgt = resolve(cfg_name, cfg, subdir, f.name, title)
            if not tgt:
                unrouted.append(f'{cfg["src"]}/{subdir or ""}/{f.name}')
                continue
            folder, name, key = tgt
            if args.only and args.only not in f.name and args.only not in name:
                continue
            out_path = out_base / folder / f'{name}.md'
            if out_path.exists() and not args.force:
                continue
            bodies = pmap.get(key, []) if key is not None else []
            if key is not None and not bodies:
                missing_pr.append(f'{cfg_name}/{folder}/{name}')
            src_path = f'{cfg["src"]}/{subdir}/{f.name}' if subdir else f'{cfg["src"]}/{f.name}'
            m_num = re.match(r'^(\d+)\.md$', f.name)
            epnum = int(m_num.group(1)) if (cfg_name == 'ShuiQianXiaoXi' and m_num) else None
            text, app, anchored = build_one(raw, bodies, cmap.get(src_path, []),
                                            linkstatus, epnum, cfg_name, folder, name)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text)
            if app.empty():
                no_app.append(f'{cfg_name}/{folder}/{name}')
            count += 1
            print(f'  ✓ {cfg_name}/{folder}/{name}.md  '
                  f'(核对{len(app.checks)} 订正{len(app.corr)} 待核{len(app.pending)}'
                  f' 锚定{sum(anchored)}/{len(anchored)})')
    print(f'\nwritten: {count}')
    build_index()
    for label, lst in (('无对应PR', missing_pr), ('无附录内容', no_app), ('未路由', unrouted)):
        if lst:
            print(f'{label}（{len(lst)}）:', *lst[:8], sep='\n  ', end='\n' if len(lst) <= 8 else '\n  …\n')




def build_index():
    """生成每栏目 INDEX.md（分级：栏目 → 百期段 → 期）与根目录 INDEX.md。"""
    root = OUT
    section_links = []
    for cfg_name, cfg in CONFIGS.items():
        sec_dir = root / cfg_name
        if not sec_dir.exists():
            continue
        entries = []
        for f in sorted(sec_dir.rglob('*.md')):
            if f.name == 'INDEX.md':
                continue
            t = f.read_text()
            tm = re.search(r'^# (.+)', t, re.M)
            title = tm.group(1).strip() if tm else f.stem
            rel = f.relative_to(sec_dir)
            entries.append((str(rel), title))
        entries.sort()
        # 分级：按文件夹
        by_folder = {}
        for rel, title in entries:
            folder = rel.split('/')[0]
            by_folder.setdefault(folder, []).append((rel, title))
        out = [f'# {cfg_name} 节目索引', '', f'共 {len(entries)} 篇。', '']
        for folder in sorted(by_folder):
            out += [f'## {folder}', '']
            for rel, title in by_folder[folder]:
                out.append(f'- [{title}]({rel})')
            out.append('')
        (sec_dir / 'INDEX.md').write_text('\n'.join(out) + '\n')
        section_links.append((cfg_name, len(entries)))
    out = ['# bedtimenews-md 节目总索引', '', '基于多步校对的干净 Markdown 文本。', '']
    for cfg_name, n in section_links:
        out.append(f'- [{cfg_name}]({cfg_name}/INDEX.md)（{n} 篇）')
    (root / 'INDEX.md').write_text('\n'.join(out) + '\n')
    print(f'index: {sum(n for _, n in section_links)} 篇已编目')



if __name__ == '__main__':
    main()
