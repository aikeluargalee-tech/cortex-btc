"""Local HTTP server: JSON API plus the static dashboard.

Binds to loopback only. No authentication because there is no network
exposure, no exchange key and no execution capability.
"""

from __future__ import annotations

import hmac
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

LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CORTEX-BTC</title>
<style>
body{background:#05090f;color:#dee8f3;font-family:system-ui,sans-serif;display:grid;place-items:center;height:100vh;margin:0}
form{border:1px solid #1b2b3d;background:#0b131d;padding:28px 26px;border-radius:10px;width:320px}
h1{font-size:14px;letter-spacing:.16em;text-transform:uppercase;color:#9fb1c4;margin:0 0 18px}
input{width:100%;box-sizing:border-box;background:#08111b;border:1px solid #1b2b3d;color:#dee8f3;
padding:9px 10px;border-radius:7px;font-family:ui-monospace,monospace;font-size:13px}
button{margin-top:12px;width:100%;padding:9px;border:0;border-radius:7px;background:#2ee6a8;color:#04140e;font-weight:600;cursor:pointer}
p{font-size:11px;color:#74879b;line-height:1.6;margin:12px 0 0}
</style></head><body>
<form method="get">
<h1>CORTEX-BTC access</h1>
<input name="token" placeholder="access token" autofocus autocomplete="off" />
<button type="submit">Enter</button>
<p>Private instance. A token is required to view the stance.</p>
</form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    app: CortexApp
    server_version = "CORTEX-BTC/0.2"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if os.environ.get("CORTEX_VERBOSE"):
            super().log_message(fmt, *args)

    # -- access control ----------------------------------------------------
    def _cookie_header(self) -> str | None:
        return getattr(self, "_set_cookie", None)

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        self._set_cookie = None
        token = os.environ.get("CORTEX_TOKEN", "").strip()
        if not token:
            return True
        supplied = (self.headers.get("X-CORTEX-Token") or "").strip()
        from_query = False
        if not supplied:
            supplied = (query.get("token") or [""])[0].strip()
            from_query = bool(supplied)
        if not supplied:
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "cortex_token":
                    supplied = value.strip()
        if not supplied or not hmac.compare_digest(supplied, token):
            return False
        if from_query:
            self._set_cookie = f"cortex_token={token}; HttpOnly; Path=/; SameSite=Lax; Max-Age=2592000"
        return True

    def _login(self, status: int = 401) -> None:
        body = LOGIN_PAGE.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        cookie = self._cookie_header()
        if cookie:
            self.send_header("Set-Cookie", cookie)
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
        cookie = self._cookie_header()
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if not self._authorised(query):
            if path.startswith("/api/"):
                self._error("unauthorised", 401)
            else:
                self._login()
            return
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
        query = parse_qs(urlparse(self.path).query)
        if not self._authorised(query):
            self._error("unauthorised", 401)
            return
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
    if host not in ("127.0.0.1", "localhost") and not os.environ.get("CORTEX_TOKEN", "").strip():
        print("WARNING: bound to a network interface without CORTEX_TOKEN — anyone on the network can read this.", flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"CORTEX-BTC dashboard: http://{host}:{port}", flush=True)
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(__file__).resolve().parents[1]
    db = os.environ.get("CORTEX_DB", str(root / "data" / "cortex.db"))
    seed = os.environ.get("CORTEX_SEED_CONFIG", str(root / "config" / "research-seed.json"))
    host = os.environ.get("CORTEX_HOST", "127.0.0.1")
    port = int(os.environ.get("CORTEX_PORT", "8787"))
    app = CortexApp(db, seed_config=seed)
    serve(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
