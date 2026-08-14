"""Configuration loading.

Values come from config.local.toml, overridden by environment variables. The file is
gitignored because it holds a bot token and a machine-specific save path; the
environment path exists so the token never has to touch disk at all if you would rather
it did not.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO / "config.local.toml"


class ConfigError(RuntimeError):
    pass


# Which wake-word model each audio source gets when `voice.models` is not set.
#
# The problem this solves is measured: putting the 236 mic recordings through a 64 kbps
# Opus round trip and changing nothing else drops recall from 92.4% to 86.9%, because 21
# clips fall below the 0.1 firing threshold. The codec costs about 6 points of recall.
#
# `hey_pal_discord` is trained on that exact path. **Head to head it does not beat
# `hey_pal`** - 84.3% against 86.9% on codec'd audio, McNemar p = 0.24 - and it was very
# nearly discarded on that reading. That was the wrong comparison: they are not
# alternatives, they are an ensemble, which is what `voice.models` being a list has always
# been for. The highest score wins, so a second model can only make the gate MORE
# sensitive and can never suppress a firing the first would have made.
#
# Read that way they are complementary, because they fail on different clips:
#
#     population   hey_pal   hey_pal_discord   BOTH
#     mic           92.4%     91.5%            94.5%
#     mic->opus     86.9%     84.3%            89.4%
#     false pos    0.027%    0.016%            0.027%
#
# Six codec-path clips are recovered that neither catches alone, and the false-positive
# rate is IDENTICAL to `hey_pal` by itself - the new model's rare firings land on frames
# the old one already fires on, so the extra sensitivity costs nothing. Inference is
# CPU-bound at a realtime factor near 0.015, so the second model is close to free.
#
# The microphone keeps `hey_pal` alone: that path does not go through a codec, and adding
# a model trained for one it never encounters would buy sensitivity on a path that is not
# short of it.
#
# `WakeWord` records which model won each firing, so the log attributes them rather than
# leaving it inferred. **What is still unmeasured is the one that matters**: nobody has
# read a script through a live Discord client with the misses recorded. Every number above
# is simulated or selection-biased - captured clips exist only because the wake word
# fired. Setting `voice.models` overrides this entirely.
# **The Discord source runs BOTH, pending a live test.** Reported from real use on
# 2026-08-13: `hey_pal` is not firing reliably over Discord. That is the unconditional
# recall the offline work above could not measure and said so - the captured clips exist
# only because the wake word fired, so "92.9% of clips that fired, fired" is circular, and
# a field report is the evidence that population cannot supply.
#
# Running both is the safe way to find out, for three reasons that happen to line up:
# the highest score wins, so adding a model can only make the gate MORE sensitive and can
# never suppress a detection `hey_pal` would have made; the new model fired on codec'd
# noise *less* than the old one (0.016% against 0.027%), so the usual cost of extra
# sensitivity is not being paid here; and `WakeWord` already records which model won, so
# the log attributes each firing rather than leaving it inferred.
#
# Inference is CPU-bound at a realtime factor near 0.015, so the second model is close to
# free.
#
# **This is an experiment with an exit condition.** If the log shows `hey_pal_discord`
# winning firings that `hey_pal` was missing, it earned its place; if the two fire
# together throughout, drop back to `("hey_pal",)` and the offline verdict stands.
SOURCE_MODELS = {
    "mic": ("hey_pal",),
    "discord": ("hey_pal", "hey_pal_discord"),
}


def default_models(source: str) -> tuple[str, ...]:
    """The wake-word models for an audio source.

    Falls back to the microphone model for an unknown source rather than to nothing: the
    source is validated at load, so this is only reachable by a caller passing something
    else, and returning an empty set there would make the bot silently deaf.
    """
    return SOURCE_MODELS.get(source, SOURCE_MODELS["mic"])


def _toml_help(path: Path, err: Exception) -> str:
    """Turn a TOML parse error into something actionable.

    Windows paths in double-quoted TOML are the overwhelmingly likely cause: "C:\\Users"
    fails because \\U opens a unicode escape. The raw parser error says "Invalid hex
    value", which points nowhere useful.
    """
    msg = [f"{path} is not valid TOML:", f"  {err}"]
    try:
        line_no = int(str(err).rsplit("at line ", 1)[1].split(",")[0])
        line = path.read_text(encoding="utf-8").splitlines()[line_no - 1]
        msg.append(f"  line {line_no}: {line.strip()}")
        if "\\" in line and '"' in line:
            msg += [
                "",
                "  A Windows path in double quotes: TOML reads \\U, \\a, \\t as escapes.",
                "  Use single quotes (a literal string) or forward slashes:",
                f"    {line.split('=')[0].strip()} = 'C:\\Users\\you\\...'",
                f"    {line.split('=')[0].strip()} = \"C:/Users/you/...\"",
            ]
    except (IndexError, ValueError):
        pass
    return "\n".join(msg)


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    channel_id: int
    listen_mode: str = "any"     # any | prefix | mention
    prefix: str = "?"


@dataclass(frozen=True)
class VoiceConfig:
    """Voice input. `enabled = false` runs the bot text-only.

    **`source` picks where the audio comes from, and it is a one-word switch on purpose.**

    - `"mic"` - the local microphone. What the project ran on from Phase 0 to 2026-08-12.
    - `"discord"` - receive from a Discord voice channel, so party members can ask by
      voice. Needs `channel_id`, and needs `pydiscorddave` installed.

    Discord receive was recorded as *upstream-blocked* on Discord's DAVE encryption
    (pycord#3139) for months, and that was wrong: DAVE decryption succeeds on 99.8% of
    packets. What was broken is py-cord 2.8's receive plumbing, which shipped a new
    `voice/receive/` package against the old `sinks/core.py`. `pydiscorddave` patches
    that; this project does not do the cryptography.

    **`mic` stays the default**, and that is the point of the flag rather than an
    oversight. Discord receive depends on a patch against py-cord internals, and the
    failure mode it degrades to - connected, recording, silent - is the one ADR-0004 calls
    the worst kind, because it is indistinguishable from nobody speaking. A regression
    should cost the party-voice feature, not all voice input.

    **`models` defaults from `source`, and that is the point of there being two.**
    `hey_pal` is trained on microphone audio; `hey_pal_discord` on the same corpus put
    through the Opus round trip the Discord path actually applies. Leave it unset and each
    source gets the model trained for it - see `SOURCE_MODELS`.

    It stays a *list* because running several at once is still useful and is still
    supported: inference is CPU-bound at a realtime factor near 0.015, so a second model
    is close to free, and the highest score wins. Setting it explicitly overrides the
    per-source default, which is how a pretrained model can be kept alongside a custom one
    during a transition.

    `speaker` is who the microphone belongs to, and it exists because **the mic cannot
    say**. Conversation memory is per person (ADR-0013) and text keys on the Discord
    display name, so without this the voice path keys on the literal "voice" and one
    person's own spoken question cannot be followed up in text - which is exactly what
    ADR-0012 promises works. Set it to your Discord display name to join the two.

    Left unset it stays "voice", because guessing which Discord user is sitting at the
    machine would be wrong in a shared channel, and silently attributing speech to the
    wrong person is worse than not joining the two at all.

    **`speaker` is unused when `source = "discord"`**: every packet arrives tagged with
    the member who sent it, so attribution is observed rather than configured. That is the
    substantive gain over the mic and not a side effect - it is what makes ADR-0012's
    promise hold for everyone in the channel instead of for one person by declaration.
    """
    enabled: bool = False
    source: str = "mic"                  # mic | discord
    channel_id: int = 0                  # the VOICE channel, when source = "discord"
    models: tuple[str, ...] = ("hey_pal",)
    threshold: float = 0.1
    device: int | str | None = None      # None = the system default input
    speaker: str | None = None


@dataclass(frozen=True)
class RouterConfig:
    """Routing behaviour. Both fields exist to be turned back off.

    The fast path skips the model when the stub can answer outright, which is the only
    lever measured to bring a Q1 voice query inside the 2.5s budget - the model round
    trip alone is a ~2s median. `cues` selects how eagerly the stub claims a query.

    `wide` is live on measured evidence that is real but thin: 11 of 15 A5 Q1 prompts
    answered, all correct, nothing claimed from another query class. Fifteen prompts
    cannot carry much confidence, and the failure it risks - a fast, confident, wrong
    card - is the one the whole design is organised against. So it is a flag rather than
    a constant. `cues = "proximity"` is the same trade with the intent guesses ("i need",
    "any") removed; `fast_path = false` restores model-only routing exactly.
    """
    fast_path: bool = True
    cues: str = "wide"                   # standard | proximity | wide
    # One `answer_query` tool instead of one per class. Measured accuracy-neutral
    # (McNemar p = 0.45) and 21% faster at the median, and it is what lets a question
    # name two Pals - `find_pal_drops(pal)` has one slot and "what do I get from Astralym
    # and Mycora" needs two. `item_source` is only reachable this way.
    unified: bool = True


@dataclass(frozen=True)
class CardsConfig:
    """Artwork on answer cards. Off by default while it is a spike.

    A map crop is ~24ms to render and ~60 KB to upload, and it arrives as a second
    Discord round trip after the text card is already on the channel - so it cannot move
    the graded answer latency, only add a reflow the player sees. The flag exists
    because that trade is a judgement about reading cards mid-play, which only real
    sessions settle.

    `maps` needs data/<version>/assets/ (tools/ingest/build_assets.py). Without it both
    silently stay off rather than failing: the text card is the answer either way.
    """
    maps: bool = False
    icons: bool = False


@dataclass(frozen=True)
class CaptureConfig:
    """Gameplay capture and the feedback controls. **Two flags, not one.**

    STATUS already records the lesson from `maps`/`icons`: one flag pair covering two
    features with different risks. Saving audio and putting controls on every card are
    separable, and someone may well want one without the other.

    Both default off because capture records whatever is near the microphone - including
    other people in the room - and that should be a decision rather than a surprise.
    Everything stays local; `data/` is gitignored.
    """
    enabled: bool = False       # keep the WAV the pipeline already wrote, and a log line
    feedback: bool = False      # show labelling buttons under answer cards


@dataclass(frozen=True)
class OutputConfig:
    """Where an answer goes. See [ADR-0018](../Docs/adr/0018-local-output-medium.md) and
    [Docs/local-output-design.md](../Docs/local-output-design.md).

    **Exclusive, not a list.** `"discord"` (the default, unchanged behaviour) or
    `"local"` - a Chat tab in the console (`palintel.ui`) instead of a Discord channel.
    Never both: a player watching a local chat page while someone else reads the same
    answers in Discord is two sources of truth for "what did it say," which is exactly
    the kind of split signal this project refuses elsewhere.

    `poll_ms` / `inbox_poll_ms` govern the local medium only - how often the console
    tails the live event file, and how often the bot polls for a newly submitted query.
    Both are guesses, not measurements (see the design doc §9), which is the whole
    reason they are config rather than a constant: a session that finds either laggy
    needs no code change to say so.
    """
    medium: str = "discord"     # discord | local
    poll_ms: int = 300
    inbox_poll_ms: int = 150


@dataclass(frozen=True)
class CostConfig:
    """The prepaid balance, and when to start warning about it.

    **On by default, unlike capture**, because logging what a query cost records nothing
    about the player and creates no privacy question - it is a number the process already
    computed and then threw away.

    `balance_usd` of 0 means "no balance configured": spend is still logged and totalled,
    and nothing is deducted or warned about. Set it to what was actually loaded onto the
    key, because the failure this guards against is silent - a depleted balance arrives
    as HTTP 429, every 429 becomes a Decline, and the roadmap records one being read as a
    13-point router regression before anyone checked.
    """
    enabled: bool = True
    balance_usd: float = 0.0
    warn_below_usd: float = 2.0


@dataclass(frozen=True)
class Config:
    discord: DiscordConfig
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    cards: CardsConfig = field(default_factory=CardsConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    data_version: str = "1.0.2"
    save_dir: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DEFAULT_PATH
        raw: dict = {}
        if path.exists():
            try:
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(_toml_help(path, e)) from e
        elif not os.environ.get("PALINTEL_DISCORD_TOKEN"):
            raise ConfigError(
                f"No config at {path} and PALINTEL_DISCORD_TOKEN is unset.\n"
                f"  copy config.example.toml config.local.toml   and fill it in.")

        o = raw.get("output", {}) or {}
        medium = o.get("medium", "discord")
        if medium not in ("discord", "local"):
            raise ConfigError(f"output.medium must be discord|local, got {medium!r}")
        for name, val in (("poll_ms", o.get("poll_ms", 300)),
                          ("inbox_poll_ms", o.get("inbox_poll_ms", 150))):
            if int(val) <= 0:
                raise ConfigError(f"output.{name} must be a positive number of "
                                  f"milliseconds, got {val!r}")

        d = raw.get("discord", {})
        token = os.environ.get("PALINTEL_DISCORD_TOKEN", d.get("token", "")).strip()
        channel = int(os.environ.get("PALINTEL_CHANNEL_ID", d.get("channel_id", 0)))

        # **Conditionally required, not unconditionally.** `output.medium = "local"` is
        # the whole reason this project can run without Discord at all - see ADR-0018.
        # Values are still PARSED either way, so switching back to `discord` later does
        # not need credentials re-entered if they were already sitting in the file.
        if medium == "discord":
            if not token:
                raise ConfigError("discord.token is empty - see config.example.toml")
            if not channel:
                raise ConfigError("discord.channel_id is 0 - see Docs/discord-setup.md")

        mode = d.get("listen_mode", "any")
        if mode not in ("any", "prefix", "mention"):
            raise ConfigError(f"listen_mode must be any|prefix|mention, got {mode!r}")

        v = raw.get("voice", {}) or {}
        # `voice.channel_id` was rejected outright from the day the mic replaced Discord
        # receive, on the reasoning that reception was blocked upstream and a stale key
        # would silently do nothing. It is live again as of 2026-08-13 - see VoiceConfig -
        # and the rejection is replaced by the check that actually matters: a Discord
        # source with no channel to listen in connects to nothing and presents as a wake
        # word that never fires, which is the failure the old error was written against.
        source = v.get("source", "mic")
        if source not in ("mic", "discord"):
            raise ConfigError(f"voice.source must be mic|discord, got {source!r}")
        channel_id = int(v.get("channel_id") or 0)
        if source == "discord" and not channel_id:
            raise ConfigError(
                "voice.source = 'discord' needs voice.channel_id - the id of a VOICE "
                "channel, not the text one. A text id here connects to nothing and the "
                "bot simply never hears anything.")
        if medium == "local" and source == "discord":
            # `DiscordListener` needs a live `discord.Client` to attach a voice receive
            # sink to - something `run_local()` never constructs (see ADR-0018). This is
            # a different constraint from the mic, which needs nothing Discord provides:
            # `output.medium = "local"` and `voice.source = "mic"` combine fine.
            raise ConfigError(
                "voice.source = 'discord' needs output.medium = 'discord' - a local "
                "run never connects to Discord at all, so there is no voice channel to "
                "listen in. Set voice.source = 'mic' for local voice input.")
        # Explicit wins. Unset, the default follows the SOURCE - see `default_models`.
        models = tuple(v.get("models") or default_models(source))
        device = v.get("device")

        r = raw.get("router", {}) or {}
        cues = r.get("cues", "wide")
        # Fail at load rather than at the first query. A typo here does not raise on its
        # own - it silently selects a cue set that does not exist, and the fast path
        # would simply never fire.
        if cues not in ("standard", "proximity", "wide"):
            raise ConfigError(
                f"router.cues must be standard|proximity|wide, got {cues!r}")

        c = raw.get("cards", {}) or {}
        cap = raw.get("capture", {}) or {}
        cst = raw.get("cost", {}) or {}
        save = (raw.get("game", {}) or {}).get("save_dir", "").strip()
        return cls(
            discord=DiscordConfig(token=token, channel_id=channel,
                                  listen_mode=mode, prefix=d.get("prefix", "?")),
            voice=VoiceConfig(enabled=bool(v.get("enabled", False)), source=source,
                              channel_id=channel_id, models=models,
                              threshold=float(v.get("threshold", 0.1)),
                              device=device,
                              speaker=(v.get("speaker") or None)),
            router=RouterConfig(fast_path=bool(r.get("fast_path", True)), cues=cues,
                                unified=bool(r.get("unified", True))),
            cards=CardsConfig(maps=bool(c.get("maps", False)),
                              icons=bool(c.get("icons", False))),
            capture=CaptureConfig(enabled=bool(cap.get("enabled", False)),
                                  feedback=bool(cap.get("feedback", False))),
            cost=CostConfig(enabled=bool(cst.get("enabled", True)),
                            balance_usd=float(cst.get("balance_usd", 0.0)),
                            warn_below_usd=float(cst.get("warn_below_usd", 2.0))),
            output=OutputConfig(medium=medium,
                                poll_ms=int(o.get("poll_ms", 300)),
                                inbox_poll_ms=int(o.get("inbox_poll_ms", 150))),
            data_version=os.environ.get(
                "PALINTEL_DATA_VERSION", (raw.get("data", {}) or {}).get("version", "1.0.2")),
            save_dir=Path(save) if save else None,
        )

    def redacted(self) -> dict:
        """Safe to log or print - never exposes the token."""
        t = self.discord.token
        return {
            "output": (self.output.medium if self.output.medium == "discord"
                       else f"local (poll {self.output.poll_ms}ms / "
                            f"inbox {self.output.inbox_poll_ms}ms)"),
            "token": f"{t[:6]}...{t[-4:]} ({len(t)} chars)" if t else "(unset)",
            "channel_id": self.discord.channel_id,
            "listen_mode": self.discord.listen_mode,
            "voice": (f"{self.voice.source}, {', '.join(self.voice.models)}"
                      if self.voice.enabled else "(text only)"),
            "router": ((f"fast path on, cues={self.router.cues}"
                        if self.router.fast_path else "model only")
                       + (", one tool" if self.router.unified else ", tool per class")),
            "cards": ", ".join(
                [k for k, on in (("maps", self.cards.maps),
                                 ("icons", self.cards.icons)) if on]) or "text only",
            "data_version": self.data_version,
            "save_dir": str(self.save_dir) if self.save_dir else "(none)",
        }
