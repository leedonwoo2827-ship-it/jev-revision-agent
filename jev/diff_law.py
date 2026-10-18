"""구판(책 기준) vs 현행 법령 조문 diff. LLM 없이 결정적으로 계산한다.

산출: work/<ch>/changes.json
"""
import difflib
import json

from . import config
from .extract_law import norm, parse


def article_sort_key(k):
    import re
    m = re.match(r"제(\d+)(조|장|절)(?:의(\d+))?", k)
    return ({"장": 0, "절": 1, "조": 2}[m.group(2)], int(m.group(1)), int(m.group(3) or 0)) if m else (9, 0, 0)


def run(ch):
    cfg = config.load()
    c = config.chapter(cfg, ch)
    out = config.workdir(ch)
    changes, laws, missing = [], [], []

    for law in c["laws"]:
        old_p, new_p = config.resolve(law["old"]), config.resolve(law["new"])
        if not old_p or not new_p:
            missing += [p for p, f in ((law["old"], old_p), (law["new"], new_p)) if not f]
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
            changes.append({
                "id": f"{law['abbr']}-{k}",
                "law": law["name"], "abbr": law["abbr"], "article": k,
                "title": (b or a)["title"], "kind": kind,
                "old": a["text"] if a else "", "new": b["text"] if b else "", "diff": diff,
            })

    if missing:
        print("[law] _context 에 아래 법령 파일이 없습니다 (README '넣을 파일' 참고):")
        for m in missing:
            print("   -", m)
    with open(out / "changes.json", "w", encoding="utf-8") as f:
        json.dump({"laws": laws, "changes": changes}, f, ensure_ascii=False, indent=1)
    for l in laws:
        print(f"[law] {l['name']}: 구 {l['old_count']}조 {l['old_header']} → 신 {l['new_count']}조 {l['new_header']}")
    print(f"[law] 변경 조문 {len(changes)}건 → {out / 'changes.json'}")
    return laws, changes, missing


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else "ch09")
