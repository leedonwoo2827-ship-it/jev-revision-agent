"""변경 목록 만들기 (LLM 없이 결정적).

- 법령 방식: 구판(책 기준) vs 현행 법령을 조문·장 단위로 비교
- 개정기본서 방식: 우리 장을 몇 쪽씩 구간으로 나눠 개정기본서와 대조할 단위를 만든다

산출: <장>/02_변경목록/changes.json
"""
import difflib
import json
import re

from . import config
from .extract_book import page_texts
from .extract_law import norm, parse

WINDOW = 4  # 개정기본서 방식: 한 번에 대조할 우리 책 쪽 수


def article_sort_key(k):
    m = re.match(r"제(\d+)(조|장|절)(?:의(\d+))?", k)
    return ({"장": 0, "절": 1, "조": 2}[m.group(2)], int(m.group(1)), int(m.group(3) or 0)) if m else (9, 0, 0)


def run_law(cid, c):
    changes, laws, missing = [], [], []
    for law in c.get("laws", []):
        old_p = config.source(cid, f"law_old_{law['abbr']}")
        new_p = config.source(cid, f"law_new_{law['abbr']}")
        if not old_p or not new_p:
            missing += [f"{law['name']} {w}" for w, f in (("구(책 기준)", old_p), ("현행", new_p)) if not f]
            continue
        old, new = parse(old_p), parse(new_p)
        laws.append({"name": law["name"], "abbr": law["abbr"], "old_header": old["header"],
                     "new_header": new["header"], "old_file": old["file"], "new_file": new["file"],
                     "old_count": len(old["articles"]), "new_count": len(new["articles"]),
                     "new_addenda": new["addenda"]})
        keys = sorted(set(old["articles"]) | set(new["articles"]), key=article_sort_key)
        for k in keys:
            a, b = old["articles"].get(k), new["articles"].get(k)
            if a and b and norm(a["text"]) == norm(b["text"]):
                continue
            if a and b and a.get("deleted") and b.get("deleted"):
                continue
            if not a or (a.get("deleted") and b and not b.get("deleted")):
                kind = "신설"
            elif not b or b.get("deleted"):
                kind = "삭제"
            else:
                kind = "변경"
            diff = "\n".join(difflib.unified_diff(
                (a["text"] if a else "").splitlines(), (b["text"] if b else "").splitlines(),
                "구", "신", lineterm="", n=1))
            changes.append({"id": f"{law['abbr']}-{k}", "law": law["name"], "abbr": law["abbr"], "article": k,
                            "title": (b or a)["title"], "kind": kind,
                            "old": a["text"] if a else "", "new": b["text"] if b else "", "diff": diff})
    if not c.get("laws"):
        missing.append("이 장에 연결된 법령이 없습니다 (차례 화면에서 법령을 확인하거나 개정기본서 방식을 쓰세요)")
    for l in laws:
        print(f"[비교] {l['name']}: 구 {l['old_header']} → 신 {l['new_header']}")
    return {"mode": "law", "laws": laws, "changes": changes}, missing


def run_book(cid, c):
    ref = config.source(cid, "ref_book")
    if not ref:
        return {"mode": "book", "laws": [], "changes": []}, ["신규 개정기본서 PDF"]
    import fitz
    n = fitz.open(ref).page_count
    rp = c.get("ref_pages") or [1, n]
    if rp[1] - rp[0] + 1 > 80:
        return {"mode": "book", "laws": [], "changes": []}, \
            [f"개정기본서가 {n}쪽입니다. 이 장에 해당하는 쪽 범위를 지정해 주세요 (80쪽 이하)"]
    _, ref_pages = page_texts(ref, rp[0], rp[1])
    ref_text = "".join(f"\n[기본서 p.{p}]\n{t}" for p, t in ref_pages)
    first, last = c["pages"]
    changes = []
    for s in range(first, last + 1, WINDOW):
        e = min(s + WINDOW - 1, last)
        changes.append({"id": f"p{s}-{e}", "law": "개정기본서", "abbr": "기본서", "article": f"p.{s}–{e}",
                        "title": "", "kind": "대조", "pages": [s, e], "old": "", "new": "", "diff": ""})
    src = {"name": "신규 개정기본서", "abbr": "기본서", "old_header": "우리 책", "new_header": f"{ref.name} p.{rp[0]}–{rp[1]}",
           "ref_text": ref_text}
    print(f"[비교] 개정기본서 {ref.name} p.{rp[0]}-{rp[1]} ↔ 우리 책 p.{first}-{last} ({len(changes)}구간)")
    return {"mode": "book", "laws": [src], "changes": changes}, []


def run(cid):
    c = config.chapter(cid)
    data, missing = (run_book if c.get("mode") == "book" else run_law)(cid, c)
    if missing:
        print("[비교] 비교자료가 모자랍니다:")
        for m in missing:
            print("   -", m)
    out = config.stage(cid, "02_변경목록")
    (out / "changes.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[비교] 대조 단위 {len(data['changes'])}건 → {out / 'changes.json'}")
    return data["laws"], data["changes"], missing


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else "09")
