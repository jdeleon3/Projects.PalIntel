"""Which wake-word model loads, and what happens when it is not there.

Two models exist because they are trained for different signals: `hey_pal` on microphone
audio, `hey_pal_discord` on the same corpus through a 64 kbps Opus round trip. The codec
costs the mic-trained model 21 of 236 clips below the firing threshold - about 9% of
recall - so neither is a replacement for the other.

The failure this guards is the one ADR-0004 calls the worst: a missing model file making
the bot silently deaf. Nothing happens, and there is nothing to diagnose.
"""
from __future__ import annotations

import pytest

from palintel.config import SOURCE_MODELS, default_models


def test_each_source_gets_the_model_trained_for_it():
    assert default_models("mic") == ("hey_pal",)
    assert default_models("discord") == ("hey_pal_discord",)


def test_an_unknown_source_gets_the_mic_model_rather_than_nothing():
    """Only reachable by a caller bypassing config validation. Returning an empty set
    there would make the bot deaf with no error."""
    assert default_models("carrier-pigeon") == ("hey_pal",)
    assert all(v for v in SOURCE_MODELS.values())


def test_an_explicit_setting_overrides_the_per_source_default(tmp_path):
    """Running several at once is still supported - that is how a pretrained model is
    kept alongside a custom one during a transition."""
    from palintel.config import Config

    p = tmp_path / "config.local.toml"
    p.write_text(
        "[discord]\ntoken = 'x'\nchannel_id = 1\n\n"
        "[voice]\nsource = 'discord'\nchannel_id = 2\n"
        "models = ['hey_pal', 'hey_jarvis']\n", encoding="utf-8")
    assert Config.load(p).voice.models == ("hey_pal", "hey_jarvis")


def test_the_default_follows_the_source_through_a_real_config(tmp_path):
    from palintel.config import Config

    base = "[discord]\ntoken = 'x'\nchannel_id = 1\n\n[voice]\n"
    mic = tmp_path / "mic.toml"
    mic.write_text(base + "source = 'mic'\n", encoding="utf-8")
    assert Config.load(mic).voice.models == ("hey_pal",)

    disc = tmp_path / "disc.toml"
    disc.write_text(base + "source = 'discord'\nchannel_id = 2\n", encoding="utf-8")
    assert Config.load(disc).voice.models == ("hey_pal_discord",)


# --- the missing-model fallback ------------------------------------------------

def _fake_model_dir(tmp_path, *names):
    for n in names:
        (tmp_path / f"{n}.onnx").write_bytes(b"not really an onnx")
    return tmp_path


def test_a_missing_trained_model_falls_back_rather_than_going_deaf(tmp_path, monkeypatch):
    """Reachable the moment `voice.source = discord` picks a model that has not been
    copied into data/wakeword/ yet. openWakeWord's own error is "could not find pretrained
    model", which sends the reader looking in entirely the wrong place."""
    from palintel import wakeword

    root = _fake_model_dir(tmp_path, "hey_pal")
    seen = {}

    class _FakeModel:
        def __init__(self, wakeword_models, inference_framework):
            seen["specs"] = wakeword_models

    monkeypatch.setattr("openwakeword.model.Model", _FakeModel)
    w = wakeword.WakeWord(model="hey_pal_discord", models_dir=root)

    assert w.names == ["hey_pal"], "should have fallen back to the mic model"
    assert "hey_pal.onnx" in seen["specs"][0]


def test_no_usable_model_at_all_is_an_error_not_a_silent_start(tmp_path, monkeypatch):
    """Starting deaf is worse than not starting: the player speaks, nothing happens, and
    there is nothing in the channel to diagnose."""
    from palintel import wakeword

    monkeypatch.setattr("openwakeword.model.Model", lambda **kw: None)
    with pytest.raises(RuntimeError, match="no usable wake-word model"):
        wakeword.WakeWord(model="hey_pal_discord", models_dir=tmp_path)


def test_a_pretrained_name_is_still_left_to_the_library(tmp_path, monkeypatch):
    """`hey_jarvis` has no file here and must not be reported as missing - openWakeWord
    resolves its own names."""
    from palintel import wakeword

    seen = {}
    monkeypatch.setattr("openwakeword.model.Model",
                        lambda wakeword_models, inference_framework: seen.update(
                            specs=wakeword_models))
    w = wakeword.WakeWord(model="hey_jarvis", models_dir=tmp_path)
    assert seen["specs"] == ["hey_jarvis"]
    assert w.names == ["hey_jarvis"]


def test_the_trained_model_is_preferred_when_present(tmp_path, monkeypatch):
    from palintel import wakeword

    root = _fake_model_dir(tmp_path, "hey_pal", "hey_pal_discord")
    seen = {}
    monkeypatch.setattr("openwakeword.model.Model",
                        lambda wakeword_models, inference_framework: seen.update(
                            specs=wakeword_models))
    w = wakeword.WakeWord(model="hey_pal_discord", models_dir=root)
    assert w.names == ["hey_pal_discord"]
    assert "hey_pal_discord.onnx" in seen["specs"][0]
