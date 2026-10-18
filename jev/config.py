"""워크스페이스 구조와 장(chapter) 설정.

workspace/
  00_책/                     교재 원본 PDF + toc.json (확정된 차례)
  09_소상공인기본법/          장 워크스페이스 (번호_제목)
    chapter.json             장 설정 (쪽 범위, 비교 방식, 법령 목록)
    00_원고/                 장 추출본 (pdf, txt, book.json, baseline.json)
    01_비교자료/             법령 구·신 / 신규 개정기본서 PDF
    02_변경목록/             changes.json
    03_판정/                 results.json, cache/
    04_검토/                 errata.html
    05_반영/                 반영내역 · 반영본 · 게시판 정오표
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WS = Path(os.environ.get("JEV_WORKSPACE", ROOT / "workspace"))
BACKUP = WS.parent / "backup"
BOOK_DIR = WS / "00_책"
STAGES = ["00_원고", "01_비교자료", "02_변경목록", "03_판정", "04_검토", "05_반영"]
SRC_EXTS = {".pdf", ".hwpx", ".txt"}


def _read(p, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 책 ----------
def book_pdf():
    if not BOOK_DIR.exists():
        return None
    pdfs = sorted(BOOK_DIR.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def toc():
    return _read(BOOK_DIR / "toc.json")


def save_toc(data):
    _write(BOOK_DIR / "toc.json", data)


# ---------- 장 ----------
def slug(title):
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", title.strip())
    return s.strip("_")[:40] or "장"


def chapter_dir(cid):
    """cid = '09' 같은 두 자리 번호. 폴더 이름은 '09_제목'."""
    hits = sorted(WS.glob(f"{cid}_*")) if WS.exists() else []
    if not hits:
        raise SystemExit(f"장 워크스페이스가 없습니다: {cid} (먼저 차례를 확정하세요)")
    return hits[0]


def chapters():
    if not WS.exists():
        return []
    out = []
    for d in sorted(WS.iterdir()):
        c = _read(d / "chapter.json") if d.is_dir() else None
        if c:
            out.append(c)
    return out


def chapter(cid):
    return _read(chapter_dir(cid) / "chapter.json")


def save_chapter(c):
    d = WS / f"{c['id']}_{slug(c['title'])}"
    for old in WS.glob(f"{c['id']}_*"):  # 제목이 바뀌면 폴더 이름도 맞춘다
        if old != d and old.is_dir():
            old.rename(d)
    for s in STAGES:
        (d / s).mkdir(parents=True, exist_ok=True)
    _write(d / "chapter.json", c)
    return d


def stage(cid, name):
    d = chapter_dir(cid) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def source(cid, slot):
    """01_비교자료/<slot>.* 중 하나 (pdf > hwpx > txt)."""
    d = stage(cid, "01_비교자료")
    order = {".pdf": 0, ".hwpx": 1, ".txt": 2}
    hits = [p for p in d.glob(f"{slot}.*") if p.suffix.lower() in order]
    return sorted(hits, key=lambda p: order[p.suffix.lower()])[0] if hits else None


def slots(c):
    """장 설정 → 비교자료 칸 목록."""
    if c.get("mode") == "book":
        return [{"slot": "ref_book", "label": "신규 개정기본서 PDF", "exts": [".pdf"]}]
    out = []
    for law in c.get("laws", []):
        for which, tag in (("old", "책 기준 (구)"), ("new", "현행 (신)")):
            out.append({"slot": f"law_{which}_{law['abbr']}", "label": f"{law['name']} — {tag}",
                        "exts": sorted(SRC_EXTS), "law": law["name"], "which": which, "abbr": law["abbr"],
                        "number": (law.get("old_version") or ",").split(",")[0],
                        "hint": law.get("old_header", "") if which == "old" else "현행"})
    return out


# ---------- 새 책 / 백업 ----------
def _ws_empty():
    return not WS.exists() or not any(WS.iterdir())


def backup_current(reason="새 책"):
    """현재 워크스페이스를 backup/<시각>_<책이름>/ 으로 옮긴다 (지우지 않음)."""
    import shutil
    from datetime import datetime
    if _ws_empty():
        return None
    pdf = book_pdf()
    name = slug(pdf.stem) if pdf else "책없음"
    dest = BACKUP / f"{datetime.now():%Y%m%d-%H%M%S}_{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(WS), str(dest))
    WS.mkdir(parents=True, exist_ok=True)
    return dest


def backups():
    if not BACKUP.exists():
        return []
    out = []
    for d in sorted(BACKUP.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        t = _read(d / "00_책" / "toc.json") or {}
        out.append({"name": d.name, "book": t.get("book"),
                    "chapters": sum(1 for x in d.iterdir() if (x / "chapter.json").exists())})
    return out


def restore(name):
    """백업을 워크스페이스로 되돌린다. 지금 작업은 먼저 백업한다."""
    import shutil
    src = BACKUP / name
    if not src.is_dir() or src.parent != BACKUP:
        raise ValueError(f"백업이 없습니다: {name}")
    saved = backup_current()
    if WS.exists():
        WS.rmdir()  # backup_current 뒤의 빈 폴더
    shutil.move(str(src), str(WS))
    return saved
