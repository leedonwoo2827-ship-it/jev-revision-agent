"""교재 PDF 전체 → 차례(장 목록) 추출.

1) PDF 책갈피(outline)가 있으면 그것을 쓴다. (파일 합본 흔적처럼 엉터리면 2로)
2) 없으면 앞쪽 CONTENTS 쪽을 읽어 장 제목을 얻고, 본문에서 'CHAPTER NN' 표지가
   처음 나오는 쪽을 실제 시작 쪽(PDF 쪽)으로 잡는다. (인쇄 쪽번호와 PDF 쪽이 달라도 됨)
3) [법령적용기준] 쪽을 찾아 장 제목과 같은 법령(법률·시행령)과 책 기준 버전을 붙인다.
   그 쪽이 없으면 본문에 실린 법령 원문 머리글('[시행 …] [법률 제N호 …]')에서 버전을 얻고,
   책 이름의 주 법령 묶음(법·시행령·시행규칙)을 장마다 기본으로 붙인다. 화면에서 바꿀 수 있다.
4) 그래도 못 찾으면 Codex에 각 쪽 첫 줄을 보여 주고 차례를 받는다 (codex_extract).
사람이 콘솔에서 고친 뒤 [차례 확정] 하면 장 워크스페이스가 만들어진다.
"""
import re
from pathlib import Path

import fitz

CH_RE = re.compile(r"CHAPTER\s*(\d{1,2})")
VER_RE = re.compile(r"\[(법률|대통령령|총리령|[^\]\s]*부령)\s*제\s*(\d+)\s*호\s*,\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.")
HEAD_RE = re.compile(r"\[시행[^\]]*\]\s*\[[^\]]*\]")


def _pages(doc):
    return [p.get_text() for p in doc]


def find_contents(texts, limit=30):
    """차례 쪽 번호들 (여러 쪽에 걸치면 이어지는 쪽까지). 없으면 []."""
    for i, t in enumerate(texts[:limit]):
        if re.search(r"^\s*(CONTENTS|차\s*례|목\s*차)\s*$", t, re.M) and CH_RE.search(t):
            pages = [i]
            # 다음 쪽이 'CHAPTER NN' 으로 시작하고 점선 쪽번호 줄이 있으면 차례가 이어지는 것
            while pages[-1] + 1 < len(texts):
                nxt = texts[pages[-1] + 1].lstrip()
                if re.match(r"CHAPTER\s*\d{1,2}\s*\n", nxt) and re.search(r"[·.…]{5,}\s*\d{1,4}\s*$", nxt, re.M):
                    pages.append(pages[-1] + 1)
                else:
                    break
            return pages
    return []


# 차례에서 장 제목 다음에 오는 하위 항목 (제목이 여기서 끝난다)
SUB_RE = re.compile(r"^(제\s*\d+\s*[절장편]|\d{2}\s|기출문제|출제예상문제|핵심|단원)")
LEADER_RE = re.compile(r"\s*[·.…]{3,}\s*\d{1,4}\s*$")


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
                if re.fullmatch(r"CHAPTER\s*\d{1,2}", l) or l == "부록":
                    break
                if title and SUB_RE.match(l):  # '제1절 …·····2' — 첫 하위 항목의 쪽번호를 장 쪽번호로
                    lm = re.search(r"(\d{1,4})\s*$", l)
                    printed = int(lm.group(1)) if lm and LEADER_RE.search(l) else printed
                    j += 1
                    while j < len(lines) and not (re.fullmatch(r"CHAPTER\s*\d{1,2}", lines[j]) or lines[j] == "부록"):
                        j += 1
                    break
                inline = re.match(r"^(.*?)(\d{1,4})$", LEADER_RE.sub(lambda x: re.sub(r"[^\d]", "", x.group(0)), l))
                if re.fullmatch(r"\d{1,4}", l):
                    printed = int(l); j += 1; break
                if inline and inline.group(1) and not re.fullmatch(r"CHAPTER\s*", inline.group(1)):
                    title.append(inline.group(1)); printed = int(inline.group(2)); j += 1; break
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
        return {"page": i + 1, "laws": laws, "source": "법령적용기준"}
    return {"page": None, "laws": [], "source": None}


EF_RE = re.compile(r"\[시행\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.")


def _version(s):
    """'[시행 2025. 1. 31.] [법률 제20645호, 2025. 1. 7.]' → '20645,20250107@20250131'
    (같은 공포번호에 시행일이 여럿인 개정이 있어, 책이 실은 시행일까지 기억해 둔다)"""
    v = VER_RE.search(s)
    if not v:
        return ""
    e = EF_RE.search(s)
    ef = f"@{e.group(1)}{int(e.group(2)):02d}{int(e.group(3)):02d}" if e else ""
    return f"{v.group(2)},{v.group(3)}{int(v.group(4)):02d}{int(v.group(5)):02d}{ef}"


def parse_law_headers(texts):
    """[법령적용기준] 쪽이 없는 책: 본문(관련법령 부록 등)에 실린 법령 원문의
    '법령명 / [시행 …] [법률 제N호, …]' 머리글에서 책 기준 버전을 얻는다."""
    laws, seen = [], set()
    for i, t in enumerate(texts):
        lines = [l.strip() for l in t.splitlines()]
        for k, s in enumerate(lines):
            m = HEAD_RE.search(s)
            if not m or not s.startswith("[시행 ") or k == 0:
                continue
            name = re.sub(r"^\d{1,2}\s+", "", lines[k - 1]).strip()
            if not re.search(r"(법|령|규칙)$", name) or len(name) > 40 or name in seen:
                continue
            seen.add(name)
            laws.append({"name": name, "header": m.group(0), "line": f"{name} {m.group(0)}",
                         "version": _version(s), "page": i + 1, "checked": True})
    return laws


def law_family(name):
    """'경비업법 시행규칙' → ('경비업법', '칙')"""
    n = name.replace(" ", "")
    for suf, ab in (("시행규칙", "칙"), ("시행령", "영")):
        if n.endswith(suf) and len(n) > len(suf):
            return n[:-len(suf)], ab
    return n, "법"


def scan_laws(texts, known=()):
    """책 본문에 나오는 법령 이름과 언급 횟수. known 에 없는 것은 체크 해제 상태로 제안."""
    body = "\n".join(texts)
    cnt = {}
    for m in re.finditer(r"「\s*([^」\n]{2,30}?(?:법|령|규칙))\s*」", body):
        n = m.group(1).strip()
        cnt[n] = cnt.get(n, 0) + 1
    fams = {law_family(k)[0] for k in known}
    out = []
    for n, c in sorted(cnt.items(), key=lambda x: -x[1]):
        if n.replace(" ", "") in {k.replace(" ", "") for k in known}:
            continue
        out.append({"name": n, "header": "", "line": "", "version": "", "page": None,
                    "checked": law_family(n)[0] in fams, "mentions": c})
    return out[:30]


def build_baseline(texts):
    """[법령적용기준] → 없으면 법령 원문 머리글. 둘 다 없으면 빈 목록 (화면에서 직접 넣기)."""
    bl = parse_baseline(texts)
    if not bl["laws"]:
        heads = parse_law_headers(texts)
        if heads:
            bl = {"page": heads[0]["page"], "laws": heads, "source": "법령 원문 머리글"}
    for law in bl["laws"]:
        law.setdefault("checked", True)
    bl["suggest"] = scan_laws(texts, [l["name"] for l in bl["laws"]])
    return bl


def _law_ref(law, abbr):
    return {"name": law["name"], "abbr": abbr, "fetch_name": law["name"].replace(" ", ""),
            "old_version": law.get("version", ""), "old_header": law.get("header", "")}


def laws_for(names, baseline):
    """법령 이름 목록 → 장 laws (법/영/칙 약칭, 묶음이 둘 이상이면 법2/영2/칙2 …)."""
    by = {l["name"]: l for l in baseline.get("laws", [])}
    fams = []
    for n in names:
        f = law_family(n)[0]
        if f not in fams:
            fams.append(f)
    out = []
    for n in names:
        if n not in by:
            continue
        f, ab = law_family(n)
        k = fams.index(f)
        out.append(_law_ref(by[n], ab + (str(k + 1) if k else "")))
    order = {"법": 0, "영": 1, "칙": 2}
    out.sort(key=lambda l: (len(l["abbr"]), order.get(l["abbr"][0], 9), l["abbr"]))
    return out


def attach_laws(entries, baseline, book_name=""):
    laws = [l for l in baseline["laws"] if l.get("checked", True)]
    fam_of = {l["name"]: law_family(l["name"])[0] for l in laws}
    fams = list(dict.fromkeys(fam_of.values()))
    # 책 이름에 들어 있는 법령 묶음 = 주 법령 (예: '경비지도사_2차_경비업법' → 경비업법)
    book_key = re.sub(r"[\s_]", "", book_name)
    main = next((f for f in sorted(fams, key=len, reverse=True) if f in book_key), None)
    for e in entries:
        tt = e["title"].replace(" ", "")
        if baseline.get("source") == "법령적용기준" or not baseline.get("source"):
            # 기존 방식: 장 제목 == 법령명 (+ 시행령)
            names = [l["name"] for l in laws if l["name"].replace(" ", "") in (tt, tt + "시행령")]
        elif tt in fams:
            names = [n for n, f in fam_of.items() if f == tt]
        elif re.search(r"관련\s*법령|법령\s*원문", e["title"]):
            names = list(fam_of)
        elif re.search(r"기출|모의|부록", e["title"]) or e.get("kind") == "appendix":
            names = []
        else:
            names = [n for n, f in fam_of.items() if f == main]
        e["laws"] = laws_for(names, baseline)


def _bad_outline(outline):
    """파일 합본 흔적 같은 엉터리 책갈피: 3개 미만이거나 CHAPTER 번호가 하나도 없음."""
    return len(outline) < 3 or not any(CH_RE.search(t) for _, t, _ in outline)


def extract(pdf_path):
    doc = fitz.open(pdf_path)
    texts = _pages(doc)
    baseline = build_baseline(texts)
    method, entries = None, []

    outline = [x for x in doc.get_toc() if x[0] == 1]
    cis = find_contents(texts)
    if outline and not (cis and _bad_outline(outline)):
        method = "outline"
        for k, (_, title, page) in enumerate(outline):
            nxt = outline[k + 1][2] - 1 if k + 1 < len(outline) else len(texts)
            m = CH_RE.search(title)
            entries.append({"no": int(m.group(1)) if m else k + 1, "kind": "chapter",
                            "title": CH_RE.sub("", title).strip(), "printed": page, "start": page, "end": nxt})
    elif cis:
        method = "contents_skip_outline" if outline else "contents"
        bl_page = baseline["page"] if baseline.get("source") == "법령적용기준" else None
        entries = locate(texts, parse_contents("\n".join(texts[i] for i in cis)),
                         after=max(cis[-1], (bl_page or 1) - 1))

    attach_laws(entries, baseline, Path(pdf_path).stem)
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
    baseline = build_baseline(texts)
    entries = [dict(e, printed=None) for e in res["chapters"]]
    attach_laws(entries, baseline, Path(pdf_path).stem)
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
