"""JEV 개정 에이전트 — 웹 콘솔 서버 (이 PC 전용 localhost, 표준 라이브러리만 사용).

  GET  /                         콘솔 화면 (jev/console.html)
  GET  /api/state                책 · 차례 · 장 목록 · 작업 상태
  POST /api/new-book             새 책 넣기: 지금 워크스페이스를 backup/ 으로 옮기고 비운다
  POST /api/restore              백업 불러오기 (지금 작업은 먼저 백업)
  POST /api/book/upload          교재 PDF 전체 올리기 → 차례 자동 추출
  POST /api/book/toc             차례 확정 (JSON) → 장 워크스페이스 생성
  POST /api/book/toc-codex       판정 엔진으로 차례 다시 뽑기 (백그라운드)
  POST /api/book/laws            이 책의 법령 목록 저장 (+ reattach: 장마다 기본 연결 다시)
  POST /api/book/laws-fetch      이 책의 법령(현행·책 기준판)을 법제처에서 한꺼번에 받기 (백그라운드)
  GET  /api/ch?id=09             장 상세 (비교자료 칸 · 판정 요약 · 폴더별 파일)
  POST /api/codex/refresh        Codex 로그인 상태 + 실제 짧은 호출로 확인 (백그라운드)
  POST /api/codex/login          새 명령창에서 codex login (relogin=true 면 먼저 로그아웃)
  POST /api/codex/logout         codex logout
  POST /api/claude/refresh|login|logout   Claude(claude.ai OAuth) 계정 — Codex 와 같은 모양
  POST /api/settings             판정 엔진(codex · auto · claude) · 한도면 기다릴 시간
  POST /api/ch/upload?id=&slot=  비교자료 올리기 (본문 = 파일, X-Filename 헤더)
  POST /api/ch/config?id=        비교 방식(law/book) · 개정기본서 쪽 범위 저장
  POST /api/ch/fetch?id=         법제처에서 구·신 법령 받기 (백그라운드)
  POST /api/ch/run?id=           추출 → 비교 → Codex 판정 → 검토 HTML (백그라운드)
  POST /api/ch/open?id=&stage=   탐색기로 폴더 열기
  POST /api/batch                고른 장에 법령 받기 · 판정을 차례로 ({ids, steps:[fetch, judge], redo}) (백그라운드)
  POST /api/stop                 지금 작업 멈춤 (진행 중 호출만 끝내 캐시에 남기고 바로)
  GET  /api/errata               ④ 책 전체 수정 후보 · 체크 · 만든 정오표
  POST /api/decide               ④ 체크 저장 ({decisions})
  POST /api/import-review        체크본 가져오기 (.json · .xlsx, X-Filename 헤더)
  POST /api/export               ⑤ 책 전체 정오표 만들기
  POST /api/errata/open          _정오표 폴더 열기
  GET  /errata-files/<name>[?dl=1]
  GET  /zip[?level=60이상]       정오표 파일 zip (level 을 주면 그 단계 한 벌만)
  GET  /review/<id>              장별 검토 내역 화면 (예전 방식)
  POST /apply                    장별 [반영] (예전 방식)
  GET  /files/<id>/<stage>/<name>[?dl=1]
"""
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
import zipfile
from datetime import datetime
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import apply, claude_auth, codex_auth, config, errata, fetch_law, llm, report, toc
from .judge_codex import EngineStop, LimitError
from .pipeline import run as run_pipeline

CONSOLE = Path(__file__).with_name("console.html")


class Job:
    """한 번에 하나의 백그라운드 작업. print 출력을 로그로 모은다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.name, self.ch, self.running, self.error, self.error_kind = None, None, False, None, None
        self.log, self.started, self.ended, self.stop, self.waiting = [], None, None, False, None

    def start(self, name, ch, fn):
        with self.lock:
            if self.running:
                return False
            self.name, self.ch, self.running, self.error, self.error_kind = name, ch, True, None, None
            self.log, self.started, self.ended, self.stop = [], datetime.now(), None, False
            self.waiting = None
            llm.CANCEL.clear()
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()
        return True

    def _run(self, fn):
        real, job = sys.stdout, self

        class Tee:
            """print 가 여러 조각으로 써도 한 줄로 모아 로그에 넣는다."""
            buf = ""

            def write(self, s):
                real.write(s)
                self.buf += s
                *lines, self.buf = self.buf.split("\n")
                job.log.extend(l for l in lines if l.strip())
                return len(s)

            def flush(self):
                real.flush()

        sys.stdout = Tee()
        try:
            fn()
            print(f"[완료] {self.name}")
        except SystemExit as e:
            self.error = str(e)
            print(f"[중단] {e}")
        except Exception as e:
            self.error, self.error_kind = str(e), getattr(e, "kind", None)
            if self.error_kind == "auth":
                codex_auth.mark_expired()
            elif self.error_kind == "claude_auth":
                claude_auth.mark_expired()
            print(f"[오류] {e}")
            if not isinstance(e, EngineStop):
                real.write(traceback.format_exc())
        finally:
            sys.stdout = real
            self.running, self.ended = False, datetime.now()

    def state(self):
        return {"name": self.name, "ch": self.ch, "running": self.running, "error": self.error,
                "error_kind": self.error_kind, "stopping": self.stop, "waiting": self.waiting,
                "log": self.log[-300:],
                "started": self.started.strftime("%H:%M:%S") if self.started else None,
                "ended": self.ended.strftime("%H:%M:%S") if self.ended else None}


JOB = Job()


def _mtime(p):
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")


def results(cid, dec=None):
    """판정 요약. dec(④ 체크)를 주면 그 장에서 체크된 건수도 센다 (안 건드린 줄은 수정확률 60% 이상이 체크)."""
    f = config.stage(cid, "03_판정") / "results.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = d["rows"]
    dec = dec or {}
    return {"mode": d.get("mode", "law"), "changes": len(d["changes"]), "rows": len(rows),
            "revise": sum(r["p_revise"] >= report.THRESHOLD for r in rows),
            "checked": sum(dec.get(f"{cid}-{r['id']}", {}).get("apply", r["p_revise"] >= report.THRESHOLD) for r in rows),
            "failed": sum(c["failed"] for c in d["changes"]), "laws": d["laws"], "updated": _mtime(f),
            "mtime": f.stat().st_mtime}


def chapter_slots(cid, c):
    out = []
    for s in config.slots(c):
        p = config.source(cid, s["slot"])
        out.append({**s, "file": p.name if p else None, "size": p.stat().st_size if p else 0})
    return out


def chapter_brief(c, dec=None, made=None):
    """made = 책 전체 정오표(xlsx)를 만든 시각(mtime). 그 뒤에 다시 판정한 장은 정오표에 아직 안 들어간 것."""
    cid = c["id"]
    sl = chapter_slots(cid, c)
    need = [s for s in sl if s["file"] or not s.get("optional")]  # 버전 모르는 구판 빈칸은 모자람으로 안 셈
    r = results(cid, dec)
    applied = list(config.stage(cid, "05_반영").glob("정오표_게시판_*.html"))
    return {"id": cid, "title": c["title"], "pages": c["pages"], "mode": c.get("mode", "law"),
            "kind": c.get("kind", "chapter"), "sources": [sum(bool(s["file"]) for s in need), len(need)],
            "revise": r["revise"] if r else None, "applied": len(applied),
            "result": {k: r[k] for k in ("changes", "rows", "revise", "checked", "failed", "updated")} if r else None,
            "in_errata": bool(r and made and made >= r["mtime"]),
            "slots": [{k: x.get(k) for k in ("law", "which", "label", "file", "hint", "optional")} for x in sl],
            "ref_pages": c.get("ref_pages")}


def book_toc(pdf):
    """toc.json — 제안 목록 규칙이 바뀌었으면(예: 「…법률」 추가) 이미 넣은 책도 한 번 다시 훑어 저장."""
    t = config.toc()
    bl = (t or {}).get("baseline")
    if bl is not None and pdf and bl.get("scan_v") != toc.SCAN_V:
        try:
            toc.rescan_suggest(bl, pdf)
            config.save_toc(t)
        except Exception as e:  # 제안 목록일 뿐 — 실패해도 화면은 뜨게
            print(f"[차례] 법령 제안 다시 훑기 실패: {e}")
    return t


def app_state():
    pdf = config.book_pdf()
    dec = errata.load_decisions()
    made = max((p.stat().st_mtime for p in config.ERRATA_DIR.glob("*_정오표.xlsx")), default=None) \
        if config.ERRATA_DIR.exists() else None
    return {"workspace": str(config.WS),
            "book": {"file": pdf.name, "size": pdf.stat().st_size} if pdf else None,
            "toc": book_toc(pdf),
            "chapters": [chapter_brief(c, dec, made) for c in config.chapters()],
            "backups": config.backups(),
            "job": JOB.state(),
            "codex": codex_auth.state(),
            "claude": claude_auth.state(),
            "settings": config.settings(), "engines": config.ENGINES, "engine_now": llm.label(),
            "errata": {"files": errata.files(), "stale": errata.stale()}}


def chapter_state(cid):
    c = config.chapter(cid)
    d = config.chapter_dir(cid)
    stages = []
    for s in config.STAGES:
        files = sorted((p for p in (d / s).rglob("*") if p.is_file() and "cache" not in p.parts),
                       key=lambda p: -p.stat().st_mtime)
        stages.append({"stage": s, "files": [{"name": p.name, "path": str(p.relative_to(d)).replace("\\", "/"),
                                              "time": _mtime(p), "size": p.stat().st_size} for p in files[:40]]})
    return {"chapter": c, "dir": str(d), "slots": chapter_slots(cid, c), "summary": results(cid),
            "review": any((d / "04_검토").glob("*.html")), "stages": stages, "job": JOB.state()}


# ---------- 동작 ----------
POLL_MIN = 30          # 풀리는 시각을 모를 때 다시 해 보는 간격
CHUNK_MIN = 60         # 시각을 알아도 길게는 이만큼씩만 자고 확인
AFTER_RESET_SEC = 90


def keep_awake(on=True):
    """한도를 기다리는 동안 PC 절전을 막는다 (Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
    except Exception:
        pass


def wait_limit(e, deadline):
    """한도가 풀릴 때까지 잔다. 풀렸을 것 같으면 True, 최대 시간을 넘기거나 [멈춤]이면 False."""
    now = time.time()
    if now >= deadline:
        return False
    wake = min(e.until + AFTER_RESET_SEC if e.until and e.until > now else now + POLL_MIN * 60,
               now + CHUNK_MIN * 60, deadline)
    JOB.waiting = datetime.fromtimestamp(wake).strftime("%H:%M")
    print(f"⏸ 사용 한도 — {JOB.waiting}에 다시 해 봅니다. 콘솔 창(run.bat)은 켜 두세요. 기다리는 동안 PC 절전을 막습니다.")
    while time.time() < wake:
        if llm.CANCEL.wait(10):
            JOB.waiting = None
            return False
    JOB.waiting = None
    llm.reset()
    print("▶ 다시 해 봅니다")
    return True


def batch(ids, steps, redo=False):
    """고른 장마다 차례로: fetch = 법제처에서 최근 법령(현행) 받기 · judge = 판정.
    자료가 모자란 장은 건너뛰고 다음 장으로. 이미 판정한 장은 redo 가 아니면 건너뛴다.
    사용 한도면 설정 wait_hours 까지 기다렸다가 같은 장부터 이어서, 로그인 만료 · [멈춤]이면 전체를 멈춘다."""
    chs = [c for c in config.chapters() if c["id"] in ids]
    st = config.settings()
    hours = st["wait_hours"]
    print(f"[일괄] {len(chs)}장 — 판정 엔진 {config.ENGINES[st['engine']]} · 한도면 최대 {hours:g}시간 기다림")
    fresh, bad, skip = set(), [], []
    keep_awake(True)
    try:
        for k, c in enumerate(chs, 1):
            if JOB.stop:
                print(f"[일괄] 멈춤 — {k - 1}/{len(chs)}장까지 했습니다.")
                return
            cid = c["id"]
            deadline = time.time() + hours * 3600
            while True:
                print(f"[일괄] {k}/{len(chs)}장 — {cid} {c['title']}")
                try:
                    if "fetch" in steps:
                        if c.get("mode") == "book" or not c.get("laws"):
                            print(f"[일괄] {cid}장: 법령 비교가 아니라 받을 법령이 없습니다.")
                        else:
                            fetch_law.fetch_chapter(cid, fresh=fresh)
                    if "judge" in steps:
                        if results(cid) and not redo:
                            print(f"[일괄] {cid}장: 이미 판정했습니다 — 건너뜀 (다시 하려면 '판정한 장도 다시'를 켜세요)")
                            skip.append(cid)
                        else:
                            run_pipeline(cid)
                    break
                except LimitError as e:
                    print(f"  {e}")
                    if not wait_limit(e, deadline):
                        if JOB.stop:
                            print(f"[일괄] 멈춤 — {k - 1}/{len(chs)}장까지 했습니다. {cid}장은 끝난 조문까지 기억해 둡니다.")
                            return
                        raise
                except EngineStop as e:
                    if getattr(e, "kind", None) == "cancel":
                        print(f"[일괄] 멈춤 — {k - 1}/{len(chs)}장까지 했습니다. {cid}장은 끝난 조문까지 기억해 둡니다.")
                        return
                    raise
                except SystemExit as e:
                    print(f"[일괄] {cid}장 건너뜀: {e}")
                    bad.append(cid)
                    break
                except (OSError, ValueError) as e:  # 법제처 접속 실패 등 — 다음 장은 계속
                    print(f"[일괄] {cid}장 실패: {e}")
                    bad.append(cid)
                    break
    finally:
        keep_awake(False)
    if bad:
        raise SystemExit(f"{len(bad)}개 장은 끝내지 못했습니다: {', '.join(bad)} — 로그를 보고 장 화면에서 자료를 채운 뒤 다시 누르세요.")


def judge_one(cid):
    try:
        run_pipeline(cid)
    except EngineStop as e:
        if getattr(e, "kind", None) != "cancel":
            raise
        print(f"[판정] 멈춤 — 끝난 조문은 기억해 두므로 [판정 시작]을 다시 누르면 이어서 합니다.")


def errata_zip(tag=""):
    """정오표 파일 zip — tag(전체 · 20이상 …)를 주면 그 단계 한 벌만."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in errata.files():
            if not tag or f"_{tag}_정오표" in f["name"]:
                z.write(config.ERRATA_DIR / f["name"], f["name"])
    return buf.getvalue()


def book_upload(filename, data):
    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("교재는 PDF로 올려 주세요.")
    config.BOOK_DIR.mkdir(parents=True, exist_ok=True)
    for old in config.BOOK_DIR.glob("*.pdf"):
        old.unlink()
    dest = config.BOOK_DIR / Path(filename).name
    dest.write_bytes(data)
    t = toc.extract(dest)
    config.save_toc(t)
    return t


def toc_confirm(body):
    t = config.toc()
    if not t:
        raise ValueError("먼저 교재 PDF를 올려 주세요.")
    bl = t.get("baseline") or {}
    for c in body["chapters"]:  # 화면에서 고른 법령 이름 → 약칭(법/영/칙 …)·책 기준 버전이 붙은 목록
        if "law_names" in c:
            c["laws"] = toc.laws_for(c.pop("law_names"), bl)
    t["chapters"] = body["chapters"]
    t["confirmed"] = True
    t["confirmed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    config.save_toc(t)
    made = []
    existing = {c["id"]: c for c in config.chapters()}
    for c in t["chapters"]:
        if not c.get("include"):
            continue
        prev = existing.get(c["id"], {})
        # 연결 법령이 그대로면 사람이 고른 비교 방식을 유지, 바뀌었으면 법령 유무로 다시 정한다
        same = prev and prev.get("laws") == c.get("laws")
        c = {**c, "id": f"{int(c['no']):02d}" if c.get("no") else c["id"],
             "pages": [int(c["pages"][0]), int(c["pages"][1])],
             "mode": prev["mode"] if same and prev.get("mode") else ("law" if c.get("laws") else "book"),
             "ref_pages": prev.get("ref_pages")}
        made.append(config.save_chapter(c).name)
    return made


def _norm_version(v):
    """'20645, 2025. 1. 7.' · '20645,20250107' → '20645,20250107' (법제처 주소 형식). 못 읽으면 ''.
    책 머리글에서 읽은 '@시행일'(예: '@20250131')은 그대로 둔다."""
    import re
    ef = re.search(r"@(\d{8})\s*$", v or "")
    base = re.sub(r"@\d{8}\s*$", "", v or "")
    m = re.match(r"\D*(\d+)\D+(\d{4})\D*?(\d{1,2})\D+(\d{1,2})\D*$", base) or \
        re.match(r"\D*(\d+)\D+(\d{4})(\d{2})(\d{2})\s*$", base)
    if not m:
        return ""
    return f"{m.group(1)},{m.group(2)}{int(m.group(3)):02d}{int(m.group(4)):02d}" + (f"@{ef.group(1)}" if ef else "")


def book_laws(body):
    """이 책의 법령 목록 저장. 법령 이름만 넣어도 되고(구판 버전은 비워 둠), with_family 면 시행령·시행규칙도 함께."""
    t = config.toc()
    if not t:
        raise ValueError("먼저 교재 PDF를 올려 주세요.")
    bl = t.setdefault("baseline", {"page": None, "laws": [], "source": None})
    old = {l["name"]: l for l in bl.get("laws", [])}
    laws = []
    for l in body.get("laws", []):
        name = " ".join(l["name"].split())
        if not name or any(x["name"] == name for x in laws):
            continue
        prev = old.get(name, {})
        ver = _norm_version(l.get("version", prev.get("version", "")))
        laws.append({**prev, "name": name, "version": ver, "checked": bool(l.get("checked", True)),
                     "header": prev.get("header") or (f"[책 기준 {ver}]" if ver else ""),
                     "line": prev.get("line", "")})
    bl["laws"] = laws
    bl["suggest"] = [x for x in bl.get("suggest", []) if x["name"] not in {l["name"] for l in laws}]
    if not bl.get("source") and laws:
        bl["source"] = "직접 입력"
    if body.get("reattach"):
        toc.attach_laws(t["chapters"], bl, Path(t["book"]).stem)
    config.save_toc(t)
    return t


def ch_upload(cid, slot, filename, data):
    c = config.chapter(cid)
    s = next((x for x in config.slots(c) if x["slot"] == slot), None)
    if not s:
        raise ValueError(f"알 수 없는 칸: {slot}")
    ext = Path(filename).suffix.lower()
    if ext not in s["exts"]:
        raise ValueError(f"{ext} 파일은 받을 수 없습니다. 가능: {', '.join(s['exts'])}  (HWP는 HWPX나 PDF로 저장하세요)")
    d = config.stage(cid, "01_비교자료")
    for old in d.glob(f"{slot}.*"):
        old.unlink()
    dest = d / f"{slot}{ext}"
    dest.write_bytes(data)
    note = ""
    if slot == "ref_book":  # 개정기본서에서 같은 제목의 장을 찾아 쪽 범위를 잡아 둔다
        try:
            rt = toc.extract(dest)
            key = c["title"].replace(" ", "")
            hit = next((x for x in rt["chapters"] if x["title"].replace(" ", "") == key), None)
            if hit:
                c["ref_pages"] = hit["pages"]
                note = f"개정기본서에서 같은 장을 찾았습니다: p.{hit['pages'][0]}–{hit['pages'][1]}"
            else:
                c["ref_pages"] = None
                note = f"개정기본서({rt['page_count']}쪽)에서 같은 제목의 장을 못 찾았습니다. 쪽 범위를 지정하세요."
        except Exception as e:
            note = f"개정기본서 차례 분석 실패: {e}. 쪽 범위를 지정하세요."
        config.save_chapter(c)
    return dest.name, note


def ch_config(cid, body):
    c = config.chapter(cid)
    if body.get("mode") in ("law", "book"):
        c["mode"] = body["mode"]
    if "ref_pages" in body:
        rp = body["ref_pages"]
        c["ref_pages"] = [int(rp[0]), int(rp[1])] if rp and rp[0] and rp[1] else None
    config.save_chapter(c)
    return c


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _q(self):
        u = urllib.parse.urlparse(self.path)
        return urllib.parse.unquote(u.path), {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def do_GET(self):
        path, q = self._q()
        try:
            if path in ("/", "/index.html"):
                return self._send(200, CONSOLE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, app_state())
            if path == "/api/ch":
                return self._send(200, chapter_state(q["id"]))
            if path.startswith("/review/"):
                cid = path.split("/")[2]
                f = next(iter(sorted(config.stage(cid, "04_검토").glob("*.html"))), None)
                if f:
                    return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
                return self._send(404, "아직 판정 결과가 없습니다.", "text/plain; charset=utf-8")
            if path == "/api/errata":
                return self._send(200, errata.state())
            if path == "/zip":
                tag = q.get("level", "")
                name = f"{errata.book_name()}_{tag or '5단계'}_정오표.zip"
                return self._send(200, errata_zip(tag), "application/zip",
                                  {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
            if path.startswith("/errata-files/"):
                f = (config.ERRATA_DIR / path.split("/", 2)[2]).resolve()
                if f.parent == config.ERRATA_DIR.resolve() and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/"):
                        ctype += "; charset=utf-8"
                    extra = {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(f.name)}                         if q.get("dl") or f.suffix == ".xlsx" else {}
                    return self._send(200, f.read_bytes(), ctype, extra)
            if path.startswith("/files/"):
                _, _, cid, rel = path.split("/", 3)
                base = config.chapter_dir(cid).resolve()
                f = (base / rel).resolve()
                if base in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if f.suffix in (".md", ".csv", ".txt", ".json"):
                        ctype = "text/plain; charset=utf-8"
                    elif ctype.startswith("text/"):
                        ctype += "; charset=utf-8"
                    extra = {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(f.name)} \
                        if q.get("dl") else {}
                    return self._send(200, f.read_bytes(), ctype, extra)
            self._send(404, "not found", "text/plain; charset=utf-8")
        except SystemExit as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        path, q = self._q()
        cid = q.get("id")
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        fname = urllib.parse.unquote(self.headers.get("X-Filename", "upload.pdf"))
        busy = {"ok": False, "error": "다른 작업이 실행 중입니다. 끝난 뒤 다시 누르세요."}
        try:
            if path == "/api/book/upload":
                if JOB.running:
                    return self._send(409, busy)
                return self._send(200, {"ok": True, "toc": book_upload(fname, body)})
            if path == "/api/new-book":
                if JOB.running:
                    return self._send(409, busy)
                d = config.backup_current()
                return self._send(200, {"ok": True, "backup": d.name if d else None})
            if path == "/api/restore":
                if JOB.running:
                    return self._send(409, busy)
                d = config.restore(json.loads(body)["name"])
                return self._send(200, {"ok": True, "backup": d.name if d else None})
            if path == "/api/book/toc":
                return self._send(200, {"ok": True, "made": toc_confirm(json.loads(body))})
            if path == "/api/book/toc-codex":
                pdf = config.book_pdf()
                if not pdf:
                    raise ValueError("먼저 교재 PDF를 올려 주세요.")
                ok = JOB.start("AI로 차례 뽑기", None, lambda: (config.save_toc(toc.codex_extract(pdf)),
                                                                print("[차례] Codex 결과를 불러왔습니다. 확인 후 확정하세요.")))
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/book/laws":
                return self._send(200, {"ok": True, "toc": book_laws(json.loads(body))})
            if path == "/api/book/laws-fetch":
                ok = JOB.start("법제처에서 책 법령 받기", None, fetch_law.fetch_book)
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/codex/refresh":
                codex_auth.check_async(deep=True)
                return self._send(200, {"ok": True})
            if path == "/api/codex/login":
                codex_auth.login(relogin=bool((json.loads(body) if body else {}).get("relogin")))
                return self._send(200, {"ok": True})
            if path == "/api/codex/logout":
                return self._send(200, {"ok": True, "out": codex_auth.logout()})
            if path == "/api/claude/refresh":
                claude_auth.check_async(deep=True)
                return self._send(200, {"ok": True})
            if path == "/api/claude/login":
                claude_auth.login()
                return self._send(200, {"ok": True})
            if path == "/api/claude/logout":
                return self._send(200, {"ok": True, "out": claude_auth.logout()})
            if path == "/api/settings":
                return self._send(200, {"ok": True, "settings": config.save_settings(json.loads(body))})
            if path == "/api/ch/upload":
                saved, note = ch_upload(cid, q.get("slot", ""), fname, body)
                return self._send(200, {"ok": True, "saved": saved, "note": note})
            if path == "/api/ch/config":
                return self._send(200, {"ok": True, "chapter": ch_config(cid, json.loads(body))})
            if path == "/api/ch/fetch":
                ok = JOB.start("법제처에서 법령 받기", cid, lambda: fetch_law.fetch_chapter(cid))
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/ch/run":
                ok = JOB.start("개정사항 판정", cid, lambda: judge_one(cid))
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/ch/open":
                d = config.chapter_dir(cid) / q.get("stage", "")
                os.startfile(str(d if d.exists() else config.chapter_dir(cid)))
                return self._send(200, {"ok": True})
            if path == "/api/batch":
                d = json.loads(body)
                if not d.get("ids") or not d.get("steps"):
                    raise ValueError("장을 하나 이상 고르세요.")
                name = {"fetch": "법령 받기", "judge": "판정"}
                label = "일괄 " + " → ".join(name[x] for x in d["steps"])
                ok = JOB.start(label, None, lambda: batch(d["ids"], d["steps"], bool(d.get("redo"))))
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/stop":
                if JOB.running:
                    JOB.stop = True
                    llm.CANCEL.set()
                return self._send(200, {"ok": True})
            if path == "/api/decide":
                errata.save_decisions(json.loads(body)["decisions"])
                return self._send(200, {"ok": True})
            if path == "/api/import-review":
                n, miss, dec = errata.import_review(body, fname)
                return self._send(200, {"ok": True, "n": n, "miss": miss, "decisions": dec})
            if path == "/api/export":
                return self._send(200, {"ok": True, **errata.run((json.loads(body) if body else {}).get("decisions"))})
            if path == "/api/errata/open":
                config.ERRATA_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(str(config.ERRATA_DIR))
                return self._send(200, {"ok": True})
            if path == "/apply":
                d = json.loads(body)
                return self._send(200, apply.run(d, d.get("chapter")))
            self._send(404, {"error": "not found"})
        except (ValueError, KeyError, SystemExit) as e:
            self._send(400, {"ok": False, "error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def log_message(self, *a):
        pass


class Server6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(port=8765, open_browser=True):
    url = f"http://localhost:{port}/"
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError:
        print(f"[server] 이미 실행 중인 것 같습니다 → {url}")
        if open_browser:
            webbrowser.open(url)
        return
    try:  # 'localhost' 가 IPv6(::1)로 풀리는 PC를 위해 ::1 에도 연다
        httpd6 = Server6(("::1", port), H)
        threading.Thread(target=httpd6.serve_forever, daemon=True).start()
    except OSError:
        pass
    config.WS.mkdir(parents=True, exist_ok=True)
    codex_auth.check_async(deep=False)  # 켜질 때는 가볍게 (실제 호출 확인은 좌하단 [새로고침])
    claude_auth.check_async(deep=False)
    print(f"[JEV 개정 에이전트] {url}")
    print(f"[JEV 개정 에이전트] 워크스페이스: {config.WS}")
    print(f"브라우저가 안 열리면 주소창에 {url} 을 직접 입력하세요. (127.0.0.1 말고 localhost)")
    print("이 창을 닫으면 콘솔도 멈춥니다. (종료: Ctrl+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765
    serve(port)
