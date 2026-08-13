"""Editing config.local.toml from the console.

Three properties matter more than the form does, and each is a way this could go wrong
quietly:

  * the Discord token must never leave the file,
  * the comments must survive, because in this repo they are the documentation,
  * a config the bot would refuse must never reach disk, because you would then be
    fixing it in a text editor - which is what the console exists to avoid.
"""
from __future__ import annotations

import tomllib

import pytest

from palintel.ui import config_edit

BASE = """\
# PalIntel local configuration.

[discord]
token = 'SECRET-TOKEN-VALUE'
channel_id = 123
listen_mode = "any"

[voice]
# mic stays the default deliberately - see VoiceConfig.
enabled = false
source = "mic"
threshold = 0.1

[cost]
enabled = true
balance_usd = 0.0
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.local.toml"
    p.write_text(BASE, encoding="utf-8")
    return p


# --- the token ----------------------------------------------------------------

def test_the_token_is_never_returned(cfg):
    """The whole security boundary. A bot token works from anywhere; putting it in a
    browser is the one thing this feature must not do."""
    d = config_edit.read(cfg)
    blob = repr(d)
    assert "SECRET-TOKEN-VALUE" not in blob
    assert d["token_set"] is True
    assert not any(f["key"] == "token" for f in d["fields"])
    # Length only. Not even the redacted form Config.redacted() logs: that shows the
    # first six characters, which is fine locally and is more than a browser needs.
    assert d["token_hint"] == "configured, 18 characters"
    for part in ("SECRET", "TOKEN", "VALUE"):
        assert part not in d["token_hint"]


def test_the_token_cannot_be_written(cfg):
    res = config_edit.write({"discord.token": "attacker-value"}, cfg)
    assert res["ok"] is False
    assert "not an editable setting" in res["error"]
    assert "SECRET-TOKEN-VALUE" in cfg.read_text(encoding="utf-8")


def test_the_token_survives_an_unrelated_write(cfg):
    """A write is a surgical line edit, so everything it does not name is not merely
    preserved - it is never touched."""
    assert config_edit.write({"voice.enabled": True}, cfg)["ok"]
    assert "token = 'SECRET-TOKEN-VALUE'" in cfg.read_text(encoding="utf-8")


def test_an_unknown_key_is_refused_rather_than_ignored(cfg):
    """Refusing loudly stops a caller discovering what is writable by trying."""
    res = config_edit.write({"voice.nonsense": 1}, cfg)
    assert res["ok"] is False


def test_a_readonly_field_is_refused(cfg):
    res = config_edit.write({"data.version": "9.9.9"}, cfg)
    assert res["ok"] is False and "not editable" in res["error"]


# --- comments and shape -------------------------------------------------------

def test_comments_survive_a_write(cfg):
    """The comments in this project's config explain WHY each flag exists. Serialising a
    parsed dict back out would delete every one of them."""
    config_edit.write({"voice.source": "discord", "voice.channel_id": 999}, cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "# PalIntel local configuration." in text
    assert "# mic stays the default deliberately" in text


def test_a_trailing_comment_on_the_edited_line_survives(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[cost]\nbalance_usd = 0.0  # what you loaded\n", encoding="utf-8")
    out = config_edit.set_values(p.read_text(encoding="utf-8"),
                                 {("cost", "balance_usd"): 5.0},
                                 {("cost", "balance_usd"): "float"})
    assert "# what you loaded" in out
    assert "balance_usd = 5.0" in out


def test_a_missing_key_is_added_to_its_own_section(tmp_path):
    """Not appended to the end of the file, where it would land under whatever section
    happened to be last and mean something else entirely."""
    p = tmp_path / "c.toml"
    p.write_text("[voice]\nenabled = true\n\n[cost]\nenabled = true\n", encoding="utf-8")
    out = config_edit.set_values(p.read_text(encoding="utf-8"),
                                 {("voice", "threshold"): 0.2},
                                 {("voice", "threshold"): "float"})
    parsed = tomllib.loads(out)
    assert parsed["voice"]["threshold"] == 0.2
    assert "threshold" not in parsed["cost"]


def test_a_windows_path_is_written_as_a_literal_string(tmp_path):
    r"""The trap `config._toml_help` exists to explain: "C:\Users" in a DOUBLE-quoted
    TOML string fails to parse, because \U opens a unicode escape."""
    p = tmp_path / "c.toml"
    p.write_text("[game]\nsave_dir = ''\n", encoding="utf-8")
    out = config_edit.set_values(
        p.read_text(encoding="utf-8"),
        {("game", "save_dir"): r"C:\Users\jd02_\AppData\Local\Pal"},
        {("game", "save_dir"): "str"})
    assert tomllib.loads(out)["game"]["save_dir"] == r"C:\Users\jd02_\AppData\Local\Pal"


def test_a_value_containing_a_quote_still_round_trips(tmp_path):
    """A literal string cannot contain a single quote, so it has to escape into a basic
    string - and the backslashes have to escape with it or the path trap returns."""
    p = tmp_path / "c.toml"
    p.write_text("[voice]\nspeaker = ''\n", encoding="utf-8")
    out = config_edit.set_values(p.read_text(encoding="utf-8"),
                                 {("voice", "speaker"): "O'Brien"},
                                 {("voice", "speaker"): "str"})
    assert tomllib.loads(out)["voice"]["speaker"] == "O'Brien"


def test_booleans_are_toml_not_python(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[voice]\nenabled = false\n", encoding="utf-8")
    out = config_edit.set_values(p.read_text(encoding="utf-8"),
                                 {("voice", "enabled"): True},
                                 {("voice", "enabled"): "bool"})
    assert "enabled = true" in out and "True" not in out


# --- validation ---------------------------------------------------------------

def test_a_config_the_bot_would_refuse_never_reaches_disk(cfg):
    """`voice.source = discord` with no channel is a ConfigError, and the bot exits on it.
    Writing it would leave a console that cannot start the bot AND a file you have to fix
    by hand."""
    before = cfg.read_text(encoding="utf-8")
    res = config_edit.write({"voice.source": "discord"}, cfg)
    assert res["ok"] is False
    assert "voice.channel_id" in res["error"]
    assert cfg.read_text(encoding="utf-8") == before


def test_the_bots_own_words_come_back(cfg):
    """Not a generic failure. Finding out exactly what the bot objects to, here, is the
    reason to edit config through this rather than a text editor."""
    res = config_edit.write({"discord.listen_mode": "sideways"}, cfg)
    assert res["ok"] is False
    assert "sideways" in res["error"]


def test_a_valid_change_lands_and_keeps_a_backup(cfg):
    res = config_edit.write({"voice.enabled": True, "cost.balance_usd": 12.5}, cfg)
    assert res["ok"] and res["changed"] == 2
    parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert parsed["voice"]["enabled"] is True
    assert parsed["cost"]["balance_usd"] == 12.5
    assert cfg.with_suffix(".toml.bak").exists()


def test_the_result_says_a_restart_is_needed(cfg):
    """The bot reads config at startup. A settings page that silently implied otherwise
    would have you changing a value and wondering why nothing happened."""
    res = config_edit.write({"voice.enabled": True}, cfg)
    assert "restart" in res["note"]


def test_a_bad_type_is_named(cfg):
    res = config_edit.write({"voice.threshold": "loud"}, cfg)
    assert res["ok"] is False and "float" in res["error"]


def test_a_choice_outside_the_set_is_refused(cfg):
    res = config_edit.write({"router.cues": "widest"}, cfg)
    assert res["ok"] is False and "widest" in res["error"]
