from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .mail_response import import_mail_response


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def handle_mail_response_request(
    payload: dict[str, Any],
    *,
    tracker_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    try:
        result = import_mail_response(payload, tracker_path=tracker_path)
    except Exception as exc:
        return 400, {"ok": False, "error": str(exc)}
    return (
        200,
        {
            "ok": True,
            "status": result.status,
            "matched_by": result.matched_by,
            "status_at": result.event["status_at"],
        },
    )


def make_handler(tracker_path: Path | None = None):
    class MailResponseHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/import-mail-response":
                self._write_json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw)
            except Exception as exc:
                self._write_json(400, {"ok": False, "error": f"invalid JSON: {exc}"})
                return
            if not isinstance(payload, dict):
                self._write_json(400, {"ok": False, "error": "payload must be object"})
                return
            status, body = handle_mail_response_request(
                payload,
                tracker_path=tracker_path,
            )
            self._write_json(status, body)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_json(self, status: int, body: dict[str, Any]) -> None:
            response = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    return MailResponseHandler


def run_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    tracker_path: Path | None = None,
) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(tracker_path))
    print(f"mail_response_webhook=http://{host}:{port}/import-mail-response")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tracker-path", type=Path, default=None)
    args = parser.parse_args(argv)
    run_server(host=args.host, port=args.port, tracker_path=args.tracker_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
