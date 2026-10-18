import glob
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUTPUT = ROOT / "output"


def load():
    with open(ROOT / "chapters.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def chapter(cfg, ch):
    if ch not in cfg["chapters"]:
        raise SystemExit(f"chapters.yaml 에 '{ch}' 가 없습니다. 있는 장: {list(cfg['chapters'])}")
    return cfg["chapters"][ch]


def resolve(pattern):
    """glob 패턴 → 실제 파일 1개 (pdf > hwpx > txt 우선). 없으면 None."""
    hits = glob.glob(str(ROOT / pattern))
    order = {".pdf": 0, ".hwpx": 1, ".txt": 2}
    hits = [h for h in hits if Path(h).suffix.lower() in order]
    if not hits:
        return None
    return Path(sorted(hits, key=lambda h: order[Path(h).suffix.lower()])[0])


def workdir(ch):
    d = WORK / ch
    d.mkdir(parents=True, exist_ok=True)
    return d
