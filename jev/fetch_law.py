"""(선택) 법제처 국가법령정보센터에서 법령 본문을 받아 <장>/01_비교자료/*.txt 로 저장한다.
손으로 저장한 PDF/HWPX가 있으면 그쪽이 우선(config.resolve 순서: pdf > hwpx > txt).

python -m jev.fetch_law 09      # 09장에 연결된 법령의 구판(책 기준)·현행을 받는다
"""
import html
import re
import sys
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
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def fetch(name, version=None):
    path = "/법령/" + name.replace(" ", "") + (f"/({version})" if version else "")
    frame = _get(BASE + urllib.parse.quote(path, safe="/(),"))
    m = re.search(r"lsiSeq=(\d+)[^\"']*?efYd=(\d*)", frame)
    if not m:
        raise SystemExit(f"법제처에서 찾지 못했습니다: {name} {version or ''}")
    seq, ef = m.groups()
    body = _get(BASE + "/LSW/lsInfoR.do",
                data=f"lsiSeq={seq}&chrClsCd=010202&efYd={ef}&ancYnChk=0",
                referer=f"{BASE}/LSW/lsInfoP.do?lsiSeq={seq}")
    t = re.sub(r"<(script|style).*?</\1>", "", body, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(l.strip() for l in t.splitlines() if l.strip())
    if "[시행" not in t:
        raise SystemExit("본문을 받지 못했습니다. 법제처 화면에서 직접 저장해 _context 에 넣어 주세요.")
    return t


def fetch_chapter(cid):
    """장에 연결된 법령(법률·시행령)의 구판(책 기준)·현행을 01_비교자료에 받는다."""
    c = config.chapter(cid)
    if not c.get("laws"):
        raise SystemExit("이 장에 연결된 법령이 없습니다. 차례 화면에서 법령을 확인하세요.")
    d = config.stage(cid, "01_비교자료")
    for law in c["laws"]:
        name = law.get("fetch_name") or law["name"].replace(" ", "")
        for which, ver in (("new", None), ("old", law.get("old_version"))):
            if which == "old" and not ver:
                print(f"[받기] {law['name']}: 책 기준 버전 정보가 없어 구판은 건너뜀")
                continue
            print(f"[받기] {law['name']} {ver or '(현행)'} ...")
            text = fetch(name, ver)
            slot = f"law_{which}_{law['abbr']}"
            (d / f"{slot}.txt").write_text(text, encoding="utf-8")
            others = [p.name for p in d.glob(f"{slot}.*") if p.suffix.lower() in (".pdf", ".hwpx")]
            note = f"  (※ {', '.join(others)} 이(가) 있어 그 파일이 우선 사용됩니다)" if others else ""
            print(f"[받기] → 01_비교자료/{slot}.txt ({len(text):,}자){note}")


if __name__ == "__main__":
    fetch_chapter(sys.argv[1] if len(sys.argv) > 1 else "09")
