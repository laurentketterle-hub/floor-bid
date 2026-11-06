#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sample bot for Floor Bid — a 40-line agent that plays a whole match.

    python3 sample_bot.py --url https://edukids-studio.fr/game --name Ada --bid 20
    python3 sample_bot.py --url ... --code ABCD          # join an existing table

Strategy: value the lot from its points, bid a little under it, and keep a
credit in reserve for the last lots. Deliberately simple; a better agent should
beat it.
"""
import argparse, json, time, urllib.request

def call(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + "/" + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="https://edukids-studio.fr/game")
    p.add_argument("--name", default="SampleBot")
    p.add_argument("--code", default=None)
    p.add_argument("--bid", type=int, default=20, help="seconds allowed per own move")
    a = p.parse_args()
    base = a.url
    if a.code:
        seat = call(base, "api/join", {"code": a.code, "name": a.name})
    else:
        seat = call(base, "api/new", {"name": a.name})
        print("table", seat["code"], "— share it, or start alone against bots")
    code, key = seat["code"], seat["key"]
    started = False
    while True:
        s = call(base, "api/state?code=%s&key=%s" % (code, key))
        if s["phase"] == "lobby":
            if not started:
                call(base, "api/start", {"code": code, "key": key}); started = True
            time.sleep(0.5); continue
        if s["phase"] == "over":
            print(json.dumps(s["winner"], indent=1)); return
        if s["phase"] == "bidding" and s["your_bid"] is None:
            me = s["seats"][s["your_seat"]]
            value = s["lot_value"]
            keep = 1 if s["round"] < s["rounds_total"] else 0
            bid = max(0, min(me["credits"], value - 1 + (1 if s["round"] >= 5 else 0) - keep))
            call(base, "api/bid", {"code": code, "key": key, "bid": bid})
            print("lot %d (value %d): bid %d" % (s["round"], value, bid))
        time.sleep(0.6)

if __name__ == "__main__":
    main()
