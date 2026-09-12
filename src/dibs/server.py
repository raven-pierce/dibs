"""`dibs serve`: a localhost-only, read-only dashboard over the run history. Stdlib
http.server, Jinja templates. Renders on each request so new `dibs check` runs
appear live. No auth, no external bind, no browser-initiated searches."""

from __future__ import annotations

import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from jinja2 import Environment, PackageLoader, select_autoescape

from . import history


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
    env: Environment = None  # set on the class before serving

    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                rows = history.latest()
                html = self.env.get_template("dashboard.html.j2").render(
                    rows=rows, columns=history.all_columns(rows))
                self._send(html)
            elif path.startswith("/name/"):
                slug = unquote(path[len("/name/"):])
                runs = history.timeline(slug)
                if not runs:
                    self._send(self._not_found(slug), status=404)
                    return
                html = self.env.get_template("name.html.j2").render(
                    slug=slug, runs=runs, latest=runs[0],
                    columns=history.all_columns(runs))
                self._send(html)
            else:
                self._send(self._not_found(path), status=404)
        except Exception as exc:  # noqa: BLE001 - never 500 a blank page
            self._send(f"<pre>error: {type(exc).__name__}: {exc}</pre>", status=500)

    def _not_found(self, what: str) -> str:
        return self.env.get_template("notfound.html.j2").render(what=what)


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    Handler.env = _env()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"dibs dashboard on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
