"""Serve the built Recast frontend over HTTPS and proxy Lens bridge calls.

This is a small demo helper for iPhone camera testing. It avoids relying on a
long-running Vite dev process and keeps browser calls same-origin:

  https://<mac-ip>:5173/api/recast-lens/* -> http://<gn100-ip>:8910/api/recast-lens/*
"""

import argparse
import http.client
import os
import ssl
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class Handler(SimpleHTTPRequestHandler):
    bridge_host = "172.16.94.151"
    bridge_port = 8910

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path == "/health" or self.path.startswith("/api/recast-lens/"):
            self.proxy_to_bridge()
            return
        if self.path == "/" or not os.path.splitext(urlparse(self.path).path)[1]:
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/recast-lens/"):
            self.proxy_to_bridge()
            return
        self.send_error(404)

    def do_OPTIONS(self):
        if self.path.startswith("/api/recast-lens/"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Recast-Session, X-Recast-Device, X-Recast-Source")
            self.end_headers()
            return
        self.send_error(404)

    def proxy_to_bridge(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() in {"content-type", "x-recast-session", "x-recast-device", "x-recast-source"}
        }
        conn = http.client.HTTPConnection(self.bridge_host, self.bridge_port, timeout=10)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            res = conn.getresponse()
            payload = res.read()
        except OSError as exc:
            message = f"Lens bridge unavailable: {exc}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return
        finally:
            conn.close()

        self.send_response(res.status)
        for key, value in res.getheaders():
            if key.lower() not in {"connection", "transfer-encoding", "server", "date"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--bridge-host", default="172.16.94.151")
    parser.add_argument("--bridge-port", type=int, default=8910)
    args = parser.parse_args()

    Handler.bridge_host = args.bridge_host
    Handler.bridge_port = args.bridge_port
    dist = os.path.abspath(args.dist)
    cert = os.path.abspath(args.cert)
    key = os.path.abspath(args.key)
    os.chdir(dist)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert, keyfile=key)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    print(f"[recast-frontend] https://{args.host}:{args.port}")
    print(f"[recast-frontend] proxy -> http://{args.bridge_host}:{args.bridge_port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
