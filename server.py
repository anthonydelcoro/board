#!/usr/bin/env python3
"""
Board - a small self-hosted task and habit tracker.

Standard library only: http.server for the HTTP layer, sqlite3 for storage.
There is nothing to pip install.

Routes
    GET  /              static files from ./static
    GET  /api/state     -> {"version": N, "doc": {...}}
    PUT  /api/state     <- {"version": N, "doc": {...}}
                        -> 200 {"version": N+1}   on success
                        -> 409 {"version": M, "doc": {...}}  if your copy was stale
    GET  /api/health    -> {"ok": true}

Environment
    BOARD_DATA   directory for board.db          (default /data)
    BOARD_PORT   port to listen on               (default 8080)
    BOARD_TOKEN  optional shared secret. If set, a browser must visit
                 /?t=<token> once; the server then stores it in a cookie.
    TZ           REQUIRED for correct 3 AM rollover, e.g. America/New_York
"""

import json
import os
import posixpath
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA = os.environ.get("BOARD_DATA", "/data")
DB = os.path.join(DATA, "board.db")
PORT = int(os.environ.get("BOARD_PORT", "8080"))
TOKEN = os.environ.get("BOARD_TOKEN", "").strip()

MAX_BODY = 8 * 1024 * 1024      # 8 MB is far more than this doc will ever be
HISTORY_KEEP = 50               # rolling snapshots kept for manual recovery

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid():
    return secrets.token_hex(4)


def seed():
    """First-run board. Mirrors the four lists from the original sketch."""
    return {
        "v": 2,
        "cols": [
            {"id": uid(), "title": "School", "kind": "tasks", "collapsed": False,
             "groups": [{"id": uid(), "title": "Class 1", "items": []},
                        {"id": uid(), "title": "Class 2", "items": []},
                        {"id": uid(), "title": "Class 3", "items": []}]},
            {"id": uid(), "title": "Personal", "kind": "tasks", "collapsed": False,
             "groups": [{"id": uid(), "title": "", "items": []}]},
            {"id": uid(), "title": "Daily", "kind": "habits", "cadence": "daily",
             "collapsed": False,
             "groups": [{"id": uid(), "title": "Morning", "items": []},
                        {"id": uid(), "title": "Day", "items": []},
                        {"id": uid(), "title": "Evening", "items": []}]},
            {"id": uid(), "title": "Weekly", "kind": "habits", "cadence": "weekly",
             "collapsed": False,
             "groups": [{"id": uid(), "title": "", "items": []}]},
        ],
    }


# ----------------------------------------------------------------- database

def connect():
    c = sqlite3.connect(DB, timeout=15)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")
    return c


def init_db():
    os.makedirs(DATA, exist_ok=True)
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS state(
                        id      INTEGER PRIMARY KEY CHECK(id = 1),
                        version INTEGER NOT NULL,
                        doc     TEXT    NOT NULL,
                        updated TEXT    NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS history(
                        id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        version INTEGER NOT NULL,
                        doc     TEXT    NOT NULL,
                        updated TEXT    NOT NULL)""")
        if not c.execute("SELECT 1 FROM state WHERE id = 1").fetchone():
            c.execute("INSERT INTO state(id, version, doc, updated) VALUES(1, 1, ?, ?)",
                      (json.dumps(seed()), now()))
            print("[board] seeded a new board at", DB, flush=True)


def read_state():
    with connect() as c:
        v, doc = c.execute("SELECT version, doc FROM state WHERE id = 1").fetchone()
    return v, json.loads(doc)


def write_state(version, doc):
    """Returns (new_version, None) on success, or (None, (version, doc)) on conflict."""
    blob = json.dumps(doc, separators=(",", ":"))
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        current, curdoc = c.execute("SELECT version, doc FROM state WHERE id = 1").fetchone()
        if version != current:
            return None, (current, json.loads(curdoc))
        nv = current + 1
        stamp = now()
        c.execute("UPDATE state SET version = ?, doc = ?, updated = ? WHERE id = 1",
                  (nv, blob, stamp))
        c.execute("INSERT INTO history(version, doc, updated) VALUES(?, ?, ?)",
                  (nv, blob, stamp))
        c.execute("""DELETE FROM history WHERE id NOT IN
                     (SELECT id FROM history ORDER BY id DESC LIMIT ?)""", (HISTORY_KEEP,))
    return nv, None


# ------------------------------------------------------------------ handler

class Handler(BaseHTTPRequestHandler):
    server_version = "board"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[board] %s %s\n" % (self.address_string(), fmt % args))

    # -- helpers

    def send_json(self, code, payload, cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_body(self, code, body, ctype, cookie=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def auth(self, query):
        """Returns (ok, cookie_header_or_None)."""
        if not TOKEN:
            return True, None
        if query.get("t", [None])[0] == TOKEN:
            return True, ("board_token=%s; Path=/; Max-Age=31536000; SameSite=Lax; HttpOnly"
                          % TOKEN)
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        if "board_token" in jar and jar["board_token"].value == TOKEN:
            return True, None
        return False, None

    def deny(self):
        self.send_body(401,
                       "<!doctype html><meta charset=utf-8>"
                       "<body style='font:15px system-ui;background:#1e1f22;color:#f2f3f5;"
                       "padding:40px'>This board needs a token. Open it once as "
                       "<code>/?t=YOUR_TOKEN</code> and it will remember you.</body>",
                       "text/html; charset=utf-8")

    # -- routes

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/api/health":
            return self.send_json(200, {"ok": True})

        ok, cookie = self.auth(query)
        if not ok:
            return self.deny()

        if url.path == "/api/state":
            version, doc = read_state()
            return self.send_json(200, {"version": version, "doc": doc}, cookie)

        return self.static(url.path, cookie)

    def do_PUT(self):
        url = urlparse(self.path)
        ok, cookie = self.auth(parse_qs(url.query))
        if not ok:
            return self.send_json(401, {"error": "unauthorized"})
        if url.path != "/api/state":
            return self.send_json(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self.send_json(400, {"error": "bad body length"})
        try:
            payload = json.loads(self.rfile.read(length))
            version = int(payload["version"])
            doc = payload["doc"]
            if not isinstance(doc, dict) or "cols" not in doc:
                raise ValueError("doc must contain cols")
        except Exception as exc:
            return self.send_json(400, {"error": str(exc)})

        nv, conflict = write_state(version, doc)
        if conflict:
            cv, cdoc = conflict
            return self.send_json(409, {"version": cv, "doc": cdoc}, cookie)
        return self.send_json(200, {"version": nv}, cookie)

    def static(self, path, cookie=None):
        rel = posixpath.normpath(path).lstrip("/")
        if rel in ("", ".", "/"):
            rel = "index.html"
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self.send_body(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            return self.send_body(200, fh.read(), TYPES.get(ext, "application/octet-stream"),
                                  cookie)


def main():
    if not os.path.isdir(STATIC):
        sys.exit("[board] missing static/ directory next to server.py")
    init_db()
    tz = os.environ.get("TZ")
    if not tz:
        print("[board] WARNING: TZ is not set, the 3 AM rollover will use UTC", flush=True)
    print("[board] serving on port %d, data in %s, TZ=%s, token %s"
          % (PORT, DATA, tz or "UTC", "on" if TOKEN else "off"), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
