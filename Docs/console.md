# The console — running and inspecting the bot

```
.venv\Scripts\python -m palintel.ui
```

It prints a URL with a token in it and opens your browser:

```
  PalIntel console
  http://127.0.0.1:8765/?token=bOjCvASYS2t1FwBKnfsQqtO4VT_4UgMH
```

That URL is the only way in — the token is minted fresh every run, so a link from a
previous session will not work. If the browser did not open, paste it.

| Flag | |
|---|---|
| `--port 8765` | Change it if something else has the port |
| `--no-browser` | Do not open a browser (running it headless, or over SSH) |
| `--save-dir <path>` | Pin one world. Omit it and the console follows whichever world was written most recently, the same way the bot does |

**Nothing to install.** `aiohttp` already comes with py-cord, and there is no build step,
no CDN and no webfont — the console works with the network down, for the same reason the
bot runs local-first.

---

## Start the bot from it

The console **runs independently of the bot** and that is the whole architectural choice:
the job it has to do best is the one where the bot is *not* running, because that includes
reading the config error that stopped it starting. So the console is what you launch, and
the bot is started from it.

Open the console, press **Start** in the transport bar. The bot loads its datasets before
dialling Discord, so give it a few seconds; the console waits and tells you which of the
two things happened.

If it fails to start, the console shows **the bot's own output** — the last 40 lines,
including whatever `Config.load` objected to. That is the case the whole arrangement
exists for.

**You cannot accidentally run two.** Two bots on one Discord token both connect and both
answer, so the only symptom is every question arriving twice — and a console is the most
likely way to cause it: start the bot, close the console, reopen it, press Start. The
guard is a heartbeat the bot writes to `data/bot-state.json`, so it holds against a bot
started from a terminal, one left by a previous console, and one started by a second
console. None of those is something the console could remember on its own.

A bot the console did not start shows as **running (not ours)** and can still be stopped
from here — a Start button that refuses because of an orphan, with no way to clear the
orphan, would be worse than no button.

You can still run the bot directly (`python -m palintel.bot`); the console will adopt it.

---

## The three views

### 01 · Status

Everything true right now, in one screen.

- **World** — which save is being read, its name, host and in-game day. Auto-detection is
  a heuristic (newest write wins), so the pick is always *shown*: a silent wrong pick would
  answer confidently about a different playthrough.
- **Players** — level, technologies, points, Pals and position, per player. In a co-op
  world each row is that player's own state.
- **Latency** — p50 and p95 per stage against the Phase 1 budgets. The bars are built to
  show a budget being **missed**, because these are exit criteria and they are still
  failing; a console that only showed what passes would be decoration.
- **Spend** — all-time, and split per person once more than one person has asked.
- **Bot process** — pid, uptime, router, voice line, and the Discord receive counters.
  `opus err` climbing *while* `ok` also climbs is the signature of partial corruption,
  which is the receive failure that sounds fine.
- **Save integrity** — the cross-checks: roster totals, and whether the base camps the
  quaternion scan located match the ones the guild claims to own.

**Re-read** forces a full save poll, including the multi-megabyte `Level.sav` walk. It is
not on a timer because it is not cheap.

### 02 · Sessions

Every capture session on disk: what was heard, what it routed to, which path answered, and
the clip itself. This is the measurement loop, and it works with the bot switched off.

A **label** column shows only human verdicts. `auto` is the router's opinion of itself and
is deliberately not shown as one — treating it as a verdict is how a consistent bug
ratifies itself in the corpus it produces.

### 03 · Settings

Edits `config.local.toml` **in place**. Three things worth knowing:

- **Your comments survive.** They are the documentation in that file, so a write is a
  surgical line edit rather than a re-serialisation. Measured on the real file: two lines
  changed of 126, all 83 comment lines intact.
- **Nothing is saved until it loads.** The candidate goes through the bot's own
  `Config.load` first, and a config the bot would refuse never reaches disk. The failure
  comes back in the bot's words — *"voice.source = 'discord' needs voice.channel_id"* —
  because finding that out here is the entire reason to edit config through a form.
  One `.bak` generation is kept beside the file.
- **The Discord token is not editable and is never sent to the page.** You see that one is
  configured and how long it is, and nothing else.

**The bot reads config at startup.** Save, then press **Restart**.

---

## Security

It binds `127.0.0.1`, so nothing off this machine can reach it. That is not the whole
story: loopback stops the network, not the browser — any page you have open can issue
requests to localhost. Harmless for a dashboard, not harmless for something that rewrites
config and restarts processes. So:

- a **token**, minted per run, sent in a header rather than a cookie (cookies are attached
  automatically, which is exactly the property that makes cross-site requests dangerous),
- an **`Origin` check** on everything that is not a plain GET,
- static paths resolved and re-checked against the static directory.

**Do not expose it.** It serves your save contents, your session audio and your spend. If
you want it from a phone, put the machine on a VPN rather than binding it wider — see
[`multi-user-design.md`](multi-user-design.md) §4.1.2, which makes the same argument about
reaching a save across machines.

---

## Files it uses

| Path | |
|---|---|
| `data/sessions/<id>/log.jsonl` | Captured utterances and human labels |
| `data/sessions/<id>/costs.jsonl` | Per-query spend |
| `data/sessions/<id>/latency.jsonl` | Per-stage timings, attributed per speaker |
| `data/sessions/<id>/*.wav` | The clips |
| `data/bot-state.json` | The heartbeat — how the console knows a bot is running |
| `data/bot.log` | The bot's output when the console started it, truncated per start |
| `data/players.json` | Discord user → in-game player bindings |
| `config.local.toml` | Read and written by Settings |

All gitignored. `data/bot.log` is truncated on every start on purpose: it exists to answer
*"why did **this** start fail"*, and an append-only file makes you scroll past every
previous attempt to find out.

---

## If something looks wrong

**"bot — stopped" but it is clearly running.** The heartbeat is older than 20 seconds.
Either the bot predates this feature (restart it), or it is wedged badly enough to have
stopped writing — which is worth knowing either way.

**Start refuses and nothing is running.** A stale `data/bot-state.json` from a killed bot.
Press Stop, which clears it, or delete the file.

**Latency shows nothing.** Timings only persist from 2026-08-13 onward. Run a session.

**The page is blank.** Almost always an expired token — the URL from a previous run will
not work. Restart the console and use the URL it prints.
