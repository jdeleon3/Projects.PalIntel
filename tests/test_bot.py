"""Discord adapter tests.

The adapter is deliberately thin, so these cover only the translation boundary: Card to
Embed, and configuration validation. Anything about *answers* belongs in
test_pipeline.py, where it can be tested without Discord installed at all.
"""
from __future__ import annotations

import os

import pytest

from palintel.cards import Card, decline_card
from palintel.config import Config, ConfigError
from palintel.tools import Decline

discord = pytest.importorskip("discord")
from palintel.bot import to_embed  # noqa: E402


def test_card_fields_survive_translation():
    card = Card(title="Coal locations", lines=["**1. (20, -153)**", "**2. (422, 4)**"],
                footer="533 clusters", colour=0x2E7D32)
    e = to_embed(card)
    assert e.title == "Coal locations"
    assert "(20, -153)" in e.description
    assert e.footer.text == "533 clusters"
    assert e.colour.value == 0x2E7D32


def test_footerless_card_does_not_set_an_empty_footer():
    """Discord rejects an empty footer object; decline cards have no footer.

    py-cord leaves `.footer` as None when never set, rather than an empty proxy - so
    this asserts on the attribute itself, not `.footer.text`.
    """
    e = to_embed(decline_card(Decline(reason="no resource identified")))
    assert e.footer is None


def test_decline_renders_in_the_decline_colour():
    from palintel.cards import TIER_DECLINE
    assert to_embed(decline_card(Decline(reason="x"))).colour.value == TIER_DECLINE


# ------------------------------------------------------------------------- config

def _write(tmp_path, body: str):
    p = tmp_path / "config.local.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_rejects_missing_token(tmp_path, monkeypatch):
    monkeypatch.delenv("PALINTEL_DISCORD_TOKEN", raising=False)
    p = _write(tmp_path, '[discord]\ntoken = ""\nchannel_id = 123\n')
    with pytest.raises(ConfigError, match="token"):
        Config.load(p)


def test_rejects_missing_channel(tmp_path, monkeypatch):
    monkeypatch.delenv("PALINTEL_CHANNEL_ID", raising=False)
    p = _write(tmp_path, '[discord]\ntoken = "abc"\nchannel_id = 0\n')
    with pytest.raises(ConfigError, match="channel_id"):
        Config.load(p)


def test_rejects_unknown_listen_mode(tmp_path):
    p = _write(tmp_path,
               '[discord]\ntoken = "abc"\nchannel_id = 1\nlisten_mode = "shout"\n')
    with pytest.raises(ConfigError, match="listen_mode"):
        Config.load(p)


def test_dotenv_is_loaded_and_real_env_wins(tmp_path, monkeypatch):
    """A key in .env must reach os.environ, and an exported one must beat it.

    .gitignore listed .env before anything loaded it, so a key placed there was
    silently ignored - the config looked correct and wasn't.
    """
    import palintel

    env_file = tmp_path / ".env"
    env_file.write_text("PALINTEL_TEST_KEY=from-dotenv\n", encoding="utf-8")
    # _load_dotenv resolves .env relative to the package's __file__, so pointing that
    # at a temp tree is what isolates this from the repo's real .env.
    monkeypatch.setattr(palintel, "__file__", str(tmp_path / "palintel" / "__init__.py"))
    monkeypatch.delenv("PALINTEL_TEST_KEY", raising=False)
    palintel._load_dotenv()
    assert os.environ.get("PALINTEL_TEST_KEY") == "from-dotenv"

    # An already-set variable is not overwritten (override=False).
    monkeypatch.setenv("PALINTEL_TEST_KEY", "from-real-env")
    palintel._load_dotenv()
    assert os.environ["PALINTEL_TEST_KEY"] == "from-real-env"


def test_environment_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PALINTEL_DISCORD_TOKEN", "from-env")
    p = _write(tmp_path, '[discord]\ntoken = "from-file"\nchannel_id = 7\n')
    assert Config.load(p).discord.token == "from-env"


def test_windows_path_in_double_quotes_gets_an_actionable_error(tmp_path):
    r"""TOML reads \U in "C:\Users" as a unicode escape.

    The raw parser says "Invalid hex value", which points nowhere. Windows users will
    hit this constantly, so the error has to name the cause and the fix.
    """
    p = _write(tmp_path, "\n".join([
        "[discord]", 'token = "abc"', "channel_id = 1", "",
        "[game]", r'save_dir = "C:\Users\me\AppData\Local\Pal"',
    ]))
    with pytest.raises(ConfigError) as exc:
        Config.load(p)
    text = str(exc.value)
    assert "single quotes" in text and "forward slashes" in text
    assert "line 6" in text          # points at the offending line
    assert "save_dir" in text        # and shows it


def test_windows_path_as_literal_string_parses(tmp_path):
    p = _write(tmp_path, "\n".join([
        "[discord]", 'token = "abc"', "channel_id = 1", "",
        "[game]", r"save_dir = 'C:\Users\me\AppData\Local\Pal'",
    ]))
    assert str(Config.load(p).save_dir).endswith("Pal")


def test_redacted_never_exposes_the_token(tmp_path, monkeypatch):
    monkeypatch.delenv("PALINTEL_DISCORD_TOKEN", raising=False)
    secret = "MTIzNDU2Nzg5.SUPERSECRETVALUE.abcdefgh"
    p = _write(tmp_path, f'[discord]\ntoken = "{secret}"\nchannel_id = 7\n')
    shown = Config.load(p).redacted()["token"]
    assert secret not in shown
    assert "SUPERSECRET" not in shown


def test_voice_speaker_defaults_to_unattributed(tmp_path):
    """Unset, spoken queries keep their own memory thread.

    Guessing which Discord user is at the machine would attribute speech to the wrong
    person in a shared channel, which is worse than not joining voice and text at all.
    """
    p = _write(tmp_path, '[discord]\ntoken = "abc"\nchannel_id = 1\n[voice]\n')
    assert Config.load(p).voice.speaker is None


def test_voice_speaker_is_read_from_config(tmp_path):
    """What joins a spoken question to its typed follow-up.

    Conversation memory is per person and the text path keys on the Discord display
    name, so naming the person at the microphone is what makes ADR-0012's "ask by voice,
    follow up in text" actually work.
    """
    p = _write(tmp_path,
               '[discord]\ntoken = "abc"\nchannel_id = 1\n'
               '[voice]\nspeaker = "jdeleon3"\n')
    assert Config.load(p).voice.speaker == "jdeleon3"


def test_blank_speaker_is_treated_as_unset(tmp_path):
    """An empty string would otherwise become a memory key that no text path ever uses."""
    p = _write(tmp_path,
               '[discord]\ntoken = "abc"\nchannel_id = 1\n[voice]\nspeaker = ""\n')
    assert Config.load(p).voice.speaker is None


# ------------------------------------------------------- the two audio sources

def test_the_microphone_is_still_the_default(tmp_path):
    """Discord receive depends on a patch against py-cord internals, and the way it fails
    is by going quietly deaf - which ADR-0004 names as the worst kind, because it is
    indistinguishable from nobody speaking. A regression there should cost party voice,
    not all voice input, so flipping the source is opt-in."""
    p = _write(tmp_path, '[discord]\ntoken = "abc"\nchannel_id = 1\n[voice]\n')
    assert Config.load(p).voice.source == "mic"


def test_a_discord_source_needs_a_voice_channel(tmp_path):
    """Connecting to nothing presents as a wake word that never fires, which is the
    failure the old `voice.channel_id is no longer used` error was written against."""
    p = _write(tmp_path,
               '[discord]\ntoken = "abc"\nchannel_id = 1\n'
               '[voice]\nsource = "discord"\n')
    with pytest.raises(ConfigError, match="channel_id"):
        Config.load(p)


def test_a_voice_channel_id_is_accepted_again(tmp_path):
    """It was rejected outright for months on the reasoning that Discord receive was
    blocked upstream by DAVE. It was not - DAVE decrypts 99.8% of packets and py-cord's
    receive package was unfinished - so the key is live and the rejection is gone."""
    p = _write(tmp_path,
               '[discord]\ntoken = "abc"\nchannel_id = 1\n'
               '[voice]\nsource = "discord"\nchannel_id = 999\n')
    cfg = Config.load(p)
    assert cfg.voice.source == "discord" and cfg.voice.channel_id == 999


def test_an_unknown_source_fails_at_load(tmp_path):
    """A typo would otherwise silently select the mic and look like Discord receive
    being broken again."""
    p = _write(tmp_path,
               '[discord]\ntoken = "abc"\nchannel_id = 1\n[voice]\nsource = "dscord"\n')
    with pytest.raises(ConfigError, match="voice.source"):
        Config.load(p)


def test_a_channel_id_set_while_on_mic_is_not_an_error(tmp_path):
    """The whole point of the flag is that switching back is one word. Rejecting a
    pre-set channel id would make the round trip a two-line edit under a failure."""
    p = _write(tmp_path,
               '[discord]\ntoken = "abc"\nchannel_id = 1\n'
               '[voice]\nsource = "mic"\nchannel_id = 999\n')
    assert Config.load(p).voice.source == "mic"


def test_the_sink_reads_pycord_28_shapes():
    """`write` took (bytes, int) until 2026-08-13, which is py-cord 2.7's signature. 2.8
    calls `sink.write(data, data.source)` where `data` is a VoiceData carrying `.pcm` and
    `source` is a Member. The old signature is called normally and then feeds a VoiceData
    to np.frombuffer, so the sink accumulates nothing and the bot is silently deaf."""
    from types import SimpleNamespace

    import numpy as np

    from palintel.voice import make_sink

    seen = []
    sink = make_sink(lambda speaker, utt: seen.append(speaker), threshold=0.99)
    member = SimpleNamespace(id=42, display_name="Ruichan")
    pcm = np.zeros(960 * 2, dtype=np.int16).tobytes()      # one 20ms 48k stereo packet
    sink.write(SimpleNamespace(pcm=pcm, source=member), member)
    assert 42 in sink._streams, "the packet must reach a per-speaker stream"


def test_the_sink_still_reads_the_old_shapes():
    """Keyed by `getattr` rather than by a version check, so the sink works against both
    while py-cord is in flux - and so this file's own older tests stay meaningful."""
    import numpy as np

    from palintel.voice import make_sink

    sink = make_sink(lambda speaker, utt: None, threshold=0.99)
    sink.write(np.zeros(960 * 2, dtype=np.int16).tobytes(), 7)
    assert 7 in sink._streams
