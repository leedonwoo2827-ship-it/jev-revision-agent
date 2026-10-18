"""전체 실행: 교재 추출 → 법령 diff → Codex JEV 판정 → HTML → (선택) 로컬 서버.

python -m jev.pipeline ch09 [--serve] [--workers 3] [--model gpt-5] [--limit N]
"""
import argparse

from . import diff_law, extract_book, judge_codex, report, server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter", nargs="?", default="ch09")
    ap.add_argument("--serve", action="store_true", help="끝나면 검토 서버를 띄우고 브라우저를 연다")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default=None, help="codex 모델 (기본: codex 설정값)")
    ap.add_argument("--limit", type=int, default=None, help="테스트용: 앞 N개 조문만 판정")
    a = ap.parse_args()

    extract_book.run(a.chapter)
    laws, changes, missing = diff_law.run(a.chapter)
    if missing:
        raise SystemExit("\n법령 파일을 _context 에 넣은 뒤 다시 실행하세요.")
    if not changes:
        print("[pipeline] 구/신 법령에 차이가 없습니다. 파일 버전을 확인하세요.")
    judge_codex.run(a.chapter, workers=a.workers, model=a.model, limit=a.limit)
    report.run(a.chapter)
    if a.serve:
        server.serve()


if __name__ == "__main__":
    main()
