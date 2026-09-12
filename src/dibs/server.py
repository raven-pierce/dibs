"""`dibs serve`: a localhost dashboard plus a JSON API. The HTML routes render the
history live; the /api/v1/* routes let an agent query history and launch screens.
Both share the single-worker job queue. Binds 127.0.0.1 only; it makes outbound
requests and writes history, so it is not read-only. No auth (loopback is the
boundary) — anything on the host can call it."""

from __future__ import annotations

import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from jinja2 import Environment, PackageLoader, select_autoescape

from . import __version__, history
from .checkers import ALL_CHECKERS
from .config import Config
from .jobs import JobManager
from .models import candidate_from_fields

WAIT_DEFAULT = 120.0
WAIT_CAP = 300.0


def _env() -> Environment:
    env = Environment(loader=PackageLoader("dibs", "templates"),
                      autoescape=select_autoescape(["html"]))
    env.filters["ago"] = _ago
    env.filters["clock"] = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    return env


def _ago(ts: float) -> str:
    secs = max(0, dt.datetime.now().timestamp() - ts)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"


def _sources(config: Config) -> list[dict]:
    out = []
    for cls in ALL_CHECKERS:
        inst = cls()
        enabled = config.checker_enabled(inst.name)
        blocked = inst.requires(config) if enabled else None
        out.append({"name": inst.name, "columns": list(inst.columns),
                    "enabled": enabled and not blocked, "blocked": blocked})
    return out


class Handler(BaseHTTPRequestHandler):
    env: Environment = None
    jobs: JobManager = None
    config: Config = None

    def log_message(self, *args):
        pass

    def _send(self, body, status=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, status=200):
        self._send(json.dumps(obj), status=status, ctype="application/json")

    # --- routing ---------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path.startswith("/api/v1"):
                self._api_get(path[len("/api/v1"):] or "/")
            elif path == "/":
                rows = history.latest()
                self._send(self.env.get_template("dashboard.html.j2").render(
                    rows=rows, columns=history.all_columns(rows)))
            elif path.startswith("/name/"):
                self._name_page(unquote(path[len("/name/"):]))
            else:
                self._send(self.env.get_template("notfound.html.j2").render(what=path),
                           status=404)
        except Exception as exc:  # noqa: BLE001
            self._send(f"<pre>error: {type(exc).__name__}: {exc}</pre>", status=500)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/v1/screen":
            self._api_screen()
        else:
            self._json({"error": "not found"}, status=404)

    # --- HTML ------------------------------------------------------------
    def _name_page(self, slug):
        runs = history.timeline(slug)
        if not runs:
            self._send(self.env.get_template("notfound.html.j2").render(what=slug), status=404)
            return
        self._send(self.env.get_template("name.html.j2").render(
            slug=slug, runs=runs, latest=runs[0], columns=history.all_columns(runs)))

    # --- API -------------------------------------------------------------
    def _api_get(self, sub):
        if sub == "/health":
            self._json({"status": "ok", "version": __version__,
                        "names": len(history.latest()),
                        "jobs_running": self.jobs.running_count()})
        elif sub == "/sources":
            self._json(_sources(self.config))
        elif sub == "/names":
            self._json(history.latest())
        elif sub.startswith("/names/"):
            slug = unquote(sub[len("/names/"):])
            runs = history.timeline(slug)
            if not runs:
                self._json({"error": f"no history for '{slug}'"}, status=404)
                return
            self._json({"slug": slug, "latest": runs[0], "runs": runs,
                        "columns": history.all_columns(runs)})
        elif sub == "/jobs":
            self._json({"jobs": self.jobs.snapshot()})
        elif sub.startswith("/jobs/"):
            job = self.jobs.job(unquote(sub[len("/jobs/"):]))
            if job is None:
                self._json({"error": "no such job"}, status=404)
            else:
                self._json(job)
        else:
            self._json({"error": "not found"}, status=404)

    def _api_screen(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"error": "invalid JSON body"}, status=400)
            return
        cand = candidate_from_fields(body.get("name"), body.get("legal"), body.get("tm"),
                                     body.get("domains"), body.get("handles"))
        if cand is None:
            self._json({"error": "name is required"}, status=400)
            return
        jid = self.jobs.submit(cand, only=body.get("only"), skip=body.get("skip"),
                               quick=bool(body.get("quick")))
        if body.get("wait"):
            timeout = min(float(body.get("wait_seconds") or WAIT_DEFAULT), WAIT_CAP)
            self._json(self.jobs.wait(jid, timeout) or {"error": "job vanished"})
        else:
            self._json(self.jobs.job(jid), status=202)


def serve(config: Config, host: str = "127.0.0.1", port: int = 8787) -> None:
    Handler.env = _env()
    Handler.config = config
    Handler.jobs = JobManager(config)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"dibs dashboard + API on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
