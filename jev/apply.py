"""[반영] 처리: 체크된 정오표 항목을 장 원고에 적용하고 기록을 남긴다.

산출 (output/applied/):
  반영내역_<ch>_<ts>.md / .csv     — 반영·미반영 전체 기록
  <ch>_revised_<ts>.md / .html     — 반영된 원고 (html은 변경부 하이라이트)
  정오표_게시판_<ch>_<ts>.html      — 회사 게시판 업로드용 정오표 (반영 건만)

CLI(서버 없이 HTML에서 내려받은 decisions JSON 처리):
  python -m jev.apply <decisions.json>
"""
import csv
import html
import json
import re
from datetime import datetime

from . import config


def loose(s):
    """공백·줄바꿈 차이를 무시하는 정규식."""
    chars = [re.escape(c) for c in re.sub(r"\s+", "", s)]
    return re.compile(r"\s*".join(chars)) if chars else None


def locate(pages, row):
    pat = loose(row["old_text"])
    if not pat:
        return None, None
    order = sorted(pages, key=lambda p: p["page"] != row["page"])  # 판정된 쪽 먼저
    for p in order:
        m = pat.search(p["text"])
        if m:
            return p, m
    return None, None


def run(decisions, ch=None):
    ch = ch or decisions.get("chapter", "ch09")
    wd = config.workdir(ch)
    res = json.loads((wd / "results.json").read_text(encoding="utf-8"))
    book = json.loads((wd / "book.json").read_text(encoding="utf-8"))
    pages = [dict(p) for p in book["pages"]]
    edits = {p["page"]: [] for p in pages}
    outside = []
    dec = {int(d["id"]): d for d in decisions["items"]}
    reviewer = decisions.get("reviewer") or "-"
    ts = datetime.now().strftime("%Y%m%d-%H%M")

    records = []
    for r in res["rows"]:
        d = dec.get(r["id"], {})
        apply = bool(d.get("apply"))
        new_text = d.get("new_text", r["new_text"])
        rec = {**r, "new_text": new_text, "apply": apply, "note": d.get("note", ""), "status": "미반영"}
        if apply:
            if r["old_text"].strip() == new_text.strip():
                rec["status"] = "변경없음"
            else:
                p, m = locate(pages, r)
                if p is None:
                    in_chapter = any(pg["page"] == r["page"] for pg in pages)
                    rec["status"] = "반영(장 외 쪽)" if not in_chapter else "원문위치 미확인 - 수동반영 필요"
                    outside.append(rec)
                else:
                    overlap = any(not (m.end() <= s or m.start() >= e) for s, e, _, _ in edits[p["page"]])
                    if overlap:
                        rec["status"] = "다른 항목과 겹침 - 수동반영 필요"
                    else:
                        edits[p["page"]].append((m.start(), m.end(), new_text, rec["id"]))
                        rec["status"] = "반영"
                        rec["page"] = p["page"]
        records.append(rec)

    out = config.OUTPUT / "applied"
    out.mkdir(parents=True, exist_ok=True)

    # 반영된 원고 (md: 깨끗한 본문 / html: 변경부 표시)
    md, hb = [f"# {res['title']} ({ch}) — 반영본 {ts}\n"], []
    for p in pages:
        t, segs, pos = p["text"], [], 0
        clean, marked = [], []
        for s, e, new, rid in sorted(edits[p["page"]]):
            clean.append(t[pos:s]); clean.append(new)
            marked.append(html.escape(t[pos:s]))
            marked.append(f'<del>{html.escape(t[s:e])}</del><ins title="#{rid}">{html.escape(new)}</ins>')
            pos = e
        clean.append(t[pos:]); marked.append(html.escape(t[pos:]))
        md.append(f"\n## p.{p['page']}\n\n" + "".join(clean))
        hb.append(f'<section><h2>p.{p["page"]}{" ✎" if edits[p["page"]] else ""}</h2><pre>{"".join(marked)}</pre></section>')
    if outside:
        md.append("\n## 장 밖 / 수동 반영 항목\n")
        hb.append("<section><h2>장 밖 / 수동 반영 항목</h2>")
        for r in outside:
            md.append(f"- p.{r['page']} [{r['status']}] {r['old_text']} → {r['new_text']}")
            hb.append(f"<p>p.{r['page']} [{html.escape(r['status'])}] <del>{html.escape(r['old_text'])}</del> → "
                      f"<ins>{html.escape(r['new_text'])}</ins></p>")
        hb.append("</section>")
    (out / f"{ch}_revised_{ts}.md").write_text("\n".join(md), encoding="utf-8")
    (out / f"{ch}_revised_{ts}.html").write_text(page_html(f"{res['title']} 반영본 {ts}", "".join(hb)), encoding="utf-8")

    # 반영내역
    applied = [r for r in records if r["apply"]]
    lines = [f"# 반영내역 — {res['title']} ({ch})", "",
             f"- 반영일시: {datetime.now():%Y-%m-%d %H:%M}", f"- 반영자: {reviewer}",
             f"- 적용 법령: " + "; ".join(f"{l['name']} {l['old_header']} → {l['new_header']}" for l in res["laws"]),
             f"- 전체 {len(records)}건 / 반영 체크 {len(applied)}건 / 원고 반영 {sum(r['status'] == '반영' for r in records)}건", "",
             "| # | 쪽 | 조문 | 유형 | 수정확률 | 상태 | 기존문구 | 수정문구 | 메모 |", "|---|---|---|---|---|---|---|---|---|"]
    cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    for r in sorted(records, key=lambda r: (not r["apply"], r["page"])):
        lines.append(f"| {r['id']} | {r['page']} | {cell(r['law'])} {cell(r['article'])} | {r['judge_type']} | "
                     f"{round(r['p_revise'] * 100)}% | {r['status']} | {cell(r['old_text'])} | {cell(r['new_text'])} | {cell(r['note'])} |")
    (out / f"반영내역_{ch}_{ts}.md").write_text("\n".join(lines), encoding="utf-8")
    cols = ["id", "page", "law", "article", "judge_type", "p_revise", "p_keep", "apply", "status",
            "old_text", "new_text", "reason", "note"]
    with open(out / f"반영내역_{ch}_{ts}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(records)

    # 게시판용 정오표
    trs = "".join(
        f"<tr><td>{r['page']}</td><td>{html.escape(r['law'])} {html.escape(r['article'])}</td>"
        f"<td><del>{html.escape(r['old_text'])}</del></td><td><ins>{html.escape(r['new_text'])}</ins></td>"
        f"<td>{html.escape(r['reason'])}</td></tr>"
        for r in sorted(applied, key=lambda r: r["page"]) if r["status"] != "변경없음")
    laws = "<br>".join(f"{html.escape(l['name'])}: {html.escape(l['old_header'])} → <b>{html.escape(l['new_header'])}</b>"
                       for l in res["laws"])
    board = (f"<h1>정오표 — CHAPTER {ch[2:]} {html.escape(res['title'])}</h1>"
             f"<p class=meta>{laws}<br>반영일 {datetime.now():%Y-%m-%d} · 반영자 {html.escape(reviewer)}</p>"
             f"<table><thead><tr><th>쪽</th><th>관련 조문</th><th>기존</th><th>수정</th><th>사유</th></tr></thead>"
             f"<tbody>{trs}</tbody></table>")
    (out / f"정오표_게시판_{ch}_{ts}.html").write_text(page_html(f"정오표 {res['title']}", board), encoding="utf-8")

    files = sorted(str(p.relative_to(config.ROOT)) for p in out.glob(f"*{ch}*{ts}*"))
    print(f"[apply] 반영 {len(applied)}건 → {out}")
    return {"files": files, "counts": {"total": len(records), "checked": len(applied),
                                       "applied": sum(r["status"] == "반영" for r in records)},
            "manual": [{"id": r["id"], "page": r["page"], "status": r["status"]} for r in outside]}


def page_html(title, body):
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
body{{font-family:"Malgun Gothic","Apple SD Gothic Neo",sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222;background:#fff}}
h1{{font-size:22px}} h2{{font-size:16px;margin-top:28px;border-bottom:1px solid #ddd}}
.meta{{color:#555;font-size:13px;line-height:1.6}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #ccc;padding:6px 8px;vertical-align:top}}
th{{background:#f3f5f8}} pre{{white-space:pre-wrap;font-family:inherit;line-height:1.7}}
del{{color:#b42318;background:#fde8e7}} ins{{color:#05603a;background:#e3f6ea;text-decoration:none;font-weight:600}}
</style></head><body>{body}</body></html>"""


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python -m jev.apply <decisions.json>")
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.dumps(run(json.load(f)), ensure_ascii=False, indent=1))
