"""(선택) 법제처 국가법령정보센터에서 법령 본문을 받아 _context/*.txt 로 저장한다.
손으로 저장한 PDF/HWPX가 있으면 그쪽이 우선(config.resolve 순서: pdf > hwpx > txt).

python -m jev.fetch_law "소상공인기본법" new 법
python -m jev.fetch_law "소상공인기본법" old 법 17623,20201208     # (법률번호,공포일자)
python -m jev.fetch_law "소상공인기본법시행령" old 령 32274,20211228
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


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    name, which, abbr = sys.argv[1:4]
    version = sys.argv[4] if len(sys.argv) > 4 else None
    text = fetch(name, version)
    out = config.ROOT / "_context" / f"law_{which}_{abbr}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[fetch] {name} {version or '(현행)'} → {out} ({len(text):,}자)")
