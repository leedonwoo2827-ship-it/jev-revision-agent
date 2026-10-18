"""JEV 개정 에이전트 — 웹 콘솔 서버 (127.0.0.1 전용, 표준 라이브러리만 사용).

  GET  /                         콘솔 화면 (jev/console.html)
  GET  /api/state                책 · 차례 · 장 목록 · 작업 상태
  POST /api/new-book             새 책 넣기: 지금 워크스페이스를 backup/ 으로 옮기고 비운다
  POST /api/restore              백업 불러오기 (지금 작업은 먼저 백업)
  POST /api/book/upload          교재 PDF 전체 올리기 → 차례 자동 추출
  POST /api/book/toc             차례 확정 (JSON) → 장 워크스페이스 생성
  POST /api/book/toc-codex       Codex로 차례 다시 뽑기 (백그라운드)
  GET  /api/ch?id=09             장 상세 (비교자료 칸 · 판정 요약 · 폴더별 파일)
  POST /api/ch/upload?id=&slot=  비교자료 올리기 (본문 = 파일, X-Filename 헤더)
  POST /api/ch/config?id=        비교 방식(law/book) · 개정기본서 쪽 범위 저장
  POST /api/ch/fetch?id=         법제처에서 구·신 법령 받기 (백그라운드)
  POST /api/ch/run?id=           추출 → 비교 → Codex 판정 → 검토 HTML (백그라운드)
  POST /api/ch/open?id=&stage=   탐색기로 폴더 열기
  GET  /review/<id>              검토 내역 화면
  POST /apply                    [반영]
  GET  /files/<id>/<stage>/<name>[?dl=1]
"""
import json
import mimetypes
import os
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import apply, config, fetch_law, report, toc
from .pipeline import run as run_pipeline

CONSOLE = Path(__file__).with_name("console.html")


class Job:
    """한 번에 하나의 백그라운드 작업. print 출력을 로그로 모은다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.name, self.ch, self.running, self.error = None, None, False, None
        self.log, self.started, self.ended = [], None, None

    def start(self, name, ch, fn):
        with self.lock:
            if self.running:
                return False
            self.name, self.ch, self.running, self.error = name, ch, True, None
            self.log, self.started, self.ended = [], datetime.now(), None
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
            self.error = str(e)
            print(f"[오류] {e}")
            real.write(traceback.format_exc())
        finally:
            sys.stdout = real
            self.running, self.ended = False, datetime.now()

    def state(self):
        return {"name": self.name, "ch": self.ch, "running": self.running, "error": self.error,
                "log": self.log[-300:],
                "started": self.started.strftime("%H:%M:%S") if self.started else None,
                "ended": self.ended.strftime("%H:%M:%S") if self.ended else None}


JOB = Job()


def _mtime(p):
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")


def results(cid):
    f = config.stage(cid, "03_판정") / "results.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = d["rows"]
    return {"mode": d.get("mode", "law"), "changes": len(d["changes"]), "rows": len(rows),
            "revise": sum(r["p_revise"] >= report.THRESHOLD for r in rows),
            "failed": sum(c["failed"] for c in d["changes"]), "laws": d["laws"], "updated": _mtime(f)}


def chapter_slots(cid, c):
    out = []
    for s in config.slots(c):
        p = config.source(cid, s["slot"])
        out.append({**s, "file": p.name if p else None, "size": p.stat().st_size if p else 0})
    return out


def chapter_brief(c):
    cid = c["id"]
    sl = chapter_slots(cid, c)
    r = results(cid)
    applied = list(config.stage(cid, "05_반영").glob("정오표_게시판_*.html"))
    return {"id": cid, "title": c["title"], "pages": c["pages"], "mode": c.get("mode", "law"),
            "kind": c.get("kind", "chapter"), "sources": [sum(bool(s["file"]) for s in sl), len(sl)],
            "revise": r["revise"] if r else None, "applied": len(applied)}


def app_state():
    pdf = config.book_pdf()
    return {"workspace": str(config.WS),
            "book": {"file": pdf.name, "size": pdf.stat().st_size} if pdf else None,
            "toc": config.toc(),
            "chapters": [chapter_brief(c) for c in config.chapters()],
            "backups": config.backups(),
            "job": JOB.state()}


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
        c = {**c, "pages": [int(c["pages"][0]), int(c["pages"][1])],
             "mode": prev.get("mode", "law" if c.get("laws") else "book"),
             "ref_pages": prev.get("ref_pages")}
        made.append(config.save_chapter(c).name)
    return made


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
                ok = JOB.start("Codex로 차례 뽑기", None, lambda: (config.save_toc(toc.codex_extract(pdf)),
                                                                print("[차례] Codex 결과를 불러왔습니다. 확인 후 확정하세요.")))
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/ch/upload":
                saved, note = ch_upload(cid, q.get("slot", ""), fname, body)
                return self._send(200, {"ok": True, "saved": saved, "note": note})
            if path == "/api/ch/config":
                return self._send(200, {"ok": True, "chapter": ch_config(cid, json.loads(body))})
            if path == "/api/ch/fetch":
                ok = JOB.start("법제처에서 법령 받기", cid, lambda: fetch_law.fetch_chapter(cid))
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/ch/run":
                ok = JOB.start("개정사항 판정", cid, lambda: run_pipeline(cid))
                return self._send(200 if ok else 409, {"ok": True} if ok else busy)
            if path == "/api/ch/open":
                d = config.chapter_dir(cid) / q.get("stage", "")
                os.startfile(str(d if d.exists() else config.chapter_dir(cid)))
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


def serve(port=8765, open_browser=True):
    url = f"http://127.0.0.1:{port}/"
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError:
        print(f"[server] 이미 실행 중인 것 같습니다 → {url}")
        if open_browser:
            webbrowser.open(url)
        return
    config.WS.mkdir(parents=True, exist_ok=True)
    print(f"[JEV 개정 에이전트] {url}")
    print(f"[JEV 개정 에이전트] 워크스페이스: {config.WS}")
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
