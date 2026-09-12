"""JSON API: endpoints against an in-process server, and in-flight dedupe."""

from __future__ import annotations

import asyncio
import threading
from copy import deepcopy
from http.server import ThreadingHTTPServer

import httpx
import pytest

from dibs.config import DEFAULTS, Config
from dibs.jobs import JobManager
from dibs.models import Candidate
from dibs.run import RunOutcome
from dibs.server import Handler, _env


def _disabled_config() -> Config:
    cfg = Config(deepcopy(DEFAULTS))
    for k in cfg.data["checkers"]:
        cfg.data["checkers"][k] = False  # instant screen, no network
    return cfg


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Handler.env = _env()
    Handler.config = _disabled_config()
    Handler.jobs = JobManager(Handler.config)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
        yield c
    httpd.shutdown()
    httpd.server_close()


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and "version" in body and body["names"] == 0


def test_sources(client):
    r = client.get("/api/v1/sources")
    names = {s["name"] for s in r.json()}
    assert {"rdap", "uspto", "cipo", "req"} <= names


def test_screen_wait_then_read(client):
    r = client.post("/api/v1/screen", json={"name": "ApiTest", "tm": ["ApiTest"], "wait": True})
    assert r.status_code == 200
    job = r.json()
    assert job["state"] == "done" and job["slug"] == "apitest"
    assert job["result"]["name"] == "ApiTest"

    names = client.get("/api/v1/names").json()
    assert any(n["slug"] == "apitest" for n in names)

    detail = client.get("/api/v1/names/apitest").json()
    assert detail["latest"]["slug"] == "apitest" and len(detail["runs"]) >= 1

    job2 = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert job2["id"] == job["id"]


def test_rerun_preserves_input(client):
    client.post("/api/v1/screen",
                json={"name": "Rer", "tm": ["Rer"], "domains": ["rer", "getrer"], "wait": True})
    run_id = client.get("/api/v1/names/rer").json()["latest"]["id"]

    # By slug: reruns the latest with the same dimensions.
    r2 = client.post("/api/v1/names/rer/rerun", json={"wait": True})
    assert r2.status_code == 200 and r2.json()["state"] == "done"
    latest = client.get("/api/v1/names/rer").json()
    assert len(latest["runs"]) >= 2
    assert latest["latest"]["input"]["domains"] == ["rer", "getrer"]

    # By run id: fetch and rerun a specific run.
    got = client.get(f"/api/v1/runs/{run_id}")
    assert got.status_code == 200 and got.json()["id"] == run_id
    rr = client.post(f"/api/v1/runs/{run_id}/rerun", json={"wait": True})
    assert rr.json()["state"] == "done"

    # Latest overall.
    assert client.post("/api/v1/rerun", json={"wait": True}).json()["state"] == "done"


def test_rerun_missing(client):
    assert client.post("/api/v1/names/nope/rerun").status_code == 404
    assert client.post("/api/v1/runs/999999/rerun").status_code == 404
    assert client.get("/api/v1/runs/999999").status_code == 404


def test_screen_async_returns_202(client):
    r = client.post("/api/v1/screen", json={"name": "AsyncName"})
    assert r.status_code == 202
    assert r.json()["state"] in ("queued", "running", "done")


def test_errors(client):
    assert client.get("/api/v1/names/nope").status_code == 404
    assert client.get("/api/v1/jobs/nope").status_code == 404
    assert client.post("/api/v1/screen", json={"legal": ["x"]}).status_code == 400
    assert client.post("/api/v1/screen", content=b"not json").status_code == 400


def test_inflight_dedupe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def slow_run(cands, config, **kw):
        await asyncio.sleep(0.4)
        out = RunOutcome()
        out.results = {cands[0].display: []}
        return out

    monkeypatch.setattr("dibs.jobs.run_screening", slow_run)
    jm = JobManager(_disabled_config())
    c = Candidate("Kestrel", tm=("Kestrel",))
    a = jm.submit(c, quick=False)
    b = jm.submit(Candidate("Kestrel", tm=("Kestrel",)))  # identical, still in flight
    assert a == b  # deduped
    # A different request is not deduped.
    d = jm.submit(Candidate("Kestrel", tm=("Other",)))
    assert d != a
