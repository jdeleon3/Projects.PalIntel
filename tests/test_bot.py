"""Discord adapter tests.

The adapter is deliberately thin, so these cover only the translation boundary: Card to
Embed, and configuration validation. Anything about *answers* belongs in
test_pipeline.py, where it can be tested without Discord installed at all.
"""
from __future__ import annotations

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
