# -*- coding: utf-8 -*-
"""
merge_notes.py —— 把 Obsidian "Webpage HTML Export" 插件导出的笔记合并成一个 index.html

功能:
  1. 收集源目录下所有 HTML 笔记(site-lib 目录除外)
  2. 提取每篇笔记的正文(保留导出插件的样式),合并进一个页面
  3. 目录(TOC)锚点跳转到对应笔记,支持明/暗主题切换;
     手机端目录变成左侧抽屉:点左上角 ☰ 展开,点 ✕/遮罩/笔记链接自动收起
  4. 笔记内容里指向其他笔记的链接自动改写为页内锚点跳转
  5. 复制 site-lib 资源目录,保证发布到 GitHub Pages 后样式/字体正常

输出: OUT_DIR 下的 index.html 和 site-lib(整个 OUT_DIR 可直接发布到 GitHub)

用法: python merge_notes.py   (新增/修改笔记后重新运行即可)
"""

import html
import re
import shutil
import sys
import urllib.parse
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path

SRC_DIR = Path(r"C:\html版的学习笔记（保存文件夹）")  # 笔记源目录
OUT_DIR = Path(r"C:\html版的学习笔记（推送文件夹）")  # 输出目录(整个目录可直接发布到 GitHub)
SKIP_DIRS = {"site-lib"}                        # 排除的资源目录
SITE_TITLE = "我的笔记"                          # 站点标题(显示在目录顶部)

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
HEAD_RE = re.compile(r"<head>(.*?)</head>", re.S | re.I)
LINK_RE = re.compile(r'(href|src)\s*=\s*("|\')(.*?)\2', re.I | re.S)


class DocumentExtractor(HTMLParser):
    """提取 class 含 obsidian-document 的第一个 div 的完整 inner HTML(原样切片,不重排)。"""

    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self._source = text
        self._offsets = [0]
        pos = 0
        for line in text.split("\n"):
            pos += len(line) + 1
            self._offsets.append(pos)
        self.start = None
        self.end = None
        self.depth = 0

    def _offset(self):
        line, col = self.getpos()
        return self._offsets[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if self.end is not None:
            return
        if self.start is None:
            classes = dict(attrs).get("class", "")
            if tag == "div" and "obsidian-document" in classes.split():
                self.start = self._offset()
                self.depth = 1
        elif tag == "div":
            self.depth += 1

    def handle_endtag(self, tag):
        if self.end is not None or self.start is None:
            return
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.end = self._offset()

    def extract(self):
        self.feed(self._source)
        if self.start is None or self.end is None:
            raise ValueError("未找到 obsidian-document 正文容器")
        return self._source[self.start:self.end]


def clean_head(head_text):
    """以第一篇笔记的 <head> 为模板,去掉插件页面逻辑,保留样式与字体。"""
    head_text = re.sub(r"<base\s[^>]*>", "", head_text, flags=re.I)
    head_text = re.sub(r"<title>.*?</title>",
                       "<title>%s</title>" % html.escape(SITE_TITLE),
                       head_text, flags=re.S | re.I)
    head_text = re.sub(r"<meta[^>]*og:[^>]*>", "", head_text, flags=re.I)
    head_text = re.sub(r'<meta[^>]*name="(?:description|pathname)"[^>]*>',
                       "", head_text, flags=re.I)
    head_text = re.sub(r'<link[^>]*alternate[^>]*>', "", head_text, flags=re.I)
    head_text = re.sub(
        r'<script[^>]*id="(?:webpage-script|graph-wasm-script|graph-render-worker-script)"[^>]*?>\s*</script>',
        "", head_text, flags=re.I)
    head_text = re.sub(r"<script[^>]*>[\s\S]*?loadIncludes[\s\S]*?</script>",
                       "", head_text, flags=re.I)
    return head_text


def anchor_id(rel_path):
    """由笔记相对路径生成唯一锚点 id,如 计算机知识/常见概念.html -> note-计算机知识-常见概念"""
    p = rel_path.replace("\\", "/")
    if p.lower().endswith(".html"):
        p = p[:-5]
    p = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", p)
    return "note-" + p.strip("-")


def collect_notes():
    notes = []
    for f in sorted(SRC_DIR.rglob("*.html")):
        rel = f.relative_to(SRC_DIR)
        if rel.parts[0] in SKIP_DIRS:
            continue
        notes.append(f)
    return notes


def build_notes_map(notes):
    """相对路径 -> 锚点 id 的映射,链接改写时查表。"""
    notes_map = {}
    for f in notes:
        rel = f.relative_to(SRC_DIR).as_posix()
        anchor = anchor_id(rel)
        while anchor in notes_map.values():
            anchor += "-2"
        notes_map[rel] = anchor
        notes_map[rel.lower()] = anchor
    return notes_map


def rewrite_links(fragment, notes_map):
    """把指向其他笔记的链接改写成页内锚点;site-lib 路径统一为根相对路径。"""

    def repl(m):
        attr, quote, val = m.group(1), m.group(2), m.group(3)
        raw = html.unescape(val)
        low = raw.lower()
        if low.startswith(("#", "http://", "https://", "mailto:", "javascript:", "data:")):
            return m.group(0)
        target, _, frag = raw.partition("#")
        norm = urllib.parse.unquote(target).replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        while norm.startswith("../"):
            norm = norm[3:]
        anchor = notes_map.get(norm) or notes_map.get(norm.lower())
        if anchor:
            return '%s=%s#%s%s' % (attr, quote, anchor, quote)
        if norm.startswith("site-lib/"):
            out = norm + (("#" + frag) if frag else "")
            return '%s=%s%s%s' % (attr, quote, out, quote)
        return m.group(0)

    return LINK_RE.sub(repl, fragment)


CUSTOM_CSS = """
/* ===== 合并站点布局(由 merge_notes.py 自动生成) ===== */
/* 覆盖导出样式表的 App 外壳布局:
   html,body{height:100%} + body{overflow:clip;contain:strict}
   会把 body 锁死为视口大小并裁掉所有溢出内容,
   导致页面无法滚动、目录锚点跳转失效 */
html, body { height: auto; min-height: 100vh; overflow: visible; contain: none; }
html { scroll-behavior: smooth; }
body { margin: 0; }
#toc {
  position: fixed; top: 0; left: 0; bottom: 0; width: 280px;
  box-sizing: border-box; overflow-y: auto; padding: 1em 1.2em 3em;
  background: var(--background-secondary, #202124);
  border-right: 1px solid var(--background-modifier-border, rgba(255,255,255,.1));
  z-index: 100;
}
#toc h1 { font-size: 1.15em; margin: 0 0 .8em; }
.toc-head { display: flex; align-items: center; justify-content: space-between; }
#toc .toc-group {
  margin-top: 1.2em; font-size: .8em; letter-spacing: .06em;
  color: var(--text-muted, #999);
}
#toc ul { list-style: none; margin: .3em 0 0; padding-left: .5em; }
#toc li { margin: .4em 0; }
#toc a { color: var(--text-normal, #ddd); text-decoration: none; }
#toc a:hover { color: var(--text-accent, #7d5bed); }
#theme-toggle {
  margin: 0 0 .6em; padding: .35em .9em; font-size: .85em; cursor: pointer;
  border: 1px solid var(--background-modifier-border, rgba(255,255,255,.15));
  border-radius: 6px; background: var(--background-primary, #2a2b2e);
  color: var(--text-normal, #ddd);
}
/* 手机上打开/关闭目录的按钮和遮罩,桌面端隐藏 */
#toc-open, #toc-close, #toc-overlay { display: none; }
#content { margin-left: 280px; min-height: 100vh; }
.note { border-bottom: 1px solid var(--background-modifier-border, rgba(255,255,255,.08)); }
.note .obsidian-document {
  max-width: var(--line-width, 40em); margin: 0 auto;
  padding: 2em 1.5em 3em; height: auto; overflow: visible;
}
.note .markdown-preview-sizer { width: 100%; }
.note .markdown-preview-pusher, .note .footer { display: none; }
.back-to-toc { margin: 0 0 2em; text-align: center; }
.back-to-toc a { color: var(--text-muted, #999); text-decoration: none; font-size: .9em; }
.back-to-toc a:hover { color: var(--text-accent, #7d5bed); }
@media (max-width: 800px) {
  /* 目录变成左侧抽屉:默认收起,点左上角 ☰ 展开,点遮罩/✕/任意笔记自动收起 */
  #toc {
    width: min(80vw, 320px); max-height: none;
    box-shadow: 4px 0 24px rgba(0,0,0,.35);
    transform: translateX(-105%);
    transition: transform .25s ease;
    z-index: 310;
  }
  body.toc-open #toc { transform: translateX(0); }
  #toc-close {
    display: block; background: none; border: none;
    color: var(--text-muted, #999); font-size: 1.15em; cursor: pointer;
  }
  #toc-open {
    display: block; position: fixed; top: 10px; left: 10px; z-index: 300;
    width: 42px; height: 42px; font-size: 20px; line-height: 1;
    background: var(--background-primary, #2a2b2e);
    color: var(--text-normal, #ddd);
    border: 1px solid var(--background-modifier-border, rgba(255,255,255,.15));
    border-radius: 10px; cursor: pointer;
  }
  #toc-overlay {
    display: block; position: fixed; inset: 0; z-index: 305;
    background: rgba(0,0,0,.45); opacity: 0; pointer-events: none;
    transition: opacity .25s ease;
  }
  body.toc-open #toc-overlay { opacity: 1; pointer-events: auto; }
  /* 给内容让出 ☰ 按钮的位置 */
  #content { margin-left: 0; padding-top: 3.4em; }
}
"""

THEME_SCRIPT = """<script>
(function () {
  function currentTheme() {
    return localStorage.getItem("theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function apply(theme) {
    document.body.classList.toggle("theme-dark", theme === "dark");
    document.body.classList.toggle("theme-light", theme !== "dark");
  }
  apply(currentTheme());
  window.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", function () {
      var t = document.body.classList.contains("theme-dark") ? "light" : "dark";
      localStorage.setItem("theme", t);
      apply(t);
    });
    // 手机端目录抽屉:☰ 展开,✕/遮罩/点击笔记链接后收起
    function setTocOpen(open) {
      document.body.classList.toggle("toc-open", open);
    }
    var openBtn = document.getElementById("toc-open");
    var closeBtn = document.getElementById("toc-close");
    var overlay = document.getElementById("toc-overlay");
    if (openBtn) openBtn.addEventListener("click", function () { setTocOpen(true); });
    if (closeBtn) closeBtn.addEventListener("click", function () { setTocOpen(false); });
    if (overlay) overlay.addEventListener("click", function () { setTocOpen(false); });
    var tocLinks = document.querySelectorAll("#toc a[href^='#']");
    for (var i = 0; i < tocLinks.length; i++) {
      tocLinks[i].addEventListener("click", function () { setTocOpen(false); });
    }
  });
})();
</script>"""

BODY_CLASS = "publish css-settings-manager styled-scrollbars show-inline-title show-ribbon is-focused"


def main():
    # Windows 控制台默认 GBK,切换为 UTF-8 避免中文输出乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not SRC_DIR.exists():
        print("源目录不存在:", SRC_DIR)
        return
    notes = collect_notes()
    if not notes:
        print("未找到任何笔记 HTML 文件:", SRC_DIR)
        return

    notes_map = build_notes_map(notes)

    # 以第一篇笔记的 <head> 为模板(所有笔记的样式引用一致)
    first_text = notes[0].read_text(encoding="utf-8")
    head_match = HEAD_RE.search(first_text)
    if not head_match:
        print("第一篇笔记中未找到 <head>:", notes[0])
        return
    head = clean_head(head_match.group(1))

    toc_parts = ['<div id="toc">',
                 '<div class="toc-head">',
                 "<h1>\U0001f4d3 %s</h1>" % html.escape(SITE_TITLE),
                 '<button id="toc-close" type="button" aria-label="关闭目录">✕</button>',
                 '</div>',
                 '<button id="theme-toggle" type="button">切换 明/暗 主题</button>']
    sections = []
    groups = OrderedDict()
    count = 0

    for f in notes:
        text = f.read_text(encoding="utf-8")
        m = TITLE_RE.search(text)
        title = html.unescape(m.group(1)).strip() if m else f.stem
        rel = f.relative_to(SRC_DIR).as_posix()
        anchor = notes_map[rel]
        group = f.relative_to(SRC_DIR).parent.as_posix()
        if not group or group == ".":
            group = "笔记"

        extractor = DocumentExtractor(text)
        try:
            body = extractor.extract()
        except ValueError:
            print("[跳过] 未找到正文:", rel)
            continue
        body = rewrite_links(body, notes_map)

        groups.setdefault(group, []).append((anchor, title))
        sections.append(
            '<!-- ===== %s ===== -->\n<section class="note" id="%s">\n%s\n'
            '<div class="back-to-toc"><a href="#top">\u2191 返回目录</a></div>\n</section>'
            % (rel, anchor, body)
        )
        count += 1

    for group, items in groups.items():
        toc_parts.append('<div class="toc-group">%s</div><ul>' % html.escape(group))
        for anchor, title in items:
            toc_parts.append('<li><a href="#%s">%s</a></li>'
                             % (anchor, html.escape(title)))
        toc_parts.append("</ul>")
    toc_parts.append("</div>")
    toc_parts.append('<button id="toc-open" type="button" aria-label="打开目录">☰</button>')
    toc_parts.append('<div id="toc-overlay"></div>')

    merged = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        + head
        + "\n<style>" + CUSTOM_CSS + "</style>\n</head>\n"
        + '<body class="%s">\n' % BODY_CLASS
        + THEME_SCRIPT
        + '\n<div id="top"></div>\n'
        + "\n".join(toc_parts)
        + '\n<div id="content">\n'
        + "\n".join(sections)
        + "\n</div>\n</body>\n</html>\n"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(merged, encoding="utf-8")
    if (SRC_DIR / "site-lib").exists():
        shutil.copytree(SRC_DIR / "site-lib", OUT_DIR / "site-lib",
                        dirs_exist_ok=True)
    else:
        print("警告: 未找到 site-lib 资源目录,页面样式可能缺失")

    print("已合并 %d 篇笔记 -> %s" % (count, OUT_DIR / "index.html"))
    print("site-lib 资源已复制 -> %s" % (OUT_DIR / "site-lib"))
    print("本地预览: 双击 index.html 即可在浏览器打开")


if __name__ == "__main__":
    main()
