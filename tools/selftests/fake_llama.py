"""A stand-in for llama.cpp's server, for the selftests: answers /v1/models
and /v1/chat/completions on --port with the model name from --alias."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
alias = args[args.index("--alias") + 1] if "--alias" in args else "fake"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"data": [{"id": alias}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send({"choices": [{"message": {"role": "assistant", "content": "local says hi from " + alias}}]})


HTTPServer(("127.0.0.1", port), H).serve_forever()
