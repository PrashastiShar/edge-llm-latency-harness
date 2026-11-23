"""
Mock OpenAI-compatible streaming server for tests and CI (no model needed).
Serves one request at a time when --serial is set, like a single-slot CPU server,
so bursty arrivals queue up.

    python tests/mock_server.py --port 21434 --ttft 0.05 --tokens 20 --token-delay 0.01 --serial
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_handler(args, lock):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if args.serial:
                lock.acquire()
            try:
                time.sleep(args.ttft)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")   # like Ollama, vLLM, hosted APIs
                self.send_header("Connection", "close")
                self.end_headers()

                def raw(b):
                    self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
                    self.wfile.flush()

                def send(obj):
                    raw(b"data: " + json.dumps(obj).encode() + b"\n\n")
                for _ in range(args.tokens):
                    send({"choices": [{"delta": {"content": "tok "}}]})
                    time.sleep(args.token_delay)
                send({"choices": [], "usage": {"completion_tokens": args.tokens}})
                raw(b"data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if args.serial:
                    lock.release()
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--ttft", type=float, default=0.05)
    ap.add_argument("--tokens", type=int, default=20)
    ap.add_argument("--token-delay", type=float, default=0.01)
    ap.add_argument("--serial", action="store_true")
    args = ap.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args, threading.Lock())).serve_forever()


if __name__ == "__main__":
    main()
