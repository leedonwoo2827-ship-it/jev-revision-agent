"""교재 PDF에서 장 범위만 잘라 쪽 단위 텍스트로 저장한다.

산출: <장>/00_원고/  ch<NN>.pdf, ch<NN>.txt, book.json, baseline.json
"""
import json
import re

import fitz

from . import config

LAW_REF = re.compile(r"(법|영|시행령)\s*제(\d+)조(?:의(\d+))?")


def page_texts(pdf, first, last):
    doc = fitz.open(pdf)
    last = min(last, doc.page_count)
    return doc, [(p, doc[p - 1].get_text()) for p in range(first, last + 1)]


def run(cid):
    c = config.chapter(cid)
    src = config.book_pdf()
    if not src:
        raise SystemExit("교재 PDF가 없습니다. 먼저 [책 넣기]에서 올려 주세요.")
    out = config.stage(cid, "00_원고")
    first, last = c["pages"]

    doc, texts = page_texts(src, first, last)
    part = fitz.open()
    part.insert_pdf(doc, from_page=first - 1, to_page=last - 1)
    part.save(out / f"ch{cid}.pdf")

    pages = []
    for p, text in texts:
        refs = sorted({f"{'법' if m.group(1) == '법' else '영'} 제{m.group(2)}조" + (f"의{m.group(3)}" if m.group(3) else "")
                       for m in LAW_REF.finditer(text)})
        pages.append({"page": p, "text": text, "law_refs": refs})
    (out / "book.json").write_text(json.dumps({"chapter": cid, "title": c["title"], "pages": pages},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / f"ch{cid}.txt", "w", encoding="utf-8") as f:
        for pg in pages:
            f.write(f"\n[p.{pg['page']}]\n{pg['text']}")

    # [법령적용기준] 중 이 장 법령 줄 (규칙 기반 수정용)
    toc = config.toc() or {}
    bl = toc.get("baseline", {})
    names = {law["name"] for law in c.get("laws", [])}
    base = [{"page": bl["page"], "law": l["name"], "line": l["line"]}
            for l in bl.get("laws", []) if l["name"] in names]
    (out / "baseline.json").write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[원고] {cid} {c['title']} p.{first}-{last} ({len(pages)}쪽) 추출 → {out}")
    return pages


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else "09")
