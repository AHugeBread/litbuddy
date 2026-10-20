# -*- coding: utf-8 -*-
"""extract_figs.py — 文献插图三层提取工具（litbuddy v2.2.0）

三层策略（见 references/02 图提取三层）：
  L1 嵌入位图直取：单个嵌入位图即整图且边长 >= --min-side 时按原分辨率抽取
  L2 图注锚定区域渲染：以 "Fig. N" 图注行定位，取邻近图形簇 bbox 渲染 PNG
  L3 高成本兜底：整页渲染待 AI 视觉定框（--allow-l3，需 Max 模式开）；
     或由 agent 给出坐标用 --clip N=x0,y0,x1,y1 回填（视为 L3 完成）

用法：
  python extract_figs.py <pdf> --out <dir> [--figs all|1,2,3] [--dpi 160]
                         [--min-side 50] [--allow-l3] [--clip 2=100,80,500,400]

输出：<dir>/图N_<短名>.png + <dir>/extract_report.json（逐图命中层，供自检与计量）
不修改源 PDF；失败图在报告中显式记原因，不静默丢图。
"""
import argparse
import json
import os
import re
import sys

try:
    import fitz
except ImportError:
    print("ERROR: 需要 PyMuPDF（fitz）。可用 Python 环境见 references/05-检索任务规范.md 提取经验一节。", file=sys.stderr)
    sys.exit(2)

CAPTION_RE = re.compile(r"^(?:Fig\.|Figure|FIG\.)\s*(\d+)", re.I)
CAPTION_CN_RE = re.compile(r"^图\s*(\d+\s*[-.．]\s*\d+|\d+)")
PAD = 4.0
MIN_CLUSTER_SIDE = 8.0
X_OVERLAP_MIN = 15.0
NEAR_GAP_MAX = 160.0
MERGE_GROW = 6.0


def parse_args():
    ap = argparse.ArgumentParser(description="litbuddy figure extraction (3-layer)")
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--figs", default="all", help="all 或逗号分隔编号，如 1,2,3")
    ap.add_argument("--dpi", type=int, default=160)
    ap.add_argument("--min-side", type=int, default=50, help="L1 直取的最小边长阈值")
    ap.add_argument("--allow-l3", action="store_true", help="允许 L3 整页渲染兜底（需 Max 模式开）")
    ap.add_argument("--clip", action="append", default=[],
                    help="N=x0,y0,x1,y1 页面点坐标，直接按给定区域渲染（L3 人工/AI 定框回填）")
    return ap.parse_args()


def merge_rects(rects):
    """迭代合并相交/邻近（<=MERGE_GROW pt）的矩形，返回簇 bbox 列表。"""
    rs = [fitz.Rect(r) for r in rects]
    merged = True
    while merged and len(rs) > 1:
        merged = False
        out = []
        used = [False] * len(rs)
        for i in range(len(rs)):
            if used[i]:
                continue
            cur = fitz.Rect(rs[i])
            for j in range(i + 1, len(rs)):
                if used[j]:
                    continue
                cand = fitz.Rect(rs[j])
                grown = fitz.Rect(cur.x0 - MERGE_GROW, cur.y0 - MERGE_GROW,
                                  cur.x1 + MERGE_GROW, cur.y1 + MERGE_GROW)
                if grown.intersects(cand):
                    cur |= cand
                    used[j] = True
                    merged = True
            out.append(cur)
            used[i] = True
        rs = out
    return rs


def page_graphic_clusters(page):
    """页面上的图形簇（矢量绘图 + 嵌入位图），滤掉发丝级碎片。"""
    rects = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width >= 3 and r.height >= 3:
            rects.append(r)
    img_boxes = []
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"])
        img_boxes.append((r, info))
        if r.width >= 3 and r.height >= 3:
            rects.append(r)
    return merge_rects(rects), img_boxes


def find_captions(page):
    """行级图注识别（中英文）。真图注 vs 正文引用的启发式过滤；
    返回锚行 rect + 所属 block rect（图注段常为窄锚行 + 宽续行，x 交叠要用 block）。"""
    caps = []
    dd = page.get_text("dict")
    for block in dd["blocks"]:
        if block.get("type", 0) != 0:
            continue
        brect = fitz.Rect(block["bbox"])
        for line in block["lines"]:
            txt = "".join(s["text"] for s in line["spans"]).strip()
            m = CAPTION_RE.match(txt)
            if m:
                fig = str(int(m.group(1)))
                rest = txt[m.end():]
            else:
                m2 = CAPTION_CN_RE.match(txt)
                if not m2:
                    continue
                fig = re.sub(r"\s+", "", m2.group(1))
                rest = txt[m2.end():]
            rest = rest.lstrip(" .:：—-,，(（[a-fA-F]）)]")
            # 过滤正文引用谓语型
            if re.match(r"^(shows|is|are|illustrates|depicts|presents|and|for|with|can|to)\b", rest, re.I):
                continue
            if re.match(r"^(中的|所示|给出|为|和|与|是|分别|对比|可见|可知|进行|采用|如下)", rest):
                continue
            if len(txt) < 6:
                continue
            caps.append({"fig": fig, "rect": fitz.Rect(line["bbox"]),
                         "brect": brect, "text": txt[:80]})
    return caps


def pick_cluster(cap, clusters):
    """选图注邻近的图形簇：x 交叠（用 block 宽度）+ 最小纵向间距（IEEE 图注多在图下方）。"""
    cr = cap["brect"]
    line = cap["rect"]
    best, best_gap = None, None
    for cl in clusters:
        xo = min(cr.x1, cl.x1) - max(cr.x0, cl.x0)
        if xo < X_OVERLAP_MIN and xo < 0.15 * cr.width:
            continue
        if cl.y1 <= line.y0 + 2:          # 簇在图注上方
            gap = line.y0 - cl.y1
        elif cl.y0 >= line.y1 - 2:        # 簇在图注下方
            gap = cl.y0 - line.y1 + 20    # IEEE 惯例偏上方，下方稍降权
        else:
            gap = 0.0                     # 重叠
        if gap > NEAR_GAP_MAX:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = cl, gap
    return best, best_gap


def l1_extract(doc, page, cluster, img_boxes, min_side):
    """若簇基本就是单个大嵌入位图，走 L1 原分辨率直取。"""
    hits = []
    for r, info in img_boxes:
        if cluster.contains(r.tl) and cluster.contains(r.br):
            if min(r.width, r.height) >= min_side:
                hits.append(info)
    if len(hits) == 1:
        info = hits[0]
        try:
            xref = info.get("xref")
            if not xref:
                xref = page.get_images(full=True)[0][0]
            base = doc.extract_image(xref)
            return base["image"], base.get("ext", "png")
        except Exception:
            return None, None
    return None, None


def main():
    args = parse_args()
    os.makedirs(args.out, exist_ok=True)
    clips = {}
    for spec in args.clip:
        n, coords = spec.split("=", 1)
        clips[n.strip()] = tuple(float(v) for v in coords.split(","))

    doc = fitz.open(args.pdf)
    # 收集图注
    captions = {}
    for page in doc:
        for cap in find_captions(page):
            captions.setdefault(cap["fig"], []).append({**cap, "page": page.number})

    def figkey(s):
        return [int(x) if x.isdigit() else x for x in re.split(r"[-.．]", s)]

    if args.figs == "all":
        want = sorted(captions.keys(), key=figkey)
    else:
        want = sorted({x.strip() for x in args.figs.split(",") if x.strip()}, key=figkey)

    report = {"pdf": os.path.basename(args.pdf), "dpi": args.dpi, "figures": []}
    for n in want:
        entry = {"fig": n, "layer": None, "file": None, "page": None, "bbox": None, "note": None}
        # L3 回填：给定 clip
        if n in clips:
            x0, y0, x1, y1 = clips[n]
            pg = captions[n][0]["page"] if n in captions else 0
            page = doc[pg]
            pix = page.get_pixmap(clip=fitz.Rect(x0, y0, x1, y1), dpi=args.dpi)
            fn = f"图{n}_clip.png"
            pix.save(os.path.join(args.out, fn))
            entry.update(layer="L3-clip", file=fn, page=pg + 1, bbox=[x0, y0, x1, y1])
            report["figures"].append(entry)
            continue
        if n not in captions:
            entry.update(layer="NEED_L3", note="未找到该图的图注行")
            report["figures"].append(entry)
            continue
        cap = captions[n][0]
        page = doc[cap["page"]]
        clusters, img_boxes = page_graphic_clusters(page)
        cl, gap = pick_cluster(cap, clusters)
        if cl is None:
            # 全部候选图注行都试一遍（正文引用锚行可能定不准，真图注行可以）
            for alt in captions[n][1:]:
                cl, gap = pick_cluster(alt, clusters)
                if cl is not None:
                    cap = alt
                    break
        if cl is None:
            if args.allow_l3:
                os.makedirs(os.path.join(args.out, "_l3"), exist_ok=True)
                fn = f"_l3_page{page.number + 1}.png"
                page.get_pixmap(dpi=120).save(os.path.join(args.out, "_l3", fn))
                entry.update(layer="NEED_L3", page=page.number + 1,
                             note=f"邻近图形簇定位失败；整页渲染已出：_l3/{fn}，待视觉定框后用 --clip {n}=x0,y0,x1,y1 回填")
            else:
                entry.update(layer="NEED_L3", page=page.number + 1,
                             note="邻近图形簇定位失败；Max 模式关（未给 --allow-l3），已如实报错，不静默降级")
            report["figures"].append(entry)
            continue
        bbox = fitz.Rect(cl.x0 - PAD, cl.y0 - PAD, cl.x1 + PAD, cl.y1 + PAD)
        data, ext = l1_extract(doc, page, cl, img_boxes, args.min_side)
        if data:
            fn = f"图{n}_embedded.{ext}"
            with open(os.path.join(args.out, fn), "wb") as f:
                f.write(data)
            entry.update(layer="L1", file=fn, page=cap["page"] + 1,
                         bbox=[round(v, 1) for v in bbox], note="嵌入位图直取（原分辨率）")
        else:
            pix = page.get_pixmap(clip=bbox, dpi=args.dpi)
            fn = f"图{n}_region.png"
            pix.save(os.path.join(args.out, fn))
            entry.update(layer="L2", file=fn, page=cap["page"] + 1,
                         bbox=[round(v, 1) for v in bbox],
                         note=f"图注锚定区域渲染（图注: {cap['text']}）")
        report["figures"].append(entry)

    with open(os.path.join(args.out, "extract_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 逐图报告
    for e in report["figures"]:
        print(f"图{e['fig']}: {e['layer'] or '-'} | p{e['page'] or '-'} | {e['file'] or '（无文件）'} | {e['note'] or ''}")
    done = sum(1 for e in report["figures"] if e["layer"] in ("L1", "L2", "L3-clip"))
    l3 = sum(1 for e in report["figures"] if e["layer"] == "NEED_L3")
    print(f"--- 完成 {done}/{len(report['figures'])}，待 L3 {l3} 件")
    doc.close()


if __name__ == "__main__":
    main()
