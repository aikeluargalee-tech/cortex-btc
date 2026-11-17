"""Local HTTP server: JSON API plus the static dashboard.

Binds to loopback only. No authentication because there is no network
exposure, no exchange key and no execution capability.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .app import AppError, CortexApp

WEB_ROOT = Path(__file__).resolve().parent / "web"
ALLOWED_FILES = {"index.html", "app.js", "style.css", "favicon.svg"}


class Handler(BaseHTTPRequestHandler):
    app: CortexApp
    server_version = "CORTEX-BTC/0.2"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if os.environ.get("CORTEX_VERBOSE"):
            super().log_message(fmt, *args)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise AppError(f"invalid JSON body: {error}") from error
        if not isinstance(payload, dict):
            raise AppError("request body must be a JSON object")
        return payload

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        if name not in ALLOWED_FILES:
            self._error("not found", 404)
            return
        target = WEB_ROOT / name
        if not target.exists():
            self._error("not found", 404)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                if path == "/api/health":
                    self._json({"ok": True, "version": "0.2.0", "mode": "market-stance"})
                    return
                if path == "/api/state":
                    force = query.get("refresh", ["0"])[0] in ("1", "true")
                    self._json(self.app.build_state(force=force))
                    return
                if path == "/api/config/versions":
                    self._json({"versions": self.app.store.config_versions(), "history": self.app.config_history()})
                    return
                if path == "/api/decisions":
                    limit = int(query.get("limit", ["50"])[0])
                    self._json({"decisions": self.app.store.decisions(limit=limit)})
                    return
                if path == "/api/export/decisions.json":
                    self._json({"decisions": self.app.export_decisions()})
                    return
                if path == "/api/research":
                    report = self.app.research()
                    if report is None:
                        self._json({"ok": False, "reason": "no study has been run yet"}, 404)
                        return
                    self._json(report)
                    return
                self._error("not found", 404)
                return
            self._static(path)
        except AppError as error:
            self._error(str(error), error.status)
        except Exception as error:  # noqa: BLE001
            self._error(f"internal error: {error}", 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._body()
            if path == "/api/refresh":
                self._json(self.app.build_state(force=True))
                return
            if path == "/api/research/refresh":
                from .research import run_veto_study, save_report

                report = run_veto_study()
                save_report(report, self.app.research_path)
                self._json(report)
                return
            if path == "/api/config":
                changes = payload.get("changes") or {}
                if not isinstance(changes, dict) or not changes:
                    raise AppError("changes must be a non-empty object of dotted paths")
                config = self.app.change_config(
                    changes,
                    operator=str(payload.get("operator", "")),
                    note=str(payload.get("note", "")),
                )
                self._json({"ok": True, "config": config.to_dict()})
                return
            self._error("not found", 404)
        except AppError as error:
            self._error(str(error), error.status)
        except Exception as error:  # noqa: BLE001
            self._error(f"internal error: {error}", 500)


def serve(app: CortexApp, host: str = "127.0.0.1", port: int = 8787) -> None:
    Handler.app = app
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"CORTEX-BTC dashboard: http://{host}:{port}", flush=True)
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(__file__).resolve().parents[1]
    db = os.environ.get("CORTEX_DB", str(root / "data" / "cortex.db"))
    seed = os.environ.get("CORTEX_SEED_CONFIG", str(root / "config" / "research-seed.json"))
    port = int(os.environ.get("CORTEX_PORT", "8787"))
    app = CortexApp(db, seed_config=seed)
    serve(app, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
