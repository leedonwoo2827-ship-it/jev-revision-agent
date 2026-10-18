"""로컬 검토 서버: errata HTML 서빙 + POST /apply → jev.apply 실행. (127.0.0.1 전용)"""
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import apply, config


def serve(ch, port=8765, open_browser=True):
    page = config.OUTPUT / f"errata_{ch}.html"

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html", f"/errata_{ch}.html"):
                self._send(200, page.read_bytes())
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            if self.path != "/apply":
                return self._send(404, "not found", "text/plain; charset=utf-8")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                result = apply.run(body, ch)
                self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e:
                self._send(500, f"반영 실패: {e}", "text/plain; charset=utf-8")

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}/"
    print(f"[server] {url}  (종료: Ctrl+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import sys
    serve(sys.argv[1] if len(sys.argv) > 1 else "ch09")
