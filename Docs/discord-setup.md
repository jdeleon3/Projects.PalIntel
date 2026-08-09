# Discord setup

One-time setup to give PalIntel a bot account and a channel. Takes about five minutes.

The bot is a thin adapter over the pipeline ([01-architecture.md](01-architecture.md)
§3.3), so nothing here affects how answers are produced — only how they arrive.

---

## 1. Create the application

1. Go to <https://discord.com/developers/applications> and choose **New Application**.
2. Name it (`PalIntel`) and create.
3. Open the **Bot** tab in the left sidebar.

## 2. Get the token

On the **Bot** tab, choose **Reset Token**, then copy it.

> **The token is a password.** Anyone holding it can control the bot in every server it
> has joined. Do not paste it into chat, screenshots, or a committed file.
> `config.local.toml` is gitignored for exactly this reason. If it ever leaks, hit
> **Reset Token** — that instantly invalidates the old one.

## 3. Enable the Message Content intent

Still on the **Bot** tab, scroll to **Privileged Gateway Intents** and turn on:

- **MESSAGE CONTENT INTENT** ✅

**This one is not optional and its failure mode is confusing.** Without it the bot
connects successfully, appears online, and receives every message with an *empty body* —
so it looks like the pipeline is broken when the problem is a checkbox.

Leave **Presence** and **Server Members** off. Neither is needed.

*(For voice later you will also want to leave the bot able to connect and speak — that
is a permission, set in the next step, not an intent.)*

## 4. Invite it to your server

Quickest way — build the URL yourself rather than clicking through the generator.

Copy your **Application ID** from **General Information** (a number, and *not* the bot
token), then open:

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_APP_ID&permissions=3230720&scope=bot
```

Pick your server → **Authorise**.

`3230720` is the sum of exactly the permissions PalIntel needs:

| Permission | Bit | Why |
|---|---|---|
| View Channel | 1024 | see the channel at all |
| Send Messages | 2048 | reply |
| **Embed Links** | 16384 | cards are embeds; without this they silently never appear |
| Read Message History | 65536 | read what was typed |
| Connect | 1048576 | unused today — included so voice needs no re-invite |
| Speak | 2097152 | same |

<details>
<summary>Or use the UI instead</summary>

**OAuth2 → URL Generator** → scope **`bot`** → tick the six permissions above → copy the
generated URL.
</details>

You need **Manage Server** on the target server to add a bot. If your server is missing
from the dropdown, that is why.

## 5. Get the channel ID

1. In the Discord client: **Settings → Advanced → Developer Mode** on.
2. Right-click the channel you want (e.g. `#copilot-hud`) → **Copy Channel ID**.

A dedicated channel is worth making. With `listen_mode = "any"` the bot answers every
message there, which is the least friction while playing — no prefix to remember, no
mention to type.

## 6. Configure

```
copy config.example.toml config.local.toml
```

Fill in `token` and `channel_id`. Leave the rest at defaults to start.

Prefer keeping the token out of files entirely? Set `PALINTEL_DISCORD_TOKEN` in the
environment instead — it overrides the file.

## 7. Run

```
.venv\Scripts\python -m palintel.bot
```

Expected startup:

```
INFO palintel.bot: config: {'token': 'MTIzND...ab12 (72 chars)', 'channel_id': ...}
INFO palintel.bot: loaded: {'game_version': '1.0.2', 'node_clusters': 2663, ...}
INFO palintel.bot: connected as PalIntel#1234, listening in #copilot-hud
```

Then type in the channel:

```
where's the nearest coal
```

---

## If it doesn't work

| Symptom | Cause |
|---|---|
| Connects, but never replies | **Message Content intent off** (step 3). The most common cause by far. |
| `channel id ... (NOT FOUND)` at startup | Wrong ID, or the bot was not invited to that server, or it cannot view the channel |
| Replies as plain text, no card | **Embed Links** permission missing |
| `config error: discord.token is empty` | `config.local.toml` not created, or token not pasted |
| `improper token has been passed` | Token truncated on copy, or it was reset after copying |
| Nothing at all, no log lines | Wrong channel — the bot only listens in `channel_id` |

## Before testing the pipeline itself

Rule out Discord entirely by running the same queries through the local harness:

```
.venv\Scripts\python -m palintel.cli "where's the nearest coal" -v
```

Identical pipeline, no network. If that works and Discord does not, the problem is in
the table above.
