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
# **One model per path, not both at once.** The two are trained for different signals:
# `hey_pal` on microphone audio, `hey_pal_discord` on the same corpus put through a 64 kbps
# Opus round trip. Running both everywhere was the other option and is worse in one
# specific way - the highest score wins, so a model tuned for a codec it is not hearing
# can only add false positives on the path it was not trained for, and a wake word firing
# on party chatter is the failure the whole channel sees.
#
# Measured, which is why there are two at all: the codec costs the mic-trained model 21 of
# 236 clips below the firing threshold, about 9% of recall, with nothing else varying.
#
# Setting `voice.models` overrides this entirely, including to run both.
SOURCE_MODELS = {
    "mic": ("hey_pal",),
    "discord": ("hey_pal_discord",),
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

        d = raw.get("discord", {})
        token = os.environ.get("PALINTEL_DISCORD_TOKEN", d.get("token", "")).strip()
        channel = int(os.environ.get("PALINTEL_CHANNEL_ID", d.get("channel_id", 0)))

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
            data_version=os.environ.get(
                "PALINTEL_DATA_VERSION", (raw.get("data", {}) or {}).get("version", "1.0.2")),
            save_dir=Path(save) if save else None,
        )

    def redacted(self) -> dict:
        """Safe to log or print - never exposes the token."""
        t = self.discord.token
        return {
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
