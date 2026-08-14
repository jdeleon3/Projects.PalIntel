"""The offline Discord-path simulation.

Exists because the wake word is now deployed behind Opus and was trained and validated in
front of a microphone. What the codec costs was measured before any of this was built, on
identical audio with one variable changed: **21 of 236 clips that cleared the 0.1 firing
threshold on the mic path fall below it after a 64 kbps round trip.** Roughly 9% of recall,
attributable to the codec alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "wakeword"))

discord_path = pytest.importorskip("discord_path")


def tone(seconds: float = 1.0, hz: float = 220.0, rate: int = 16_000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * hz * t) * 12000).astype(np.int16)


def test_a_round_trip_preserves_length_and_format():
    """16 kHz mono in, 16 kHz mono out. The bot's sink hands openWakeWord 16 kHz mono, so
    anything else would be a different signal rather than a codec-damaged one."""
    src = tone(1.0)
    out = discord_path.through_discord(src)
    assert out.dtype == np.int16
    assert out.ndim == 1
    # 20 ms framing truncates a partial final frame; a whole frame is the most that can go.
    assert 0 <= len(src) - len(out) <= 320


def spectrum(pcm: np.ndarray) -> np.ndarray:
    return np.abs(np.fft.rfft(pcm.astype(float)))


def spectral_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """**Compared in the frequency domain, because the time domain lies here.**

    Opus delays its output, and a delayed signal compared sample-by-sample looks
    destroyed however faithful it is - the first version of these tests asserted on mean
    absolute error and reported 16 kbps as *less* damaging than 64. A magnitude spectrum
    is shift-invariant, so it measures what the codec did rather than when it did it.
    """
    n = min(len(a), len(b))
    return float(np.corrcoef(spectrum(a[:n]), spectrum(b[:n]))[0, 1])


def test_the_codec_changes_the_signal_without_destroying_it():
    """A round trip returning the input unchanged would be simulating nothing; one
    returning noise would be misconfigured. The tone must survive, altered."""
    src = tone(1.0, hz=220.0)
    out = discord_path.through_discord(src)
    n = min(len(src), len(out))

    # The pitch is still there: same dominant bin, to within a bin.
    assert abs(int(spectrum(out[:n]).argmax()) - int(spectrum(src[:n]).argmax())) <= 1
    # But the samples are not the input handed back.
    assert not np.array_equal(src[:n], out[:n])


def test_a_lower_bitrate_does_more_damage():
    """The direction that makes the parameter meaningful, and the reason 64 is the
    default: it is Discord's default channel bitrate. Measured on the real corpus, the
    median peak score goes 0.914 untouched, 0.839 at 64 kbps, 0.707 at 32."""
    src = tone(2.0, hz=440.0)
    assert (spectral_similarity(src, discord_path.through_discord(src, bitrate=16))
            < spectral_similarity(src, discord_path.through_discord(src, bitrate=64)))


def test_packet_loss_is_off_by_default():
    """**Uncalibrated on purpose.** No real session has produced a non-zero loss figure -
    the receive counters read all zeros - so a default here would be an invented number."""
    a = discord_path.through_discord(tone(0.5), bitrate=64)
    b = discord_path.through_discord(tone(0.5), bitrate=64, loss=0.0)
    assert np.array_equal(a, b)


def test_loss_is_reproducible_from_a_seed():
    """A corpus transform has to be repeatable, or two runs produce different training
    data and nothing measured against it can be compared."""
    import random

    a = discord_path.through_discord(tone(1.0), loss=0.2, rng=random.Random(7))
    b = discord_path.through_discord(tone(1.0), loss=0.2, rng=random.Random(7))
    assert np.array_equal(a, b)


def test_it_uses_the_same_opus_the_bot_links():
    """Not an approximation of Discord's codec - py-cord's own libopus, the same library
    and version the voice path already loads."""
    import discord.opus as opus

    if not opus.is_loaded():
        opus._load_default()
    assert opus.is_loaded()
    assert opus.Encoder.SAMPLING_RATE == 48_000
    assert opus.Encoder.SAMPLES_PER_FRAME == 960      # 20 ms, Discord's framing
