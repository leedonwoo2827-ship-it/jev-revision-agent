"""법제처에서 저장한 법령 파일(PDF/HWPX/TXT) → 조문 단위 dict.

{"header": "[시행 ...] [법률 제...]", "articles": {"제2조": {"title": "정의", "text": "..."}}, "addenda": "..."}
"""
import re
import zipfile
from pathlib import Path

import fitz

# 줄 맨 앞의 "제N조(제목)" 또는 "제N조 삭제" 만 조문 시작으로 본다.
# (본문 중 줄바꿈으로 "제2조제1항에 따른 ..." 이 줄 앞에 오는 경우를 배제)
ART = re.compile(r"^(제\d+조(?:의\d+)?)\s*(?:\(([^)]*)\)|(삭제))")
NOISE = re.compile(r"^(법제처\s*\d*\s*국가법령정보센터|국가법령정보센터|법제처|\d+\s*/\s*\d+|\[시행일[^\]]*\])$")
PART = re.compile(r"^(제\d+(?:장|절)(?:의\d+)?)\s+(.+)$")  # 장·절 제목도 하나의 항목으로 비교
HEADER = re.compile(r"\[시행[^\]]*\]\s*\[[^\]]*\]")
TAGS = re.compile(r"<(개정|신설|삭제|본조신설|전문개정|타법개정)[^>]*>|\[(본조신설|전문개정|제목개정|종전[^\]]*)[^\]]*\]")


def read_text(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return "\n".join(p.get_text() for p in fitz.open(path))
    if suf == ".hwpx":
        out = []
        with zipfile.ZipFile(path) as z:
            secs = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n))
            for n in secs:
                xml = z.read(n).decode("utf-8")
                for para in re.findall(r"<hp:p[ >].*?</hp:p>", xml, flags=re.S):
                    out.append("".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", para, flags=re.S)))
        return re.sub(r"<[^>]+>", "", "\n".join(out))
    return path.read_text(encoding="utf-8", errors="replace")


def parse(path: Path) -> dict:
    raw = read_text(path)
    m = HEADER.search(raw)
    header = m.group(0) if m else ""
    lines = [l.strip() for l in raw.splitlines()]
    lines = [l for l in lines if l and not NOISE.match(l)]
    # 법제처 PDF는 쪽마다 법령명을 머리글로 찍는다 → '[시행 …]' 바로 위 줄(법령명)과 같은 줄은 버린다
    hi = next((i for i, l in enumerate(lines) if HEADER.search(l)), None)
    if hi:
        title = re.sub(r"\s+", "", lines[hi - 1])
        if title and not ART.match(lines[hi - 1]):
            lines = lines[:hi] + [l for l in lines[hi:] if re.sub(r"\s+", "", l) != title]

    articles, cur, addenda = {}, None, []
    in_addenda = False
    for l in lines:
        if re.match(r"^부\s*칙", l) or re.search(r"부\s*칙\s*<(법률|대통령령|총리령|부령|[^>]*령)", l):
            in_addenda = True
        if in_addenda:
            addenda.append(l)
            continue
        m = PART.match(l)
        if m and not ART.match(l):
            title = TAGS.sub("", m.group(2)).strip()
            articles[m.group(1)] = {"title": title, "deleted": False, "text": l}
            cur = None
            continue
        m = ART.match(l)
        if m:
            key = m.group(1)
            cur = {"title": m.group(2) or "", "deleted": bool(m.group(3)), "text": l}
            articles[key] = cur  # 같은 조번호가 다시 나오면(시행예정 병기 등) 뒤의 것을 쓴다
        elif cur is not None:
            cur["text"] += "\n" + l
    return {"file": path.name, "header": header, "articles": articles, "addenda": "\n".join(addenda)[:6000]}


def norm(text: str) -> str:
    """비교용: 개정 태그·공백 제거."""
    return re.sub(r"\s+", "", TAGS.sub("", text))
