#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FLOOR BID — a small turn-based strategy game for humans and agents.

Rules (one paragraph)
--------------------
Each player starts with 12 credits and 0 points. Six lots are revealed one at a
time, each with a public value (2-7 points). Every round all players submit a
secret bid in whole credits, simultaneously. The highest bid pays its bid and
scores the lot value; ties go to the player with fewer points, then to the lower
seat. A bid of 0 is a pass: if everyone passes, the lot is unsold. After six
lots the highest score wins; ties are broken by credits left. Match under ten
minutes.

HTTP/JSON API (no auth beyond the per-seat key returned by /api/join)
--------------------------------------------------------------------
GET  api/rules                       -> {"rules": "..."}
POST api/new     {name}              -> {"code": "ABCD"}
POST api/join    {code, name}        -> {"seat": 0, "key": "..."}
POST api/start   {code, key}         -> fills empty seats with bots, deals lot 1
GET  api/state   ?code=ABCD&key=...  -> full public state (+ your private bid)
POST api/bid     {code, key, bid}    -> {"accepted": true, "waiting_for": n}
GET  api/log     ?code=ABCD          -> {"log": ["..."]}

Served from the standard library only; single process, in-memory state.
"""
from __future__ import annotations

import json
import os
import random
import re
import string
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
LOTS = [3, 5, 2, 6, 4, 7]          # fixed schedule: same for every match
START_CREDITS = 12
BID_SECONDS = 75
REVEAL_SECONDS = 6
MAX_SEATS = 4
LOCK = threading.RLock()
GAMES: dict[str, dict] = {}

RULES = (
    "Six lots, values 3-5-2-6-4-7, one per round. Every player starts with 12 "
    "credits and 0 points. Each round all players bid secretly and at the same "
    "time in whole credits. Highest bid pays its bid and scores the lot. Ties: "
    "fewer points wins the lot, then the lower seat. All pass = lot unsold. "
    "After six lots the highest score wins; ties broken by credits left."
)


def now() -> float:
    return time.time()


def new_code() -> str:
    while True:
        c = "".join(random.choice(string.ascii_uppercase) for _ in range(4))
        if c not in GAMES:
            return c


def new_key() -> str:
    return "".join(random.choice("abcdef0123456789") for _ in range(24))


def public(g: dict, seat: int | None) -> dict:
    st = {
        "code": g["code"],
        "phase": g["phase"],
        "lot_index": g["lot_index"],
        "lot_value": LOTS[g["lot_index"]] if g["phase"] in ("bidding", "reveal") else None,
        "round": g["lot_index"] + 1 if g["phase"] in ("bidding", "reveal") else len(g["history"]),
        "rounds_total": len(LOTS),
        "seats": [
            {
                "seat": i,
                "name": s["name"],
                "bot": s["bot"],
                "credits": s["credits"],
                "score": s["score"],
                "has_bid": i in g["bids"],
                "empty": s["name"] is None,
            }
            for i, s in enumerate(g["seats"])
        ],
        "history": g["history"],
        "seconds_left": max(0, int(g["deadline"] - now())) if g["phase"] == "bidding" else 0,
        "reveal": g.get("reveal"),
        "winner": g.get("winner"),
        "your_seat": seat,
        "your_bid": g["bids"].get(seat) if seat is not None else None,
        "log": g["log"][-40:],
    }
    return st


def bot_bid(g: dict, seat: int) -> int:
    """The built-in bot: bids toward a private valuation, saves credits."""
    value = LOTS[g["lot_index"]]
    s = g["seats"][seat]
    credits = s["credits"]
    rivals = [x["credits"] for i, x in enumerate(g["seats"]) if i != seat and x["name"]]
    rich = max(rivals) if rivals else 0
    # Later lots matter more; keep a reserve while others can still outbid you.
    greed = 0.55 + 0.09 * g["lot_index"]
    target = int(round(value * greed + random.uniform(-1.0, 1.2)))
    if g["lot_index"] >= 4:
        target = max(target, value - 1)
    if credits <= 3:
        target = min(target, credits)
    if rich >= credits and g["lot_index"] >= 3 and random.random() < 0.35:
        target = 0                      # let the rich rival burn credits
    return max(0, min(credits, target))


def resolve(g: dict) -> None:
    """All bids in (or the clock ran out): settle the lot, then advance."""
    value = LOTS[g["lot_index"]]
    bids = {i: g["bids"].get(i, 0) for i, s in enumerate(g["seats"]) if s["name"]}
    top = max(bids.values()) if bids else 0
    contenders = [i for i, b in bids.items() if b == top] if top > 0 else []
    winner = None
    if contenders:
        contenders.sort(key=lambda i: (g["seats"][i]["score"], i))
        winner = contenders[0]
        g["seats"][winner]["credits"] -= top
        g["seats"][winner]["score"] += value
    g["history"].append({
        "round": g["lot_index"] + 1,
        "value": value,
        "bids": [{"seat": i, "name": g["seats"][i]["name"], "bid": b} for i, b in sorted(bids.items())],
        "winner": winner,
        "price": top if winner is not None else 0,
        "tie": len(contenders) > 1,
    })
    g["log"].append(
        "Lot %d (value %d): %s" % (
            g["lot_index"] + 1, value,
            ("%s takes it for %d" % (g["seats"][winner]["name"], top)) if winner is not None else "unsold, everyone passed",
        )
    )
    g["reveal"] = g["history"][-1]
    g["phase"] = "reveal"
    g["deadline"] = now() + REVEAL_SECONDS
    g["bids"] = {}


def advance(g: dict) -> None:
    if g["lot_index"] + 1 >= len(LOTS):
        ranked = sorted(
            [i for i, s in enumerate(g["seats"]) if s["name"]],
            key=lambda i: (-g["seats"][i]["score"], -g["seats"][i]["credits"], i),
        )
        g["winner"] = {
            "seat": ranked[0],
            "name": g["seats"][ranked[0]]["name"],
            "score": g["seats"][ranked[0]]["score"],
            "ranking": [
                {"seat": i, "name": g["seats"][i]["name"], "score": g["seats"][i]["score"],
                 "credits": g["seats"][i]["credits"]} for i in ranked
            ],
        }
        g["phase"] = "over"
        g["log"].append("Match over: %s wins with %d points." % (g["winner"]["name"], g["winner"]["score"]))
        return
    g["lot_index"] += 1
    g["phase"] = "bidding"
    g["reveal"] = None
    g["bids"] = {}
    g["deadline"] = now() + BID_SECONDS
    g["log"].append("Lot %d open, value %d." % (g["lot_index"] + 1, LOTS[g["lot_index"]]))
    for i, s in enumerate(g["seats"]):
        if s["name"] and s["bot"]:
            g["bids"][i] = bot_bid(g, i)


def tick(g: dict) -> None:
    if g["phase"] == "bidding":
        seated = [i for i, s in enumerate(g["seats"]) if s["name"]]
        if len(g["bids"]) >= len(seated) or now() >= g["deadline"]:
            resolve(g)
    elif g["phase"] == "reveal" and now() >= g["deadline"]:
        advance(g)


def game(code: str):
    g = GAMES.get((code or "").upper())
    return g


def seat_of(g: dict, key: str):
    for i, s in enumerate(g["seats"]):
        if s["key"] and key and s["key"] == key:
            return i
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "FloorBid/1.0"

    def log_message(self, fmt, *a):  # keep the journal readable
        pass

    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if p in ("/", "/index.html"):
            return self._static("index.html")
        if re.match(r"^/(rules|static/[A-Za-z0-9_.-]+)$", p):
            return self._static(p.lstrip("/"))
        if p == "/api/rules":
            return self._send(200, {"rules": RULES, "lots": LOTS, "credits": START_CREDITS})
        if p == "/api/state":
            with LOCK:
                g = game(q.get("code", ""))
                if not g:
                    return self._send(404, {"error": "no such game"})
                tick(g)
                return self._send(200, public(g, seat_of(g, q.get("key", ""))))
        if p == "/api/log":
            with LOCK:
                g = game(q.get("code", ""))
                if not g:
                    return self._send(404, {"error": "no such game"})
                return self._send(200, {"log": g["log"], "history": g["history"]})
        if p == "/api/games":
            with LOCK:
                return self._send(200, {"games": [
                    {"code": k, "phase": v["phase"], "players": sum(1 for s in v["seats"] if s["name"])}
                    for k, v in GAMES.items() if v["phase"] != "over"]})
        return self._send(404, {"error": "not found"})

    def _static(self, name):
        path = os.path.join(STATIC, name)
        if not os.path.abspath(path).startswith(STATIC) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ctype = {"html": "text/html; charset=utf-8", "js": "application/javascript",
                 "css": "text/css", "png": "image/png", "svg": "image/svg+xml"}.get(
            name.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as f:
            return self._send(200, f.read(), ctype)

    def do_HEAD(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            path = os.path.join(STATIC, "index.html")
            n = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(n))
            self.end_headers()
            return
        self.send_response(200 if u.path.startswith("/api/") else 404)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        p = urlparse(self.path).path
        d = self._json()
        with LOCK:
            if p == "/api/new":
                name = (d.get("name") or "Player").strip()[:16] or "Player"
                code = new_code()
                g = {"code": code, "phase": "lobby", "lot_index": -1, "bids": {}, "history": [],
                     "reveal": None, "winner": None, "deadline": now() + 900, "log": [],
                     "seats": [{"name": None, "bot": False, "key": None, "credits": START_CREDITS,
                                "score": 0} for _ in range(MAX_SEATS)]}
                key = new_key()
                g["seats"][0].update({"name": name, "key": key})
                GAMES[code] = g
                g["log"].append("%s opened table %s." % (name, code))
                return self._send(200, {"code": code, "seat": 0, "key": key})
            if p == "/api/join":
                g = game(d.get("code", ""))
                if not g:
                    return self._send(404, {"error": "no such game"})
                if g["phase"] != "lobby":
                    return self._send(409, {"error": "match already started"})
                free = [i for i, s in enumerate(g["seats"]) if not s["name"]]
                if not free:
                    return self._send(409, {"error": "table full"})
                name = (d.get("name") or "Player").strip()[:16] or "Player"
                key = new_key()
                g["seats"][free[0]].update({"name": name, "key": key})
                g["log"].append("%s joined seat %d." % (name, free[0]))
                return self._send(200, {"code": g["code"], "seat": free[0], "key": key})
            if p == "/api/start":
                g = game(d.get("code", ""))
                if not g:
                    return self._send(404, {"error": "no such game"})
                if g["phase"] != "lobby":
                    return self._send(409, {"error": "already started"})
                if seat_of(g, d.get("key", "")) is None:
                    return self._send(403, {"error": "only a seated player can start"})
                bots = 0
                for i, s in enumerate(g["seats"]):
                    if not s["name"]:
                        s.update({"name": "Bot-%d" % (i + 1), "bot": True})
                        bots += 1
                g["lot_index"] = -1
                advance(g)
                if bots:
                    g["log"].append("%d bot(s) filled the empty seats." % bots)
                return self._send(200, {"started": True, "bots": bots})
            if p == "/api/bid":
                g = game(d.get("code", ""))
                if not g:
                    return self._send(404, {"error": "no such game"})
                tick(g)
                if g["phase"] != "bidding":
                    return self._send(409, {"error": "round closed", "state": public(g, None)})
                seat = seat_of(g, d.get("key", ""))
                if seat is None:
                    return self._send(403, {"error": "bad key"})
                try:
                    bid = int(d.get("bid"))
                except Exception:
                    return self._send(400, {"error": "bid must be an integer"})
                if bid < 0 or bid > g["seats"][seat]["credits"]:
                    return self._send(400, {"error": "bid out of range",
                                            "max": g["seats"][seat]["credits"]})
                g["bids"][seat] = bid
                g["log"].append("%s bid %d." % (g["seats"][seat]["name"], bid))
                seated = [i for i, s in enumerate(g["seats"]) if s["name"]]
                if len(g["bids"]) >= len(seated):
                    resolve(g)
                return self._send(200, {"accepted": True, "bid": bid,
                                        "waiting_for": len(seated) - len(g["bids"]),
                                        "state": public(g, seat)})
        return self._send(404, {"error": "not found"})


def main():
    port = int(os.environ.get("PORT") or 8790)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("floor-bid on 127.0.0.1:%d" % port, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
