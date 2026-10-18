"""웹 콘솔 서버 (127.0.0.1 전용, 표준 라이브러리만 사용).

  GET  /                       콘솔 화면 (jev/console.html)
  GET  /api/state?ch=          장 목록 · 입력파일 상태 · 작업 로그 · 결과/반영 파일
  POST /api/upload?ch=&slot=   파일 업로드 → _context (본문 = 파일, X-Filename 헤더 = 원래 이름)
  POST /api/fetch?ch=          법제처에서 구/신 법령 받기 (백그라운드)
  POST /api/run?ch=            추출 → 비교 → Codex 판정 → 검토 HTML (백그라운드)
  GET  /review/<ch>            검토 화면 (output/errata_<ch>.html)
  POST /apply                  [반영]
  GET  /files/<path>           output/ 아래 파일 내려받기
"""
import json
import mimetypes
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import apply, config, diff_law, extract_book, fetch_law, judge_codex, report

CONSOLE = Path(__file__).with_name("console.html")
EXTS = {".pdf", ".hwpx", ".txt"}


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
        real = sys.stdout
        job = self

        class Tee:
            def write(self, s):
                real.write(s)
                for line in s.splitlines():
                    if line.strip():
                        job.log.append(line)
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


def stem(pattern):
    """'_context/law_old_령.*' → 'law_old_령' (업로드·받기 파일명을 설정 패턴에 맞춘다)"""
    return Path(pattern).name.split(".")[0]


def slots(cfg, ch):
    """업로드 칸: 교재 1 + 법령별 구/신."""
    c = config.chapter(cfg, ch)
    out = [{"slot": "book", "label": "교재 PDF (원본 그대로)", "pattern": cfg["book_pdf"], "exts": [".pdf"]}]
    for law in c["laws"]:
        for which, tag in (("old", "책 기준 (구)"), ("new", "현행 (신)")):
            out.append({"slot": stem(law[which]), "label": f"{law['name']} — {tag}",
                        "pattern": law[which], "exts": sorted(EXTS),
                        "hint": law.get("old_version", "") if which == "old" else "현행"})
    for s in out:
        p = config.ROOT / s["pattern"] if s["slot"] == "book" else config.resolve(s["pattern"])
        s["file"] = p.name if p and Path(p).exists() else None
        s["size"] = Path(p).stat().st_size if s["file"] else 0
    return out


def state(ch):
    cfg = config.load()
    wd = config.WORK / ch
    res = wd / "results.json"
    summary = None
    if res.exists():
        d = json.loads(res.read_text(encoding="utf-8"))
        rows = d["rows"]
        summary = {"changes": len(d["changes"]), "rows": len(rows),
                   "revise": sum(r["p_revise"] >= report.THRESHOLD for r in rows),
                   "failed": sum(c["failed"] for c in d["changes"]),
                   "laws": d["laws"],
                   "updated": datetime.fromtimestamp(res.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
    applied = config.OUTPUT / "applied"
    files = sorted((p for p in applied.glob(f"*{ch}*") if p.is_file()), key=lambda p: -p.stat().st_mtime) \
        if applied.exists() else []
    return {"chapters": [{"id": k, "title": v["title"], "pages": v["pages"]} for k, v in cfg["chapters"].items()],
            "ch": ch, "slots": slots(cfg, ch), "summary": summary,
            "review": (config.OUTPUT / f"errata_{ch}.html").exists(),
            "applied": [{"name": p.name, "path": str(p.relative_to(config.OUTPUT)).replace("\\", "/"),
                         "time": datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")} for p in files[:30]],
            "job": JOB.state()}


def do_upload(ch, slot, filename, data):
    cfg = config.load()
    s = next((x for x in slots(cfg, ch) if x["slot"] == slot), None)
    if not s:
        raise ValueError(f"알 수 없는 칸: {slot}")
    ext = Path(filename).suffix.lower()
    if ext not in s["exts"]:
        raise ValueError(f"{ext} 파일은 받을 수 없습니다. 가능: {', '.join(s['exts'])}  (HWP는 HWPX나 PDF로 저장하세요)")
    ctx = config.ROOT / "_context"
    ctx.mkdir(exist_ok=True)
    if slot == "book":
        dest = config.ROOT / cfg["book_pdf"]
    else:
        for old in ctx.glob(f"{slot}.*"):  # 같은 칸의 다른 형식 파일은 지워 혼동을 막는다
            old.unlink()
        dest = ctx / f"{slot}{ext}"
    dest.write_bytes(data)
    return dest.name


def do_fetch(ch):
    c = config.chapter(config.load(), ch)
    ctx = config.ROOT / "_context"
    ctx.mkdir(exist_ok=True)
    for law in c["laws"]:
        name = law.get("fetch_name") or law["name"]
        for which, ver in (("new", None), ("old", law.get("old_version"))):
            if which == "old" and not ver:
                print(f"[fetch] {law['name']}: chapters.yaml 에 old_version 이 없어 구판은 건너뜀")
                continue
            print(f"[fetch] {name} {ver or '(현행)'} 받는 중 ...")
            text = fetch_law.fetch(name, ver)
            dest = ctx / f"{stem(law[which])}.txt"
            dest.write_text(text, encoding="utf-8")
            others = [p.name for p in ctx.glob(f"{stem(law[which])}.*") if p.suffix.lower() in (".pdf", ".hwpx")]
            note = f"  (※ {', '.join(others)} 이(가) 있어 그 파일이 우선 사용됩니다)" if others else ""
            print(f"[fetch] → {dest.name} ({len(text):,}자){note}")


def do_run(ch):
    extract_book.run(ch)
    _, changes, missing = diff_law.run(ch)
    if missing:
        raise SystemExit("법령 파일이 모자랍니다. 위 칸에 올리거나 [법제처에서 받기]를 누르세요.")
    if not changes:
        print("[run] 구/신 법령에 차이가 없습니다. 파일 버전을 확인하세요.")
    judge_codex.run(ch)
    report.run(ch)


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
        return u.path, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def do_GET(self):
        path, q = self._q()
        ch = q.get("ch", "ch09")
        try:
            if path in ("/", "/index.html"):
                return self._send(200, CONSOLE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, state(ch))
            if path.startswith("/review/"):
                f = config.OUTPUT / f"errata_{path.split('/')[2]}.html"
                if f.exists():
                    return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
                return self._send(404, "아직 판정 결과가 없습니다.", "text/plain; charset=utf-8")
            if path.startswith("/files/"):
                rel = urllib.parse.unquote(path[len("/files/"):])
                f = (config.OUTPUT / rel).resolve()
                if config.OUTPUT.resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or f.suffix in (".md", ".csv"):
                        ctype = ("text/plain" if f.suffix in (".md", ".csv") else ctype) + "; charset=utf-8"
                    extra = {}
                    if q.get("dl"):
                        extra["Content-Disposition"] = "attachment; filename*=UTF-8''" + urllib.parse.quote(f.name)
                    return self._send(200, f.read_bytes(), ctype, extra)
            self._send(404, "not found", "text/plain; charset=utf-8")
        except SystemExit as e:
            self._send(400, {"error": str(e)})

    def do_POST(self):
        path, q = self._q()
        ch = q.get("ch", "ch09")
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            if path == "/api/upload":
                name = urllib.parse.unquote(self.headers.get("X-Filename", "upload.pdf"))
                saved = do_upload(ch, q.get("slot", ""), name, body)
                return self._send(200, {"ok": True, "saved": saved})
            if path == "/api/fetch":
                ok = JOB.start("법제처에서 받기", ch, lambda: do_fetch(ch))
                return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 실행 중입니다."})
            if path == "/api/run":
                ok = JOB.start("판정 실행", ch, lambda: do_run(ch))
                return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 실행 중입니다."})
            if path == "/apply":
                d = json.loads(body)
                return self._send(200, apply.run(d, d.get("chapter", ch)))
            self._send(404, {"error": "not found"})
        except (ValueError, SystemExit) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *a):
        pass


def serve(port=8765, open_browser=True):
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError:
        url = f"http://127.0.0.1:{port}/"
        print(f"[server] 이미 실행 중인 것 같습니다 → {url}")
        if open_browser:
            webbrowser.open(url)
        return
    url = f"http://127.0.0.1:{port}/"
    print(f"[server] 웹 콘솔: {url}")
    print("[server] 이 창을 닫으면 콘솔도 멈춥니다. (종료: Ctrl+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765
    serve(port)
