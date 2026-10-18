"""교재 PDF 전체 → 차례(장 목록) 추출.

1) PDF 책갈피(outline)가 있으면 그것을 쓴다.
2) 없으면 앞쪽 CONTENTS 쪽을 읽어 장 제목을 얻고, 본문에서 'CHAPTER NN' 표지가
   처음 나오는 쪽을 실제 시작 쪽(PDF 쪽)으로 잡는다. (인쇄 쪽번호와 PDF 쪽이 달라도 됨)
3) [법령적용기준] 쪽을 찾아 장 제목과 같은 법령(법률·시행령)과 책 기준 버전을 붙인다.
4) 그래도 못 찾으면 Codex에 각 쪽 첫 줄을 보여 주고 차례를 받는다 (codex_extract).
사람이 콘솔에서 고친 뒤 [차례 확정] 하면 장 워크스페이스가 만들어진다.
"""
import re

import fitz

CH_RE = re.compile(r"CHAPTER\s*(\d{1,2})")
VER_RE = re.compile(r"\[(법률|대통령령|총리령|[^\]\s]*부령)\s*제\s*(\d+)\s*호\s*,\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.")
HEAD_RE = re.compile(r"\[시행[^\]]*\]\s*\[[^\]]*\]")


def _pages(doc):
    return [p.get_text() for p in doc]


def find_contents(texts, limit=30):
    for i, t in enumerate(texts[:limit]):
        if re.search(r"^\s*(CONTENTS|차\s*례|목\s*차)\s*$", t, re.M) and CH_RE.search(t):
            return i
    return None


def parse_contents(text):
    """CONTENTS 쪽 텍스트 → [{no, title, printed}] (+ 부록)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out, i = [], 0
    while i < len(lines):
        m = re.fullmatch(r"CHAPTER\s*(\d{1,2})", lines[i])
        if m or lines[i] == "부록":
            title, j, printed = [], i + 1, None
            while j < len(lines):
                l = lines[j]
                inline = re.match(r"^(.*?)(\d{1,4})$", l)  # '…특별법266' 처럼 붙은 쪽번호
                if re.fullmatch(r"\d{1,4}", l):
                    printed = int(l); j += 1; break
                if inline and inline.group(1) and not re.fullmatch(r"CHAPTER\s*", inline.group(1)):
                    title.append(inline.group(1)); printed = int(inline.group(2)); j += 1; break
                if re.fullmatch(r"CHAPTER\s*\d{1,2}", l) or l == "부록":
                    break
                title.append(l); j += 1
            out.append({"no": int(m.group(1)) if m else None, "kind": "chapter" if m else "appendix",
                        "title": " ".join(title).replace("  ", " ").strip(), "printed": printed})
            i = j
        else:
            i += 1
    return out


def locate(texts, entries, after):
    """각 장의 실제 PDF 시작 쪽 찾기."""
    n = len(texts)
    for e in entries:
        e["start"] = None
        for i in range(after + 1, n):
            t = texts[i]
            if e["kind"] == "chapter":
                if any(int(x) == e["no"] for x in CH_RE.findall(t)):
                    e["start"] = i + 1
                    break
            elif re.match(r"^\s*부\s*록", t) and (not e["title"] or e["title"].split()[0] in t):
                e["start"] = i + 1
                break
    found = [e for e in entries if e["start"]]
    for k, e in enumerate(found):
        end = (found[k + 1]["start"] - 1) if k + 1 < len(found) else n
        while end > e["start"] and not texts[end - 1].strip():  # 끝의 빈 쪽 제외
            end -= 1
        e["end"] = end
    return found


def parse_baseline(texts, limit=40):
    """[법령적용기준] 쪽 → {page, laws:[{name, header, kind, number, date}]}"""
    for i, t in enumerate(texts[:limit]):
        if "법령적용기준" not in t:
            continue
        laws = []
        for line in t.splitlines():
            s = line.strip()
            m = HEAD_RE.search(s)
            if not m:
                continue
            name = s[:m.start()].strip()
            v = VER_RE.search(s)
            laws.append({"name": name, "header": m.group(0), "line": s,
                         "version": f"{v.group(2)},{v.group(3)}{int(v.group(4)):02d}{int(v.group(5)):02d}" if v else ""})
        return {"page": i + 1, "laws": laws}
    return {"page": None, "laws": []}


def attach_laws(entries, baseline):
    for e in entries:
        e["laws"] = []
        for law in baseline["laws"]:
            nm = law["name"].replace(" ", "")
            tt = e["title"].replace(" ", "")
            if nm == tt or nm == tt + "시행령":
                decree = nm != tt
                e["laws"].append({"name": law["name"], "abbr": "영" if decree else "법",
                                  "fetch_name": nm, "old_version": law["version"],
                                  "old_header": law["header"]})
        e["laws"].sort(key=lambda l: l["abbr"] != "법")


def extract(pdf_path):
    doc = fitz.open(pdf_path)
    texts = _pages(doc)
    baseline = parse_baseline(texts)
    method, entries = None, []

    outline = [x for x in doc.get_toc() if x[0] == 1]
    if outline:
        method = "outline"
        for k, (_, title, page) in enumerate(outline):
            nxt = outline[k + 1][2] - 1 if k + 1 < len(outline) else len(texts)
            m = CH_RE.search(title)
            entries.append({"no": int(m.group(1)) if m else k + 1, "kind": "chapter",
                            "title": CH_RE.sub("", title).strip(), "printed": page, "start": page, "end": nxt})
    else:
        ci = find_contents(texts)
        if ci is not None:
            method = "contents"
            entries = locate(texts, parse_contents(texts[ci]), after=max(ci, (baseline["page"] or 1) - 1))

    attach_laws(entries, baseline)
    chapters = []
    app_no = max([e["no"] or 0 for e in entries] + [0])
    for e in entries:
        if e["kind"] == "appendix":
            app_no += 1
            e["no"] = app_no
        chapters.append({"id": f"{e['no']:02d}", "no": e["no"], "kind": e["kind"], "title": e["title"] or "부록",
                         "printed": e.get("printed"), "pages": [e["start"], e["end"]],
                         "include": e["kind"] == "chapter", "laws": e["laws"]})
    return {"book": pdf_path.name, "page_count": len(texts), "method": method or "none",
            "baseline": baseline, "chapters": chapters, "confirmed": False}


CODEX_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["chapters"],
    "properties": {"chapters": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["no", "kind", "title", "start", "end"],
        "properties": {"no": {"type": "integer"}, "kind": {"type": "string", "enum": ["chapter", "appendix"]},
                       "title": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}}}},
}


def codex_extract(pdf_path):
    """책갈피·CONTENTS로 못 찾을 때: 각 쪽 첫 줄들을 Codex에 보여 주고 차례를 받는다."""
    from .judge_codex import ask_codex
    doc = fitz.open(pdf_path)
    texts = _pages(doc)
    lines = []
    for i, t in enumerate(texts):
        head = " | ".join(l.strip() for l in t.splitlines() if l.strip())[:110]
        lines.append(f"p{i + 1}: {head}")
    prompt = ("아래는 교재 PDF의 각 쪽 첫머리다. 본문 장(chapter)과 부록의 차례를 뽑아라. "
              "start/end 는 PDF 쪽 번호(p숫자)다. 표지·머리말·차례 쪽은 제외. 파일을 읽거나 명령을 실행하지 마라.\n\n"
              + "\n".join(lines))
    res = ask_codex(prompt, schema=CODEX_SCHEMA)
    baseline = parse_baseline(texts)
    entries = [dict(e, printed=None) for e in res["chapters"]]
    attach_laws(entries, baseline)
    chapters = [{"id": f"{e['no']:02d}", "no": e["no"], "kind": e["kind"], "title": e["title"],
                 "printed": None, "pages": [e["start"], e["end"]], "include": e["kind"] == "chapter",
                 "laws": e["laws"]} for e in entries]
    return {"book": pdf_path.name, "page_count": len(texts), "method": "codex",
            "baseline": baseline, "chapters": chapters, "confirmed": False}


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    r = extract(Path(sys.argv[1]))
    print(json.dumps({k: v for k, v in r.items() if k != "baseline"}, ensure_ascii=False, indent=1))
