# -*- coding: utf-8 -*-
"""文献检索 · 单文件生成器

扫描「项目根」下的文献库与加工产物，生成一个**零依赖的单文件 HTML 检索程序**：
  - 双击即可用浏览器打开，不需要安装 Python 或任何东西
  - 支持「关键词」与「正则」两种检索
  - 结果联动「文献初印象卡」定位文献，并可直接点开原始文件与加工产物
  - 全部使用相对路径 → 随文件夹整体迁移到任何人的电脑都能用

用法：
    python build_search.py                        # 自动识别项目根（tools 的上一级）
    python build_search.py --root <项目根>          # 指定要索引的项目根
    python build_search.py --out <输出.html>        # 指定输出文件
"""
import os
import re
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))


def default_root():
    """tools\\build_search.py 的上一级 = 项目根。"""
    return os.path.dirname(HERE)


# 纳入检索的文本目录（按知识加工层级；不含 99-临时文件 与 _收件箱 这类非知识层）
TEXT_DIRS = ["00-AI协作规范", "01-原始文献", "02-加工与沉淀", "03-每日简报"]
# 原始件（用于「打开原文」）
RAW_DIRS = ["01-原始文献"]
RAW_EXT = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg"}
SKIP_EXT = {".py", ".html", ".htm", ".json", ".yaml", ".log"}
CARD_REL = os.path.join("01-原始文献", "文献初印象卡（总览）.md")


def strip_comments(text):
    """剥离 HTML 注释（`<!-- ... -->`，含跨行）。

    HTML 注释在本系统里是**写给人和 AI 看的填写指引**（如卡片骨架里的条目格式示例），
    **不是内容**。若不剥离：
      - 卡片解析器会把示例 `### D-01 <题名>` 当成一条真卡片；
      - 占位符文本（`<100–300 字>`、`<类型>·<单位>·<年份>` 等）会进检索索引。
    两者都会造成**假命中**。故在读取层统一剥离，卡片解析与检索索引口径一致。
    """
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return strip_comments(f.read())
        except (UnicodeDecodeError, UnicodeError, OSError):
            continue
    return ""


def parse_cards(root):
    path = os.path.join(root, CARD_REL)
    if not os.path.exists(path):
        return []
    txt = read_text(path)
    cards = []
    for block in re.split(r"\n(?=###\s)", txt):
        m = re.match(r"###\s+([A-Za-z]+-\d+)\s+(.+)", block.strip())
        if not m:
            continue
        code, title = m.group(1), m.group(2).strip()

        def field(name):
            mm = re.search(r"\*\*%s\*\*[:：]\s*(.+)" % name, block)
            return mm.group(1).strip() if mm else ""

        cards.append({
            "code": code,
            "title": title,
            "meta": field("论文") or field("资料"),
            "intro": field("简介"),
            "rel": field("关联"),
        })
    return cards


def scan_docs(root, out_abs):
    docs = []
    for layer in TEXT_DIRS:
        base = os.path.join(root, layer)
        if not os.path.isdir(base):
            continue
        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                p = os.path.join(dirpath, name)
                ext = os.path.splitext(name)[1].lower()
                if ext not in (".md", ".txt") or ext in SKIP_EXT:
                    continue
                if os.path.abspath(p) == out_abs:
                    continue
                # 机器文件不进索引：_build* 生成物、_水位线 等以 "_" 开头的、以及隐藏文件
                if name.startswith("_") or name.startswith("."):
                    continue
                body = read_text(p)
                if not body.strip():
                    continue
                docs.append({
                    "file": os.path.relpath(p, root).replace("\\", "/"),
                    "name": name,
                    "layer": layer,
                    "body": body,
                })
    return docs


def scan_raw(root, out_abs):
    raw = []
    for layer in RAW_DIRS:
        base = os.path.join(root, layer)
        if not os.path.isdir(base):
            continue
        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                p = os.path.join(dirpath, name)
                if os.path.splitext(name)[1].lower() in RAW_EXT and os.path.abspath(p) != out_abs:
                    raw.append({
                        "file": os.path.relpath(p, root).replace("\\", "/"),
                        "name": name,
                    })
    return raw


def build(root, out):
    root = os.path.abspath(root)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    out_abs = os.path.abspath(out)
    cards = parse_cards(root)
    docs = scan_docs(root, out_abs)
    raw = scan_raw(root, out_abs)
    data = {"cards": cards, "docs": docs, "raw": raw, "root": os.path.basename(root)}
    payload = json.dumps(data, ensure_ascii=False)
    with open(out, "w", encoding="utf-8") as f:
        f.write(HTML_HEAD + payload + HTML_TAIL)
    return len(cards), len(docs), len(raw), out_abs


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>文献检索</title>
<style>
  :root{--bg:#ffffff;--fg:#1a1a1a;--dim:#6b7280;--line:#e5e7eb;--accent:#2563eb;--chip:#f3f4f6;--hl:#fde68a;}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg)}
  header{padding:18px 24px 12px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:9}
  h1{margin:0 0 10px;font-size:17px;font-weight:600}
  .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  input[type=text]{flex:1;min-width:260px;padding:9px 12px;font-size:14px;border:1px solid var(--line);border-radius:8px;outline:none}
  input[type=text]:focus{border-color:var(--accent)}
  button{padding:9px 14px;font-size:14px;border:1px solid var(--line);background:var(--chip);border-radius:8px;cursor:pointer}
  button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  .stat{color:var(--dim);font-size:12px;margin-top:8px}
  main{padding:16px 24px 60px;max-width:1000px}
  .sec{font-size:12px;font-weight:600;color:var(--dim);letter-spacing:.06em;margin:20px 0 8px;text-transform:uppercase}
  .hit{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px}
  .hit:hover{border-color:var(--accent)}
  .h-top{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
  .code{font:600 12px/1.4 ui-monospace,Consolas,monospace;background:var(--accent);color:#fff;padding:2px 7px;border-radius:5px}
  .title{font-weight:600}
  .meta{color:var(--dim);font-size:12px}
  .snippet{margin-top:7px;color:#374151;font-size:13px;word-break:break-word}
  .snip{display:block;margin:3px 0;padding-left:8px;border-left:2px solid var(--line)}
  mark{background:var(--hl);padding:0 1px}
  .file{color:var(--dim);font-size:12px;font:400 12px ui-monospace,Consolas,monospace}
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  .empty{color:var(--dim);padding:30px 0;text-align:center}
  .tag{font-size:11px;background:var(--chip);border-radius:4px;padding:1px 6px;color:var(--dim)}
  .tip{color:var(--dim);font-size:12px;padding:10px 24px}
</style>
</head>
<body>
<header>
  <h1>文献检索 · <span id="rootname"></span></h1>
  <div class="bar">
    <input type="text" id="q" placeholder="输入一段概念或话，例如：负载电阻变化范围 压缩" autocomplete="off">
    <button id="mode-kw" class="on">关键词</button>
    <button id="mode-re">正则</button>
    <button id="btn-go">检索</button>
  </div>
  <div class="stat" id="stat">就绪</div>
</header>
<main id="out"><div class="empty">输入关键词开始检索</div></main>
<div class="tip">提示：结果中的链接在「用浏览器直接打开本文件」时可点开；多个关键词用空格分隔（全部命中才算命中）。</div>
<script>
const DATA = """

HTML_TAIL = """;
let MODE = "kw";
const $ = s => document.querySelector(s);
$("#rootname").textContent = DATA.root;

function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function esc4re(s){return s.replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&");}

function matcher(q){
  if(MODE === "re"){
    try{ const re = new RegExp(q, "i"); return t => re.test(t); }
    catch(e){ return null; }
  }
  const terms = q.trim().split(/\\s+/).filter(Boolean).map(s=>s.toLowerCase());
  return t => { const l = String(t).toLowerCase(); return terms.every(x=>l.includes(x)); };
}

function countIn(text, q){
  if(MODE === "re"){ try{ const re=new RegExp(q,"gi"); return (text.match(re)||[]).length; }catch(e){ return 0; } }
  const terms = q.trim().split(/\\s+/).filter(Boolean); const l = String(text).toLowerCase();
  return terms.reduce((a,t)=>a + (l.split(t.toLowerCase()).length-1), 0);
}

function highlight(s, q){
  try{
    if(MODE === "re") return s.replace(new RegExp(q, "gi"), m=>"<mark>"+m+"</mark>");
    for(const t of q.trim().split(/\\s+/).filter(Boolean)){
      if(t) s = s.replace(new RegExp(esc4re(t), "gi"), m=>"<mark>"+m+"</mark>");
    }
  }catch(e){}
  return s;
}

function snippet(text, q, len){
  text = String(text);
  let idx = -1;
  if(MODE === "re"){ try{ const m=new RegExp(q,"i").exec(text); idx = m?m.index:-1; }catch(e){} }
  else{
    const terms = q.trim().split(/\\s+/).filter(Boolean);
    for(const t of terms){ const i = text.toLowerCase().indexOf(t.toLowerCase()); if(i>=0 && (idx<0||i<idx)) idx=i; }
  }
  if(idx < 0) idx = 0;
  const start = Math.max(0, idx - 40);
  let s = text.slice(start, start + len);
  if(start > 0) s = "…" + s;
  return highlight(esc(s), q) + "…";
}

function run(){
  const q = $("#q").value.trim();
  const out = $("#out");
  if(!q){ out.innerHTML = '<div class="empty">输入关键词开始检索</div>'; $("#stat").textContent="就绪"; return; }
  const test = matcher(q);
  if(!test){ out.innerHTML = '<div class="empty">正则表达式无效</div>'; $("#stat").textContent="正则错误"; return; }

  const cardHits = DATA.cards.filter(c => test([c.code,c.title,c.meta,c.intro,c.rel].join(" ")));
  const docHits = DATA.docs.map(d => {
    const n = countIn(d.body, q);
    const nm = test(d.name) || test(d.layer);
    return {d, n, nm};
  }).filter(x => x.n > 0 || x.nm).sort((a,b)=> (b.n+b.nm*3) - (a.n+a.nm*3)).slice(0, 25);
  const rawHits = DATA.raw.filter(r => test(r.name));

  let h = "";
  if(cardHits.length){
    h += '<div class="sec">初印象卡定位 · '+cardHits.length+' 条</div>';
    for(const c of cardHits){
      h += '<div class="hit"><div class="h-top"><span class="code">'+esc(c.code)+'</span><span class="title">'+esc(c.title)+'</span></div>'
         + '<div class="meta">'+esc(c.meta)+'</div>'
         + '<div class="snippet">'+snippet(c.intro + "　｜关联：" + c.rel, q, 260)+'</div></div>';
    }
  }
  if(docHits.length){
    h += '<div class="sec">加工产物 · '+docHits.length+' 篇</div>';
    for(const x of docHits){
      h += '<div class="hit"><div class="h-top"><span class="tag">'+esc(x.d.layer)+'</span>'
         + '<span class="title">'+esc(x.d.name)+'</span><span class="meta">命中 '+x.n+' 处</span></div>'
         + '<div class="snippet"><span class="snip">'+snippet(x.d.body, q, 240)+'</span></div>'
         + '<div class="file"><a href="'+esc(x.d.file)+'" target="_blank">打开 · '+esc(x.d.file)+'</a></div></div>';
    }
  }
  if(rawHits.length){
    h += '<div class="sec">原始文献 · '+rawHits.length+' 份</div>';
    for(const r of rawHits){
      h += '<div class="hit"><div class="h-top"><span class="title">'+esc(r.name)+'</span></div>'
         + '<div class="file"><a href="'+esc(r.file)+'" target="_blank">打开 · '+esc(r.file)+'</a></div></div>';
    }
  }
  out.innerHTML = h || '<div class="empty">没有命中</div>';
  $("#stat").textContent = "卡片 "+cardHits.length+" ｜ 产物 "+docHits.length+" ｜ 原文 "+rawHits.length
      + "　（库内共 "+DATA.cards.length+" 张卡 / "+DATA.docs.length+" 篇产物 / "+DATA.raw.length+" 份原文）";
}

$("#btn-go").onclick = run;
$("#q").addEventListener("keydown", e => { if(e.key === "Enter") run(); });
$("#mode-kw").onclick = () => { MODE="kw"; $("#mode-kw").classList.add("on"); $("#mode-re").classList.remove("on"); run(); };
$("#mode-re").onclick = () => { MODE="re"; $("#mode-re").classList.add("on"); $("#mode-kw").classList.remove("on"); run(); };
$("#q").focus();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="生成单文件文献检索程序")
    ap.add_argument("--root", default=None, help="要索引的项目根（默认：本脚本的上一级）")
    ap.add_argument("--out", default=None, help="输出 HTML 路径（默认：<项目根>\\打开文献检索.html）")
    args = ap.parse_args()

    root = os.path.abspath(args.root) if args.root else default_root()
    out = os.path.abspath(args.out) if args.out else os.path.join(root, "打开文献检索.html")

    if not os.path.isdir(root):
        raise SystemExit("项目根不存在: " + root)

    n_cards, n_docs, n_raw, out_abs = build(root, out)
    print("项目根:", root)
    print("索引: 卡片 %d ｜ 产物 %d ｜ 原文 %d" % (n_cards, n_docs, n_raw))
    print("输出:", out_abs, "(%.1f KB)" % (os.path.getsize(out_abs) / 1024))


if __name__ == "__main__":
    main()
