"""④ 정오 판정 · ⑤ 정오표 받기 — 장마다 나온 판정 결과를 책 하나로 모아 체크하고, 정오표를 책 전체 한 번에 만든다.

workspace/_정오표/
  decisions.json               체크 · 직접 고친 수정 문구 · 메모 ({"09-3": {apply, new_text?, note?}}, 없는 키 = 기본값)
  <책>_정오표.xlsx              [정오표] 체크한 것 · [전체 후보] 반영(O) · 수정 문구 · 메모를 고쳐 다시 올릴 수 있음 · [요약]
  <책>_정오표_검토.pdf          회의용 종이 (☑/☐ · 메모칸)
  <책>_정오표_검토.html         브라우저에서 체크 → [체크 결과 저장] → 콘솔 「체크본 가져오기」
  <책>_정오표_게시판.html       체크한 것만, 장 · 쪽 순 (회사 게시판용)

행 ID 는 '<장>-<장 안 번호>' (예: 09-3). 장 안 번호는 판정할 때마다 다시 매겨지므로, 다시 판정한 장의 체크는 기본값으로 돌아간다.
"""
import html
import io
import json
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import apply, config, report

DIR = config.ERRATA_DIR
DONE = ("반영", "반영(장 외 쪽)", "변경없음")  # 이 밖의 상태는 사람이 원고에서 직접 확인


# ---------- 행 · 체크 ----------
def results(cid):
    f = config.stage(cid, "03_판정") / "results.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def rows():
    """판정이 끝난 모든 장의 후보 → 한 줄씩 (장 · 쪽 순)."""
    out, chs = [], []
    for c in config.chapters():
        res = results(c["id"])
        if not res:
            continue
        chs.append({"id": c["id"], "title": c["title"], "mode": res.get("mode", "law"), "laws": res["laws"],
                    "n": len(res["rows"]), "revise": sum(r["p_revise"] >= report.THRESHOLD for r in res["rows"])})
        for r in res["rows"]:
            out.append({"id": f"{c['id']}-{r['id']}", "rid": r["id"], "ch": c["id"], "ch_title": c["title"],
                        "page": r.get("page"), "law": r.get("law", ""), "article": r.get("article", ""),
                        "title": r.get("title", ""), "judge_type": r.get("judge_type", ""), "p": r["p_revise"],
                        "old": r.get("old_text", ""), "new": r.get("new_text", ""), "reason": r.get("reason", ""),
                        "book_quote": r.get("book_quote", ""), "old_law_quote": r.get("old_law_quote", ""),
                        "new_law_quote": r.get("new_law_quote", ""), "source": r.get("source", ""),
                        "on_default": r["p_revise"] >= report.THRESHOLD})
    out.sort(key=lambda r: (r["ch"], r["page"] or 0, r["rid"]))
    return out, chs


def load_decisions():
    f = DIR / "decisions.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_decisions(dec):
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "decisions.json").write_text(json.dumps(dec, ensure_ascii=False, indent=1), encoding="utf-8")


def edit_on(r, dec):
    """(반영?, 수정 문구, 메모) — 체크를 안 건드렸으면 기본값(수정확률 60% 이상)."""
    d = dec.get(r["id"], {})
    return d.get("apply", r["on_default"]), d["new_text"] if d.get("new_text") is not None else r["new"], d.get("note", "")


def book_name():
    t = config.toc() or {}
    pdf = config.book_pdf()
    return config.slug(pdf.stem if pdf else Path(t.get("book") or "책").stem)


def files():
    if not DIR.exists():
        return []
    return [{"name": p.name, "time": datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")}
            for p in sorted(DIR.glob("*정오표*"), key=lambda p: -p.stat().st_mtime) if p.is_file()]


def stale():
    """체크를 바꾼 뒤 정오표를 아직 다시 만들지 않았나."""
    d = DIR / "decisions.json"
    made = [p.stat().st_mtime for p in DIR.glob("*_정오표.xlsx")] if DIR.exists() else []
    return bool(made and d.exists() and d.stat().st_mtime > max(made) + 2)


def state():
    rs, chs = rows()
    return {"rows": rs, "chapters": chs, "decisions": load_decisions(), "files": files(), "stale": stale(),
            "book": book_name(), "dir": str(DIR)}


# ---------- 바뀐 글자만 색칠 ----------
def diff_spans(a, b):
    """기존 → 수정을 글자 단위로 나눠 [(글자, 바뀜?)] 두 줄로."""
    xa, xb = [], []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        xa.append((a[i1:i2], op != "equal"))
        xb.append((b[j1:j2], op != "equal"))
    return [x for x in xa if x[0]], [x for x in xb if x[0]]


def mark_pair(a, b):
    """기존 문구는 바뀌는 글자만 <b class=x>, 수정 문구는 새로 든 글자만 <b class=y>."""
    xa, xb = diff_spans(a, b)
    tag = lambda xs, c: "".join(f"<b class={c}>{html.escape(t)}</b>" if ch else html.escape(t) for t, ch in xs)
    return tag(xa, "x"), tag(xb, "y")


def _pair(r):
    o, n = mark_pair(r["old"], r["new"])
    return o, n or "<b class=x>(삭제)</b>"


RED, GREEN = InlineFont(color="C0392B", b=True), InlineFont(color="1E7A34", b=True)


def _rich(r):
    """엑셀 칸: 바뀐 글자만 붉게 / 녹색으로."""
    xa, xb = diff_spans(r["old"], r["new"])
    cell = lambda xs, f: CellRichText([TextBlock(f, t) if ch else t for t, ch in xs])
    return cell(xa, RED), cell(xb, GREEN) if r["new"] else CellRichText([TextBlock(RED, "(삭제)")])


def _art(r):
    return f"{r['law']} {r['article']}".strip()


def _where(r):
    return (f"{r['ch']}장 {html.escape(r['ch_title'][:12])}<br>{('p.' + str(r['page'])) if r['page'] else ''}"
            f"<br>{html.escape(_art(r))}")


def _laws(c):
    return "<br>".join(f"{html.escape(l['name'])}: {html.escape(l.get('old_header', ''))} → <b>{html.escape(l.get('new_header', ''))}</b>"
                       for l in c["laws"])


# ---------- 파일 ----------
HEAD_FILL = PatternFill("solid", fgColor="E9EEF6")
THIN = Side(style="thin", color="C9D1DD")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")


def _sheet(ws, cols, data):
    ws.append([c for c, _ in cols])
    for k, (_, w) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(k)].width = w
        c = ws.cell(row=1, column=k)
        c.font, c.fill, c.border = Font(bold=True), HEAD_FILL, BOX
    for r in data:
        ws.append(r)
        for k in range(1, len(cols) + 1):
            c = ws.cell(row=ws.max_row, column=k)
            c.alignment, c.border = WRAP, BOX
    ws.freeze_panes = "A2"
    if data:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{ws.max_row}"


CSS = """body{font:13px/1.55 "Malgun Gothic",malgun,sans-serif;margin:20px;color:#222}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:4px 6px;vertical-align:top;text-align:left}
th{background:#f1ece3;font-size:12px}td.c{text-align:center;white-space:nowrap}.s{color:#666;font-size:11px}
.memo{min-width:120px}.meta{color:#555;font-size:12.5px;line-height:1.6}
b.x{color:#9b1c10;background:#ffc4bb;border-radius:2px;padding:0 1px}b.y{color:#11602a;background:#a8ebb6;border-radius:2px;padding:0 1px}"""


def board_html(book, chs, on):
    """게시판에 올릴 정오표 (체크한 것만, 장마다 표 하나)."""
    esc = html.escape
    parts = []
    for c in chs:
        mine = [r for r in on if r["ch"] == c["id"]]
        if not mine:
            continue
        body = "".join(f"<tr><td class=c>{r['page'] or ''}</td><td>{esc(_art(r))}</td><td>{_pair(r)[0]}</td>"
                       f"<td>{_pair(r)[1]}</td><td>{esc(r['reason'])}</td></tr>" for r in mine)
        parts.append(f"<h3>CHAPTER {esc(c['id'])} {esc(c['title'])}</h3><p class=meta>{_laws(c)}</p>"
                     f"<table><thead><tr><th>쪽</th><th>관련 조문</th><th>기존</th><th>수정</th><th>사유</th></tr></thead>"
                     f"<tbody>{body}</tbody></table>")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{esc(book)} 정오표</title>
<style>{CSS}</style></head><body>
<h2>{esc(book)} — 정오표</h2><p class=meta>{datetime.now():%Y-%m-%d} · {len(on)}건</p>{''.join(parts)}
</body></html>"""


def review_html(book, data):
    """검토용 정오표 (후보 전부). 체크 · 수정 문구 · 메모를 고치고 [체크 결과 저장] → 콘솔 「체크본 가져오기」."""
    esc = html.escape
    body = "".join(
        f"<tr data-id='{esc(r['id'])}'><td class=c><input type=checkbox {'checked' if r['on'] else ''}></td>"
        f"<td class=c>{esc(r['id'])}</td><td class=s>{_where(r)}</td>"
        f"<td>{_pair(r)[0]}</td><td><span contenteditable class=new>{mark_pair(r['old'], r['new'])[1]}</span>{'' if r['new'] else '<span class=s>(삭제)</span>'}</td>"
        f"<td class=c>{r['p']:.0%}</td><td class=s>{esc(r['judge_type'])}<br>{esc(r['reason'])}</td>"
        f"<td class=memo contenteditable>{esc(r['note'])}</td></tr>" for r in data)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{esc(book)} 정오표 검토</title>
<style>{CSS}
.bar{{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #ddd;margin-bottom:8px}}
button{{font:inherit;padding:4px 10px;margin-right:4px}}@media print{{.bar{{display:none}}}}</style></head><body>
<h2>{esc(book)} — 정오표 검토</h2>
<p class=s>{datetime.now():%Y-%m-%d %H:%M} · 후보 {len(data)}건 · 체크 = 반영. 수정 문구와 메모 칸은 눌러서 고칠 수 있습니다.
 다 고치면 [체크 결과 저장] → 받은 파일을 JEV 콘솔 ④ 정오 판정의 「체크본 가져오기」로 올리세요. 종이로 회의하려면 인쇄(PDF로 저장).</p>
<div class=bar><button onclick="save()">체크 결과 저장 (JSON)</button><button onclick="print()">인쇄 · PDF로 저장</button>
 <label><input type=checkbox id=only onchange="only()"> 체크한 것만 보기</label> <span id=n class=s></span></div>
<table><thead><tr><th>반영</th><th>ID</th><th>장 · 쪽 · 조문</th><th>기존 문구</th><th>수정 문구</th><th>확률</th><th>유형 · 사유</th><th>메모</th></tr></thead>
<tbody>{body}</tbody></table>
<script>
const rows=[...document.querySelectorAll('tr[data-id]')];
function count(){{document.getElementById('n').textContent='체크 '+rows.filter(r=>r.querySelector('input').checked).length+' / '+rows.length}}
document.addEventListener('change',count);count();
function only(){{const on=document.getElementById('only').checked;rows.forEach(r=>r.style.display=on&&!r.querySelector('input').checked?'none':'')}}
function save(){{const items=rows.map(r=>({{id:r.dataset.id,apply:r.querySelector('input').checked,
 new_text:r.querySelector('.new').innerText.trim(),note:r.querySelector('.memo').innerText.trim()}}));
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({{book:{json.dumps(book)},items}},null,1)],{{type:'application/json'}}));
 a.download='체크본_'+{json.dumps(book)}+'.json';a.click()}}
</script></body></html>"""


MEMO_PAD = "&#160;" * 40  # PDF 표는 내용 길이로 열 너비를 정한다 — 빈 메모칸도 손글씨 쓸 만큼 넓게


def review_pdf(path, book, data):
    """회의용 종이 정오표 (후보 전부, 체크칸 ☐/☑ · 빈 메모칸)."""
    import pymupdf
    esc = html.escape
    body = "".join(f"<tr><td class=c>{'☑' if r['on'] else '☐'}</td><td class=c>{esc(r['id'])}</td><td class=s>{_where(r)}</td>"
                   f"<td>{_pair(r)[0]}</td><td>{_pair(r)[1]}</td>"
                   f"<td class=c>{r['p']:.0%}</td><td class=s>{esc(r['judge_type'])}</td><td>{esc(r['note']) or MEMO_PAD}</td></tr>"
                   for r in data)
    doc = (f"<h3>{esc(book)} — 정오표 검토</h3><p class=s>{datetime.now():%Y-%m-%d} · 후보 {len(data)}건 · ☑ = 반영(지금 체크). "
           f"고칠 것은 체크칸에 표시하고 메모칸에 적어 주세요.</p>"
           "<table><tr><th width=4%>반영</th><th width=5%>ID</th><th width=12%>장 · 쪽 · 조문</th><th width=26%>기존 문구</th>"
           f"<th width=26%>수정 문구</th><th width=5%>확률</th><th width=7%>유형</th><th width=15%>메모</th></tr>{body}</table>")
    css = ("@font-face{font-family:malgun;src:url(malgun.ttf);}\n*{font-family:malgun}\n"
           "body{margin:0;font-size:9pt;color:#222}table{border-collapse:collapse;width:100%}"
           "th,td{border:1px solid #999;padding:3px 4px;vertical-align:top;text-align:left;font-size:9pt}"
           "th{background:#f1ece3}td.c{text-align:center}.s{color:#555;font-size:8pt}"
           "b.x{color:#9b1c10;background-color:#ffc4bb}b.y{color:#11602a;background-color:#a8ebb6}")
    story = pymupdf.Story(html=doc, user_css=css, archive=pymupdf.Archive("C:/Windows/Fonts"))
    page = pymupdf.paper_rect("a4-l")
    w = pymupdf.DocumentWriter(str(path))
    more = 1
    while more:
        dev = w.begin_page(page)
        more, _ = story.place(page + (28, 28, -28, -28))
        story.draw(dev)
        w.end_page()
    w.close()


def import_review(data, filename):
    """체크본(검토 HTML 에서 저장한 JSON · 정오표.xlsx 의 「전체 후보」 시트) → decisions.json 에 합친다."""
    rs, _ = rows()
    by_id = {r["id"]: r for r in rs}
    got = []
    if filename.lower().endswith(".json"):
        got = json.loads(data.decode("utf-8-sig")).get("items", [])
    elif filename.lower().endswith(".xlsx"):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True)["전체 후보"]
        head, *body = [list(r) for r in ws.iter_rows(values_only=True)]
        col = {h: k for k, h in enumerate(head)}
        for r in body:
            got.append({"id": str(r[col["ID"]]), "apply": str(r[col["반영"]] or "").strip().upper() in ("O", "○", "V", "Y", "1", "TRUE"),
                        "new_text": "" if r[col["수정 문구"]] == "(삭제)" else str(r[col["수정 문구"]] or ""),
                        "note": str(r[col["메모"]] or "")})
    else:
        raise ValueError("체크본은 검토 HTML 에서 저장한 .json 이나 정오표 .xlsx 를 올려 주세요.")
    dec = load_decisions()
    n = 0
    for it in got:
        r = by_id.get(it.get("id"))
        if not r:
            continue
        cur = {"apply": bool(it.get("apply"))}
        if it.get("new_text") is not None and it["new_text"] != r["new"]:
            cur["new_text"] = it["new_text"]
        if it.get("note"):
            cur["note"] = it["note"]
        dec[r["id"]] = cur
        n += 1
    save_decisions(dec)
    print(f"[체크본] {filename} → {n}건 반영 (못 맞춘 줄 {len(got) - n}건)")
    return n, len(got) - n, dec


def run(decisions=None):
    """체크한 대로 책 전체 정오표를 만든다."""
    if decisions is not None:
        save_decisions(decisions)
    dec = load_decisions()
    rs, chs = rows()
    if not rs:
        raise ValueError("아직 판정한 장이 없습니다. ② 장별 판정에서 판정을 먼저 돌리세요.")
    data = []
    for r in rs:
        on, new, note = edit_on(r, dec)
        data.append({**r, "on": bool(on), "new": new, "note": note, "status": ""})
    # 원고에서 위치 확인 (체크한 것만) — 장 원고 그대로 찾기, 못 찾으면 '수동 확인'
    for c in chs:
        mine = {r["rid"]: r for r in data if r["ch"] == c["id"]}
        _, _, records, _, _ = apply.resolve(c["id"], {k: {"apply": r["on"], "new_text": r["new"]} for k, r in mine.items()})
        for rec in records:
            if rec["id"] in mine and rec["apply"]:
                mine[rec["id"]]["status"] = rec["status"]
    on = [r for r in data if r["on"] and r["status"] != "변경없음"]
    book = book_name()
    DIR.mkdir(parents=True, exist_ok=True)
    made = []

    wb = Workbook()
    cols = [("ID", 8), ("장", 18), ("쪽", 6), ("관련 조문", 18), ("기존 문구", 46), ("수정 문구", 46), ("수정확률", 8),
            ("영향유형", 11), ("사유", 40), ("메모", 24), ("확인", 18)]
    pick = lambda r: [r["id"], f"{r['ch']} {r['ch_title']}", r["page"] or "", _art(r), *_rich(r), f"{r['p']:.0%}",
                      r["judge_type"], r["reason"], r["note"],
                      "" if not r["status"] or r["status"] in DONE else r["status"]]
    ws = wb.active
    ws.title = "정오표"
    _sheet(ws, cols, [pick(r) for r in on])
    # 「전체 후보」의 반영(O) · 수정 문구 · 메모를 고쳐 콘솔 「체크본 가져오기」로 올리면 그대로 반영된다
    _sheet(wb.create_sheet("전체 후보"), [("반영", 6)] + cols, [["O" if r["on"] else ""] + pick(r) for r in data])
    summary = [["책", book], ["만든 때", datetime.now().strftime("%Y-%m-%d %H:%M")],
               ["정오표", f"체크 {sum(r['on'] for r in data)}건 / 후보 {len(data)}건"],
               ["원고에서 직접 확인", f"{sum(1 for r in on if r['status'] and r['status'] not in DONE)}건 (정오표 시트 '확인' 칸)"]]
    for c in chs:
        mine = [r for r in data if r["ch"] == c["id"]]
        summary.append([f"{c['id']} {c['title']}", f"후보 {len(mine)} · 체크 {sum(r['on'] for r in mine)}"
                        + "".join(f"\n{l['name']}: {l.get('old_header', '')} → {l.get('new_header', '')}" for l in c["laws"])])
    _sheet(wb.create_sheet("요약"), [("항목", 26), ("내용", 90)], summary)
    wb.save(DIR / f"{book}_정오표.xlsx")
    made.append(f"{book}_정오표.xlsx")

    (DIR / f"{book}_정오표_게시판.html").write_text(board_html(book, chs, on), encoding="utf-8")
    (DIR / f"{book}_정오표_검토.html").write_text(review_html(book, data), encoding="utf-8")
    made += [f"{book}_정오표_검토.html", f"{book}_정오표_게시판.html"]
    try:
        review_pdf(DIR / f"{book}_정오표_검토.pdf", book, data)
        made.append(f"{book}_정오표_검토.pdf")
    except Exception as e:  # 글꼴이 없는 PC 등 — 검토 HTML 을 인쇄하면 같은 종이가 나온다
        print(f"  [정오표] PDF는 만들지 못했습니다 ({e}). 검토 HTML 을 열어 인쇄 → PDF로 저장하세요.")
    print(f"[정오표] {', '.join(made)} → {DIR}")
    return {"files": made, "checked": len(on), "total": len(data)}
