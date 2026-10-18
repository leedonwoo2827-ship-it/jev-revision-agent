"""명령줄 실행: 원고 추출 → 비교 → Codex JEV 판정 → 검토 HTML.

python -m jev.pipeline 09 [--workers 3] [--model gpt-5] [--limit N]
(차례 확정은 웹 콘솔 또는 python -m jev.pipeline --toc 로)
"""
import argparse

from . import config, diff_law, extract_book, judge_codex, report, toc


def make_toc():
    pdf = config.book_pdf()
    if not pdf:
        raise SystemExit(f"{config.BOOK_DIR} 에 교재 PDF를 넣으세요.")
    t = toc.extract(pdf)
    t["confirmed"] = True
    config.save_toc(t)
    for c in t["chapters"]:
        if c["include"]:
            config.save_chapter({**c, "mode": "law"})
    print(f"[차례] {len(t['chapters'])}개 장 ({t['method']}) → {config.WS}")


def run(cid, workers=3, model=None, limit=None):
    extract_book.run(cid)
    _, changes, missing = diff_law.run(cid)
    if missing:
        raise SystemExit("비교자료가 모자랍니다. 위 목록을 채운 뒤 다시 실행하세요.")
    if not changes:
        print("[실행] 구/신 사이에 차이가 없습니다. 비교자료 버전을 확인하세요.")
    judge_codex.run(cid, workers=workers, model=model, limit=limit)
    report.run(cid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter", nargs="?", default="09")
    ap.add_argument("--toc", action="store_true", help="교재 PDF에서 차례를 뽑아 장 워크스페이스를 만든다")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default=None, help="codex 모델 (기본: codex 설정값)")
    ap.add_argument("--limit", type=int, default=None, help="테스트용: 앞 N개 단위만 판정")
    a = ap.parse_args()
    if a.toc:
        return make_toc()
    run(a.chapter, a.workers, a.model, a.limit)


if __name__ == "__main__":
    main()
