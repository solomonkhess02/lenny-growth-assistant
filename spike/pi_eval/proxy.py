"""SPIKE ONLY. Logging reverse proxy in front of Ollama's OpenAI-compatible API.

Captures the EXACT request body Pi (or anything else) sends, so prompt overhead
is measured from the wire rather than estimated. Forwards verbatim to Ollama
and returns the real response, so the client under test is unaffected.
"""
import http.server, json, socketserver, threading, urllib.request, sys, time
from pathlib import Path

UPSTREAM = "http://127.0.0.1:11434"
LOG = Path("captures")
LOG.mkdir(exist_ok=True)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11435
TAG = sys.argv[2] if len(sys.argv) > 2 else "run"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"_unparsed": body[:400].decode(errors="replace")}
        stamp = f"{TAG}-{int(time.time()*1000)}"
        (LOG / f"{stamp}.request.json").write_text(
            json.dumps(parsed, indent=1, ensure_ascii=False), encoding="utf-8")

        req = urllib.request.Request(
            UPSTREAM + self.path, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        # Stream the upstream body through unbuffered. Buffering an SSE
        # response would stall any client that waits on incremental deltas,
        # and streaming is a locked requirement of this project.
        try:
            r = urllib.request.urlopen(req, timeout=600)
            code, ctype = r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            body = e.read()
            (LOG / f"{stamp}.response.json").write_bytes(body[:200000])
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        captured = bytearray()
        try:
            while True:
                chunk = r.read(1024)
                if not chunk:
                    break
                captured.extend(chunk)
                self.wfile.write(b"%X
" % len(chunk) + chunk + b"
")
                self.wfile.flush()
            self.wfile.write(b"0

")
            self.wfile.flush()
        finally:
            r.close()
            (LOG / f"{stamp}.response.json").write_bytes(bytes(captured[:400000]))

    def do_GET(self):
        try:
            with urllib.request.urlopen(UPSTREAM + self.path, timeout=60) as r:
                data, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"proxy on {PORT} -> {UPSTREAM}, tag={TAG}", flush=True)
    Server(("127.0.0.1", PORT), Handler).serve_forever()
