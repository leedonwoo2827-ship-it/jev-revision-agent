"""AI로 장별 법령 찾기 — 장 제목이 법 이름이 아닌 장의 '비교할 법령(분모)'을 제안한다.

1. 규칙(무료): 장 본문에서 「…법」 · 「…법률」 · '상법 제382조'처럼 이름·조문이 직접 나온 법령을 센다.
2. 엔진(장마다 1번): 이름이 안 나와도 내용상 기대는 법령까지 찾게 한다 (확신도 · 근거 쪽 · 이유).
3. 법제처 검증: 실제로 있는 법령만 남기고, 현행 시행일이 책 기준일 뒤인지(=책 이후 개정)와
   책 기준일에 시행 중이던 판을 붙인다.
4. 결과는 toc.json 차례의 장마다 law_suggest 로 — ③ 차례 확인에 「제안」 칩으로 뜬다.
   「확신 0.8 이상 + 책 이후 개정됨」은 이 책의 법령 목록에 넣고 그 장에 미리 연결해 둔다(③에서 끌 수 있음).

엔진 결과는 00_책/law_scan/ 에 장 본문 해시로 저장 — 다시 눌러도 같은 장은 다시 부르지 않는다.
"""
import hashlib
import json
import re

import fitz

from . import config, fetch_law, toc

AUTO_ON = 0.8      # 이 확신도 이상 + 책 이후 개정 → 미리 연결
MAX_CHARS = 80000  # 엔진에 보낼 장 본문 상한 (넘으면 쪽을 고르게 덜어 냄)

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["laws"],
    "properties": {"laws": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "confidence", "pages", "reason"],
        "properties": {
            "name": {"type": "string"},
            "confidence": {"type": "number"},
            "pages": {"type": "array", "items": {"type": "integer"}},
            "reason": {"type": "string"},
        }}}},
}

PROMPT = """너는 수험서 법령 교재의 개정 검수 편집자다. 아래 [교재 장]이 내용상 기대고 있는 대한민국 법령을 찾아라.
목적: 법령이 개정되면 이 장의 서술·문제·해설이 틀려질 수 있는지 점검할 '비교 대상 법령' 목록을 만드는 것.

규칙:
1. name 은 법제처 정식 명칭으로 쓴다 (예: 상법, 상법 시행령, 자본시장과 금융투자업에 관한 법률, 경찰관 직무집행법).
   약칭·'법'·'동법'·'영'·'규칙'만 쓰지 마라. 교재의 '법 제5조' 같은 약칭은 문맥으로 정식 명칭을 정한다.
2. 이 장이 그 법령의 조문 내용(요건·기간·금액·절차·정의·벌칙 등)을 직접 서술하거나 문제·해설로 묻는 법령만 낸다.
   이름이 안 나와도 내용이 그 법령에 기대면 포함한다 (예: '이사의 충실의무' 서술 → 상법).
3. 결격사유 등에서 '「형법」 제○조를 위반하여'처럼 이름만 인용되는 법령은 confidence 를 0.3 이하로.
4. 시행령·시행규칙의 내용(세부 기준·서식·금액 등)을 서술하면 그것도 따로 항목으로 낸다.
5. confidence = 이 장이 그 법령 개정에 영향을 받을 가능성(0~1). pages = 근거 쪽 번호(최대 5개, [p.N] 표시 기준).
   reason = 한 문장.
6. 파일을 읽거나 명령을 실행하지 말고, 이 프롬프트만으로 답하라.

이미 이 장에 연결된 법령: {linked}
규칙으로 센 인용 (이름: 횟수): {mentions}

[교재 장: {title} · p.{s}–{e}]
{text}
"""

STOP = {"법", "동법", "이법", "같은법", "본법", "당해법", "해당법", "영", "규칙", "시행령", "시행규칙", "법률",
        "특별법", "일반법", "개정법", "구법", "신법", "현행법", "관계법", "관련법", "관계법령", "법령", "위법", "적법"}
BRACKET_RE = re.compile(r"「\s*([^」\n]{1,40}?(?:법률|법|령|규칙))\s*」")  # 「형법」 같은 두 글자도
# 괄호 없이 '상법 제382조' · '청원경찰법 시행령 제3조'처럼 쓴 것 — 이름에 띄어쓰기는 시행령·시행규칙 앞만 허용
# (띄어 쓴 긴 이름은 교재에서 보통 「」로 묶고, 잘못 잡힌 것은 법제처 검증에서 빠진다)
BARE_RE = re.compile(r"(?<![가-힣ㆍ])([가-힣ㆍ]{1,30}(?:법률|법)(?:\s?시행령|\s?시행규칙)?)\s*제\s*\d+\s*조")

def _norm(n):
    n = re.sub(r"\s*[․·・ㆍ]\s*", "ㆍ", n.strip())
    return " ".join(n.split())


def mentions(text):
    """장 본문에 이름·조문이 직접 나온 법령과 횟수."""
    cnt = {}
    for rx in (BRACKET_RE, BARE_RE):
        for m in rx.finditer(text):
            n = _norm(m.group(1))
            if n.replace(" ", "") in STOP or len(n.replace(" ", "")) < 2:
                continue
            cnt[n] = cnt.get(n, 0) + 1
    return dict(sorted(cnt.items(), key=lambda x: -x[1]))


def eligible(e, fams):
    """제목이 법 이름이 아닌 장만 (관련법령 · 부록 · 제외한 장은 건너뜀)."""
    if not e.get("include"):
        return False
    tt = e["title"].replace(" ", "")
    if re.search(r"관련\s*법령|법령\s*원문|부록", e["title"]) or e.get("kind") == "appendix":
        return False
    return tt not in fams and not any(tt in (f, f + "시행령", f + "시행규칙") for f in fams)


def _chapter_text(doc, s, e):
    pages = [(p, doc[p - 1].get_text()) for p in range(s, min(e, doc.page_count) + 1)]
    total = sum(len(t) for _, t in pages)
    if total > MAX_CHARS:  # 고르게 덜어 냄
        step = total / MAX_CHARS
        pages = pages[::max(1, round(step))]
    return "".join(f"\n[p.{p}]\n{t}" for p, t in pages)


def _check(name, date, memo):
    """법제처 검증 → {exists, cur_header, cur_ef, changed, version, note}."""
    if name in memo:
        return memo[name]
    try:
        hist = fetch_law.history(name)
    except (SystemExit, OSError) as e:
        memo[name] = {"exists": False, "note": str(e)}
        return memo[name]
    cur = fetch_law.current(hist)
    ver, h = fetch_law.version_at(name, date, hist) if date else (None, None)
    memo[name] = {"exists": bool(hist), "cur_header": cur["header"] if cur else "", "cur_ef": cur["ef"] if cur else "",
                  "changed": bool(date and cur and cur["ef"] > date),
                  "version": ver or "", "header": h["header"] if h else "",
                  "note": "" if ver or not date else "책 기준일엔 아직 없던 법령"}
    return memo[name]


def _key(e):
    return f"{e.get('no')}|{e['title']}"


def run():
    from . import llm
    t = config.toc()
    pdf = config.book_pdf()
    if not t or not pdf:
        raise SystemExit("먼저 교재 PDF를 올리고 차례를 확인하세요.")
    bl = t.setdefault("baseline", {"page": None, "laws": [], "source": None})
    date = toc.book_date(bl)
    fams = {toc.law_family(l["name"])[0] for l in bl.get("laws", [])}
    targets = [e for e in t["chapters"] if eligible(e, fams)]
    if not targets:
        print("[법령 찾기] 제목이 법 이름이 아닌 장이 없습니다. (관련법령 · 부록 · 제외한 장은 건너뜁니다)")
        return
    print(f"[법령 찾기] {len(targets)}개 장 · 엔진 {llm.label()} · 책 기준일 {toc.fmt_date(date) or '모름(개정 여부 표시 안 함)'}")
    cache = config.BOOK_DIR / "law_scan"
    cache.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    memo, found = {}, {}
    for e in targets:
        s, end = e["pages"]
        text = _chapter_text(doc, s, end)
        ments = mentions(text)
        linked = [l["name"] for l in e.get("laws", [])]
        prompt = PROMPT.format(linked=", ".join(linked) or "없음",
                               mentions=", ".join(f"{k}: {v}" for k, v in list(ments.items())[:40]) or "없음",
                               title=e["title"], s=s, e=end, text=text)
        # 캐시 키 = 장 제목 · 쪽 · 본문 (+ 프롬프트 틀). 연결 법령이 바뀌어도(미리 연결 등) 다시 부르지 않게
        key = hashlib.sha1((PROMPT + e["title"] + f"{s}-{end}" + text).encode("utf-8")).hexdigest()[:12]
        cf = cache / f"{key}.json"
        if cf.exists():
            res = json.loads(cf.read_text(encoding="utf-8"))
            print(f"  [cache] {e['title']}")
        else:
            print(f"  [{llm.label()}] {e['title']} (p.{s}–{end}) ...", flush=True)
            res = llm.ask(prompt, SCHEMA)  # 멈춤 · 한도 · 로그인 만료는 그대로 올려서 작업을 멈춘다
            cf.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        cand = {}
        for x in res.get("laws", []):
            n = _norm(x["name"])
            if n.replace(" ", "") in STOP:
                continue
            cand[n] = {"name": n, "conf": round(float(x.get("confidence", 0)), 2),
                       "pages": [p for p in x.get("pages", []) if s <= p <= end][:5], "reason": x.get("reason", ""),
                       "mentions": ments.get(n, 0)}
        for n, c in ments.items():  # 규칙으로만 잡힌 것 (엔진이 빠뜨린 직접 인용)
            if n not in cand:
                cand[n] = {"name": n, "conf": 0.3 if c < 3 else 0.5, "pages": [], "reason": "본문에 이름이 직접 인용됨",
                           "mentions": c}
        out = []
        for n, c in cand.items():
            if n in linked:
                continue
            chk = _check(n, date, memo)
            if not chk["exists"]:
                print(f"    - {n}: 법제처에 없음 → 뺌")
                continue
            out.append({**c, **{k: v for k, v in chk.items() if k != "exists"}})
        out.sort(key=lambda x: (-(x["conf"] >= AUTO_ON and x["changed"]), -x["conf"], -x["mentions"]))
        found[_key(e)] = out
        on = [x["name"] for x in out if x["conf"] >= AUTO_ON and x["changed"]]
        print(f"  → {e['title']}: 제안 {len(out)}개" + (f" · 미리 연결 {', '.join(on)}" if on else ""))

    # 저장 — 그 사이 화면에서 바뀐 목록을 덮지 않게 다시 읽어서 합친다
    t = config.toc()
    bl = t.setdefault("baseline", {"page": None, "laws": [], "source": None})
    names = {l["name"] for l in bl.get("laws", [])}
    for e in t["chapters"]:
        sug = found.get(_key(e))
        if sug is None:
            continue
        e["law_suggest"] = sug
        add = [x for x in sug if x["conf"] >= AUTO_ON and x["changed"]]
        for x in add:
            if x["name"] not in names:
                bl.setdefault("laws", []).append({"name": x["name"], "version": x["version"], "checked": True,
                                                  "header": x["header"], "line": "", "auto": "연혁" if x["version"] else None,
                                                  "auto_tried": date, "auto_note": x["note"], "from": "AI 장별 찾기"})
                names.add(x["name"])
        if add:
            cur = [l["name"] for l in e.get("laws", [])]
            e["laws"] = toc.laws_for(cur + [x["name"] for x in add if x["name"] not in cur], bl)
    bl["suggest"] = [x for x in bl.get("suggest", []) if x["name"] not in names]
    t["law_scan"] = {"date": date, "chapters": len(found)}
    config.save_toc(t)
    print("[법령 찾기] 끝 — ① 차례 화면 ③에서 장마다 「AI 제안」을 보고 켜거나 끈 뒤 [차례 확정]을 누르세요.")
