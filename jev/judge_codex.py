"""JEV 판정: 변경 조문마다 Codex CLI(ChatGPT OAuth 로그인)를 호출해
책의 어느 문구를 고쳐야 하는지(Judge), 근거(Evidence), 수정확률·수정문구(Verdict)를 받는다.

산출: work/<ch>/results.json   (캐시: work/<ch>/cache/<id>.json)
"""
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

JUDGE_TYPES = ["수치변경", "용어변경", "내용변경", "신설조문누락", "삭제조문잔존", "무관"]

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary_of_change", "items"],
    "properties": {
        "summary_of_change": {"type": "string"},
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["page", "judge_type", "book_quote", "old_law_quote", "new_law_quote",
                         "p_revise", "old_text", "new_text", "reason"],
            "properties": {
                "page": {"type": "integer"},
                "judge_type": {"type": "string", "enum": JUDGE_TYPES},
                "book_quote": {"type": "string"},
                "old_law_quote": {"type": "string"},
                "new_law_quote": {"type": "string"},
                "p_revise": {"type": "number"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "reason": {"type": "string"},
            }}},
    },
}

PROMPT = """너는 수험서 법령 교재의 개정 검수 편집자다. 아래 [변경 조문]이 [교재 본문]에 미치는 영향을 JEV 방식으로 판정하라.

- J(Judge): 교재의 어떤 문구가 이 개정과 관련되는지 찾고 영향유형을 고른다
  (수치변경/용어변경/내용변경/신설조문누락/삭제조문잔존/무관).
- E(Evidence): book_quote(교재 원문), old_law_quote(구 조문 해당부분), new_law_quote(신 조문 해당부분)를 그대로 인용한다.
- V(Verdict): p_revise = 이 교재 문구를 '고쳐야 할' 확률(0~1). 그대로 둬도 되면 낮게.
  old_text = 교재에서 바꿀 부분을 교재 원문 그대로(띄어쓰기·기호 포함, 15~120자, 교재 안에서 유일하게 찾히도록) 복사.
  new_text = old_text 를 대체할 문구(교재 문체 유지: 법조문 문장이 아니라 교재 서술체).
  신설조문누락이면 old_text 는 새 내용을 넣을 위치 바로 앞 교재 문장, new_text 는 그 문장 + 추가할 서술.
  reason = 판단 근거 한두 문장.
  old_text 는 한 줄~한 문장 단위로 짧게 끊고, 항목끼리 범위가 겹치지 않게 한다(제목 줄과 본문 줄은 별도 항목).

규칙:
1. 교재가 이 조문을 다루는 곳은 모두 항목으로 낸다. 고칠 필요가 없으면 p_revise 를 낮게(≤0.2) 주고 old_text=new_text 로 둔다.
2. 교재가 이 조문을 전혀 다루지 않고, 시험상 추가할 필요도 낮으면 items 를 빈 배열로 둔다.
3. 기본문제·정답해설(마지막 쪽들)에 이 개정으로 정답이 바뀌는 지문이 있으면 반드시 포함한다.
4. 교재 원문에 없는 내용을 지어내지 마라. page 는 [p.N] 표시를 따른다.
5. 파일을 읽거나 명령을 실행하지 말고, 이 프롬프트만으로 답하라.

참고: 교재에 이 조문번호가 표기된 쪽(자동탐지) = {hint}

[변경 조문] {law} {article}({title}) — {kind}
--- 구(교재 기준) ---
{old}
--- 신(현행) ---
{new}
--- diff ---
{diff}

[교재 본문: {ch_title}]
{book}
"""


def codex_bin():
    exe = shutil.which("codex") or shutil.which("codex.cmd")
    if not exe:
        raise SystemExit("codex CLI를 찾을 수 없습니다. setup.bat 을 먼저 실행하세요.")
    return exe


def ask_codex(prompt, model=None, timeout=600):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "schema.json").write_text(json.dumps(SCHEMA, ensure_ascii=False), encoding="utf-8")
        cmd = [codex_bin(), "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
               "-C", str(td), "--output-schema", str(td / "schema.json"),
               "-o", str(td / "out.json"), "--color", "never", "-"]
        if model:
            cmd[2:2] = ["-m", model]
        r = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout)
        out = td / "out.json"
        if not out.exists():
            raise RuntimeError(f"codex 실패 (exit {r.returncode}): {r.stderr.decode('utf-8', 'replace')[-800:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def baseline_item(ch, laws):
    """[법령적용기준] 줄 갱신 — 규칙 기반, 항상 수정."""
    base = json.loads((config.workdir(ch) / "baseline.json").read_text(encoding="utf-8"))
    items = []
    for b in base:
        law = next((l for l in laws if l["name"] == b["law"]), None)
        if not law or not law["new_header"]:
            continue
        new_line = f"{b['law']} {law['new_header']}"
        if new_line.replace(" ", "") == b["line"].replace(" ", ""):
            continue
        items.append({
            "change_id": "기준", "law": b["law"], "article": "법령적용기준", "title": "", "kind": "변경",
            "page": b["page"], "judge_type": "수치변경", "book_quote": b["line"],
            "old_law_quote": law["old_header"], "new_law_quote": law["new_header"],
            "p_revise": 1.0, "old_text": b["line"], "new_text": new_line,
            "reason": "책의 법령 적용 기준일을 현행 법령으로 갱신 (규칙 기반)", "source": "rule",
        })
    return items


def _key(s):
    return re.sub(r"\s+", "", s)


def dedupe(rows):
    """여러 조문이 같은 교재 문구를 짚은 경우 하나로 합친다.
    - 같은 문구: 수정확률이 가장 높은 항목만 남기고 나머지 조문은 related 에 기록
    - '그대로 둠' 항목이 다른 '수정' 항목 범위 안에 들어가면 제거
    - 작은 '수정' 항목이 큰 '수정' 항목에 포함되고 수정내용도 포함되면 제거
    """
    best = {}
    for r in sorted(rows, key=lambda r: -r["p_revise"]):
        k = (r["page"], _key(r["old_text"]))
        if k in best:
            best[k].setdefault("related", []).append(r["change_id"])
        else:
            best[k] = r
    rows = list(best.values())
    rev = [r for r in rows if r["p_revise"] >= 0.2 and _key(r["old_text"]) != _key(r["new_text"])]
    out = []
    for r in rows:
        ko, kn = _key(r["old_text"]), _key(r["new_text"])
        covered = False
        for b in rev:
            if b is r or r["page"] != b["page"]:
                continue
            bo, bn = _key(b["old_text"]), _key(b["new_text"])
            if ko and ko in bo and len(ko) < len(bo):
                if r not in rev or (kn in bn):
                    covered = True
                    break
        if covered:
            continue
        out.append(r)
    return out


def run(ch, workers=3, model=None, limit=None):
    cfg = config.load()
    c = config.chapter(cfg, ch)
    wd = config.workdir(ch)
    cache = wd / "cache"
    cache.mkdir(exist_ok=True)
    book = json.loads((wd / "book.json").read_text(encoding="utf-8"))
    ch_data = json.loads((wd / "changes.json").read_text(encoding="utf-8"))
    changes = ch_data["changes"][:limit] if limit else ch_data["changes"]
    book_text = "".join(f"\n[p.{p['page']}]\n{p['text']}" for p in book["pages"])

    def one(chg):
        hint = [p["page"] for p in book["pages"]
                if f"{chg['abbr']} {chg['article']}" in p["law_refs"]] or "없음"
        prompt = PROMPT.format(hint=hint, law=chg["law"], article=chg["article"], title=chg["title"],
                               kind=chg["kind"], old=chg["old"] or "(없음)", new=chg["new"] or "(없음)",
                               diff=chg["diff"], ch_title=c["title"], book=book_text)
        key = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
        cf = cache / f"{chg['id']}_{key}.json"
        if cf.exists():
            res = json.loads(cf.read_text(encoding="utf-8"))
            print(f"  [cache] {chg['id']}")
        else:
            print(f"  [codex] {chg['id']} ({chg['kind']}) ...", flush=True)
            try:
                res = ask_codex(prompt, model=model)
            except Exception as e:  # 한 조문 실패가 전체를 막지 않게
                print(f"  [오류] {chg['id']}: {e}")
                return chg, {"summary_of_change": f"판정 실패: {e}", "items": []}, True
            cf.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        return chg, res, False

    print(f"[judge] 변경 조문 {len(changes)}건을 Codex로 판정 (동시 {workers})")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, changes))

    rows = baseline_item(ch, ch_data["laws"])
    summaries = []
    for chg, res, failed in results:
        summaries.append({"change_id": chg["id"], "law": chg["law"], "article": chg["article"],
                          "title": chg["title"], "kind": chg["kind"], "summary": res["summary_of_change"],
                          "n_items": len(res["items"]), "failed": failed,
                          "old": chg["old"], "new": chg["new"]})
        for it in res["items"]:
            p = max(0.0, min(1.0, float(it["p_revise"])))
            rows.append({"change_id": chg["id"], "law": chg["law"], "article": chg["article"],
                         "title": chg["title"], "kind": chg["kind"], **it, "p_revise": round(p, 2),
                         "source": "codex"})
    rows = dedupe(rows)
    for i, r in enumerate(sorted(rows, key=lambda r: (-r["p_revise"], r["page"]))):
        r["id"] = i + 1
        r["p_keep"] = round(1 - r["p_revise"], 2)
    rows.sort(key=lambda r: r["id"])

    out = {"chapter": ch, "title": c["title"], "laws": ch_data["laws"],
           "changes": summaries, "rows": rows}
    (wd / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[judge] 정오표 후보 {len(rows)}건 → {wd / 'results.json'}")
    return out


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else "ch09")
