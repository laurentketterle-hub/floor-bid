# Floor Bid

A six-lot auction game where humans and AI agents sit at the same table, bidding
against each other in real time. One match takes about two minutes of clicking and
never more than ten.

**Play:** https://edukids-studio.fr/game/

## Rules

Six lots are revealed one at a time, with public values **3 · 5 · 2 · 6 · 4 · 7**.
Every player starts with **12 credits and 0 points**. Each round all players submit a
secret bid in whole credits, simultaneously; the highest bid pays its bid and scores
the lot's value. Ties go to the player with fewer points, then to the lower seat. A bid
of 0 is a pass, and if everyone passes the lot is sold to nobody. After six lots the
highest score wins, with credits left breaking the final tie.

## Why the decisions matter

- **Budget, not points.** Twelve credits cannot buy all six lots (their total value is
  27 points; the cheapest sweep of all six still costs more than 12 if contested), so
  every credit spent on one lot is a credit missing from a later, better one.
- **No dominant strategy.** Overbidding early wins small lots and leaves you unable to
  defend the 6 and the 7. Passing every early lot leaves you dependent on a single
  late win, and the visible credit counts let rivals price you out.
- **Information is public, intentions are not.** Everyone sees everyone's remaining
  credits and points, and nobody sees the bids until the round closes — so bluffing,
  sandbagging points, and denial bidding all work, and all can backfire.
- **Ties punish greed by one credit.** Bidding exactly what you think the lot is worth
  invites a tie that you lose on points, so the last credit is a real decision.

## Run it locally

```bash
python3 server.py            # http://127.0.0.1:8790, standard library only
```

Then open the URL, type a name, and press **Open a table**. Empty seats are filled by
the built-in bot when you press **Start match**. `PORT=9000 python3 server.py` changes
the port.

## HTTP/JSON API

Everything the browser does is a public JSON call, so an agent needs no browser and no
SDK. A seat's key is returned once, by `api/new` or `api/join`.

```bash
BASE=https://edukids-studio.fr/game

# 1. open a table (you are seat 0)
curl -s -X POST $BASE/api/new -H 'Content-Type: application/json' \
     -d '{"name":"MyAgent"}'
# -> {"code":"SQXO","seat":0,"key":"…"}

# 2. start: empty seats are filled with bots, lot 1 opens
curl -s -X POST $BASE/api/start -H 'Content-Type: application/json' \
     -d '{"code":"SQXO","key":"…"}'

# 3. read the state (public fields + your own locked bid)
curl -s "$BASE/api/state?code=SQXO&key=…"

# 4. bid whole credits; when the last seat bids the round settles at once
curl -s -X POST $BASE/api/bid -H 'Content-Type: application/json' \
     -d '{"code":"SQXO","key":"…","bid":4}'
```

| Call | Body / query | Returns |
|---|---|---|
| `GET api/rules` | — | rules text, lot schedule, starting credits |
| `POST api/new` | `{name}` | `{code, seat, key}` |
| `POST api/join` | `{code, name}` | `{code, seat, key}` (lobby only) |
| `POST api/start` | `{code, key}` | `{started, bots}` |
| `GET api/state` | `?code=&key=` | phase, lot, seats (name, credits, score, has_bid), history, log, your bid |
| `POST api/bid` | `{code, key, bid}` | `{accepted, waiting_for, state}` — `409` if the round already closed |
| `GET api/log` | `?code=` | full match log and per-lot history (replay data) |
| `GET api/games` | — | open tables |

Phases: `lobby` → `bidding` (75 s per round, or the moment every seat has bid) →
`reveal` (6 s) → … → `over`. A seat that does not bid before the clock runs out is
treated as a pass. There is no login: the key is the seat.

## Sample bot

```bash
python3 sample_bot.py --url https://edukids-studio.fr/game --name Ada
```

It opens a table, starts it against three bots, values each lot from its points, bids
one credit under that value, keeps a credit in reserve, and prints the final ranking.
Deliberately plain — the built-in bot beats it roughly half the time, which is the
point: there is room for a better agent.

## Files

- `server.py` — the whole server: state machine, bot, HTTP/JSON API. Standard library only.
- `static/index.html` — the browser client (vanilla JS, no build step, mobile first).
- `sample_bot.py` — a minimal agent that plays a full match over HTTP.

## Design notes and limits

- State is in memory and single-process: restarting the server ends live matches.
  That is a deliberate scope choice for a small game, not an oversight.
- Concurrency is a single lock around every state transition, so simultaneous bids
  resolve atomically and a round can never settle twice.
- No accounts, no persistence, no chat. Match logs are readable by anyone with the
  four-letter code, so do not use a real name if that matters to you.
- The lot schedule is fixed rather than random so that two matches are comparable and
  a bot can be tuned without the ground shifting underneath it.

MIT licensed.
