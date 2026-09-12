"""`dibs serve`: a localhost-only dashboard over the run history that can also
launch checks. Stdlib http.server + Jinja. GET renders live views; POST /check
enqueues a background run (one at a time) whose progress the page polls at
/status. Binds 127.0.0.1 only; it makes outbound requests and writes history, so
it is not purely read-only."""

from __future__ import annotations

import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote

from jinja2 import Environment, PackageLoader, select_autoescape

from . import history
from .config import Config
from .jobs import JobManager
from .models import candidate_from_fields


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("dibs", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["ago"] = _ago
    env.filters["clock"] = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    return env


def _ago(ts: float) -> str:
    secs = max(0, dt.datetime.now().timestamp() - ts)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"


class Handler(BaseHTTPRequestHandler):
    env: Environment = None
    jobs: JobManager = None

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

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                rows = history.latest()
                self._send(self.env.get_template("dashboard.html.j2").render(
                    rows=rows, columns=history.all_columns(rows)))
            elif path == "/status":
                self._json({"jobs": self.jobs.snapshot()})
            elif path.startswith("/name/"):
                slug = unquote(path[len("/name/"):])
                runs = history.timeline(slug)
                if not runs:
                    self._send(self.env.get_template("notfound.html.j2").render(what=slug),
                               status=404)
                    return
                self._send(self.env.get_template("name.html.j2").render(
                    slug=slug, runs=runs, latest=runs[0], columns=history.all_columns(runs)))
            else:
                self._send(self.env.get_template("notfound.html.j2").render(what=path),
                           status=404)
        except Exception as exc:  # noqa: BLE001
            self._send(f"<pre>error: {type(exc).__name__}: {exc}</pre>", status=500)

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/check":
            self._json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        # Repeated fields (chip inputs) arrive as lists; name is single.
        name = (form.get("name", [""])[0]).strip()
        cand = candidate_from_fields(
            name, form.get("legal"), form.get("tm"),
            form.get("domains"), form.get("handles"))
        if cand is None:
            self._json({"error": "name is required"}, status=400)
            return
        self._json({"job_id": self.jobs.submit(cand)})


def serve(config: Config, host: str = "127.0.0.1", port: int = 8787) -> None:
    Handler.env = _env()
    Handler.jobs = JobManager(config)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"dibs dashboard on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
