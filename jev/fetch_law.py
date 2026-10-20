"""(선택) 법제처 국가법령정보센터에서 법령 본문을 받아 <장>/01_비교자료/*.txt 로 저장한다.
손으로 저장한 PDF/HWPX가 있으면 그쪽이 우선(config.resolve 순서: pdf > hwpx > txt).

python -m jev.fetch_law 09      # 09장에 연결된 법령의 구판(책 기준)·현행을 받는다
python -m jev.fetch_law book    # 책의 법령 목록 전체를 00_책/법령/ 에 한꺼번에 받는다

한 번 받은 법령은 00_책/법령/<법령명>__<버전|현행>.txt 에 두고, 장마다 다시 받지 않고 복사한다.
"""
import html
import re
import sys
import time
import urllib.parse
import urllib.request

from . import config

BASE = "https://www.law.go.kr"
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url, data=None, referer=None):
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, data=data.encode() if data else None, headers=headers)
    for attempt in range(3):  # 법제처가 가끔 일시적으로 404/5xx 를 돌려준다
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def fetch(name, version=None):
    """version = '공포번호,공포일자[@시행일자]' (예: '20645,20250107@20250131').
    같은 공포번호라도 시행일이 여럿이면(시행일이 나뉜 개정) 책이 기준으로 삼은 시행일 본문을 받는다."""
    version, _, want_ef = (version or "").partition("@")
    path = "/법령/" + name.replace(" ", "") + (f"/({version})" if version else "")
    frame = _get(BASE + urllib.parse.quote(path, safe="/(),"))
    m = re.search(r"lsiSeq=(\d+)[^\"']*?efYd=(\d*)", frame)
    if not m:
        raise SystemExit(f"법제처에서 찾지 못했습니다: {name} {version or ''}")
    seq, ef = m.groups()
    for e in ([want_ef] if want_ef and want_ef != ef else []) + [ef]:
        try:
            t = _body(seq, e)
        except OSError as err:  # 책에 적힌 시행일이 틀린 경우(예: 11. 31.) 등 → 기본 시행일로 다시
            print(f"[받기] 시행일 {e} 본문 실패({err}) → 기본 본문으로 다시 시도")
            continue
        if "[시행" in t:
            return t
    raise SystemExit("본문을 받지 못했습니다. 법제처 화면에서 직접 저장해 _context 에 넣어 주세요.")


def _body(seq, ef):
    body = _get(BASE + "/LSW/lsInfoR.do",
                data=f"lsiSeq={seq}&chrClsCd=010202&efYd={ef}&ancYnChk=0",
                referer=f"{BASE}/LSW/lsInfoP.do?lsiSeq={seq}")
    t = re.sub(r"<(script|style).*?</\1>", "", body, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return "\n".join(l.strip() for l in t.splitlines() if l.strip())


LAW_DIR = config.BOOK_DIR / "법령"


def cached(name, ver, refresh=False):
    """00_책/법령/ 캐시를 거쳐 법령 본문을 얻는다. (현행은 refresh=True 면 다시 받음)"""
    f = LAW_DIR / f"{name.replace(' ', '')}__{re.sub(r'[,@]', '_', ver or '현행')}.txt"
    if f.exists() and not refresh:
        print(f"[받기] {name} {ver or '(현행)'} — 이미 받아 둔 파일 사용")
        return f.read_text(encoding="utf-8")
    print(f"[받기] {name} {ver or '(현행)'} ...")
    text = fetch(name, ver)
    LAW_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return text


def fetch_book():
    """책의 법령 목록(차례 화면 '이 책의 법령')에서 체크된 것의 현행·책 기준판을 한꺼번에 받는다."""
    laws = [l for l in ((config.toc() or {}).get("baseline") or {}).get("laws", []) if l.get("checked", True)]
    if not laws:
        raise SystemExit("받을 법령이 없습니다. '이 책의 법령'에 법령을 넣어 주세요.")
    ok, fail = 0, []
    for law in laws:
        name = law["name"].replace(" ", "")
        for ver in (None, law.get("version")):
            if ver == "":
                print(f"[받기] {law['name']}: 책 기준 버전 정보가 없어 구판은 건너뜀")
                continue
            try:
                t = cached(name, ver, refresh=ver is None)
                ok += 1
                print(f"[받기]  → {len(t):,}자")
            except (SystemExit, OSError) as e:  # 한 법령 실패가 나머지를 막지 않게
                fail.append(f"{law['name']} {ver or '현행'}")
                print(f"[받기] 실패: {e}")
    print(f"[받기] 완료 {ok}건" + (f" · 실패 {len(fail)}건: {', '.join(fail)}" if fail else "") + f" → {LAW_DIR}")


def fetch_chapter(cid, only_missing=False):
    """장에 연결된 법령(법률·시행령)의 구판(책 기준)·현행을 01_비교자료에 받는다.
    only_missing=True 이면 비어 있는 칸만 받는다 (판정 시작 때 자동 보충용)."""
    c = config.chapter(cid)
    if not c.get("laws"):
        raise SystemExit("이 장에 연결된 법령이 없습니다. 차례 화면에서 법령을 확인하세요.")
    d = config.stage(cid, "01_비교자료")
    for law in c["laws"]:
        name = law.get("fetch_name") or law["name"].replace(" ", "")
        for which, ver in (("new", None), ("old", law.get("old_version"))):
            if only_missing and config.source(cid, f"law_{which}_{law['abbr']}"):
                continue
            if which == "old" and not ver:
                print(f"[받기] {law['name']}: 책 기준 버전 정보가 없어 구판은 건너뜀")
                continue
            text = cached(name, ver)
            slot = f"law_{which}_{law['abbr']}"
            (d / f"{slot}.txt").write_text(text, encoding="utf-8")
            others = [p.name for p in d.glob(f"{slot}.*") if p.suffix.lower() in (".pdf", ".hwpx")]
            note = f"  (※ {', '.join(others)} 이(가) 있어 그 파일이 우선 사용됩니다)" if others else ""
            print(f"[받기] → 01_비교자료/{slot}.txt ({len(text):,}자){note}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "09"
    fetch_book() if arg == "book" else fetch_chapter(arg)
