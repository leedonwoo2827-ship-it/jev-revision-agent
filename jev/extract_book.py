"""교재 PDF에서 장 범위만 잘라 쪽 단위 텍스트로 저장한다.

산출: work/<ch>/<ch>.pdf, <ch>.txt, book.json, baseline.json
"""
import json
import re

import fitz

from . import config

LAW_REF = re.compile(r"(법|영|시행령)\s*제(\d+)조(?:의(\d+))?")


def run(ch):
    cfg = config.load()
    c = config.chapter(cfg, ch)
    src = config.ROOT / cfg["book_pdf"]
    if not src.exists():
        raise SystemExit(f"교재 PDF가 없습니다: {src}")
    out = config.workdir(ch)
    first, last = c["pages"]

    doc = fitz.open(src)
    part = fitz.open()
    part.insert_pdf(doc, from_page=first - 1, to_page=last - 1)
    part.save(out / f"{ch}.pdf")

    pages = []
    for p in range(first, last + 1):
        text = doc[p - 1].get_text()
        refs = sorted({f"{'영' if m.group(1) != '법' else '법'} 제{m.group(2)}조" + (f"의{m.group(3)}" if m.group(3) else "")
                       for m in LAW_REF.finditer(text)})
        pages.append({"page": p, "text": text, "law_refs": refs})

    with open(out / "book.json", "w", encoding="utf-8") as f:
        json.dump({"chapter": ch, "title": c["title"], "pages": pages}, f, ensure_ascii=False, indent=1)
    with open(out / f"{ch}.txt", "w", encoding="utf-8") as f:
        for pg in pages:
            f.write(f"\n[p.{pg['page']}]\n{pg['text']}")

    # [법령적용기준] 쪽에서 이 장 법령 줄만 뽑아 둔다 (규칙 기반 수정 1건용)
    bp = cfg.get("baseline_page")
    base = []
    if bp:
        names = [law["name"] for law in c["laws"]]
        for line in doc[bp - 1].get_text().splitlines():
            s = line.strip()
            for name in names:
                if s.startswith(name) and "[시행" in s and s[len(name):].lstrip().startswith("["):
                    base.append({"page": bp, "law": name, "line": s})
    with open(out / "baseline.json", "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=1)

    print(f"[book] {ch} p.{first}-{last} ({len(pages)}쪽) 추출, 법령적용기준 {len(base)}줄 → {out}")
    return pages


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else "ch09")
