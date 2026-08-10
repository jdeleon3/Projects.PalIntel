"""Activity log and status card.

What is worth testing here is not that counting works - it is that the *shape* of the
report distinguishes the failure modes it exists to distinguish. ADR-0004's silent
failure has several causes that feel identical to the player, and a status card that
reads the same for all of them would be decoration.
"""
from __future__ import annotations

from palintel.activity import ActivityLog, ago, duration
from palintel.cards import status_card


def test_counts_are_per_kind():
    log = ActivityLog()
    log.record("wake", "hey_pal at 0.81")
    log.record("wake", "hey_pal at 0.64")
    log.record("heard", "where is coal")
    assert log.counts() == {"wake": 2, "heard": 1}


def test_events_outside_the_window_are_excluded():
    """An event is aged directly rather than by waiting or by a zero-width window.

    A zero window looks like the obvious test and is not one: Windows' monotonic clock
    ticks at ~15ms, so an event recorded in the same tick compares equal to the cutoff
    and the test passes or fails on scheduling.
    """
    import time

    from palintel.activity import Event

    log = ActivityLog()
    log.record("wake")
    log._events.append(Event(time.monotonic() - 7200, "wake", "two hours ago"))
    assert log.counts(window=3600.0) == {"wake": 1}
    assert log.counts(window=10_000.0) == {"wake": 2}


def test_ago_is_none_until_the_kind_happens():
    log = ActivityLog()
    assert log.ago("wake") is None
    log.record("wake")
    assert log.ago("wake") == "0s ago"


def test_ring_buffer_does_not_grow_without_bound():
    from palintel.activity import MAX_EVENTS
    log = ActivityLog()
    for _ in range(MAX_EVENTS + 50):
        log.record("wake")
    assert log.counts()["wake"] == MAX_EVENTS


def test_silent_detector_reads_differently_from_a_noisy_one():
    """The two failures that feel identical to the player must not read identically.

    Nothing firing points at the mic or the model; firing without transcripts points at
    the threshold. A single health number would collapse both into "voice is broken".
    """
    quiet = status_card(ActivityLog(), voice="mic - hey_pal @ 0.5").to_text()
    assert "No activation yet" in quiet

    noisy = ActivityLog()
    for _ in range(9):
        noisy.record("wake", "hey_pal at 0.52")
        noisy.record("empty", "0.8s")
    text = status_card(noisy, voice="mic - hey_pal @ 0.5").to_text()
    assert "**9**" in text and "9 silent" in text
    assert "No activation yet" not in text


def test_dropped_audio_is_only_reported_when_it_happens():
    log = ActivityLog()
    assert "Audio dropped" not in status_card(log, voice="x").to_text()
    log.record("overflow", "input overflow")
    assert "Audio dropped" in status_card(log, voice="x").to_text()


def test_formatting_is_coarse():
    assert ago(4) == "4s ago"
    assert ago(125) == "2m ago"
    assert ago(7200) == "2.0h ago"
    assert duration(90) == "1m"
    assert duration(7500) == "2h 5m"


# ---------------------------------------------------------------------- latency

def test_percentiles_are_none_until_something_is_timed():
    log = ActivityLog()
    log.record("answered", "where is coal")
    assert log.percentiles("voice") is None


def test_untimed_events_stay_out_of_the_percentiles():
    """`record` and `timed` write to the same buffer; only the latter has a duration."""
    log = ActivityLog()
    log.record("voice", "not a timing")
    log.timed("voice", 1200.0)
    assert log.percentiles("voice") == (1, 1200.0, 1200.0)


def test_percentiles_are_nearest_rank():
    log = ActivityLog()
    for ms in range(1, 21):        # 1..20
        log.timed("text", float(ms * 100))
    n, p50, p95 = log.percentiles("text")
    assert n == 20
    assert p50 == 1100.0           # index 10 of 0..19
    # int(20 * 0.95) = 19, the slowest sample. With 20 points that IS the honest p95 -
    # an interpolated 1905 would imply a resolution twenty samples cannot support.
    assert p95 == 2000.0


def test_status_card_omits_latency_before_any_query():
    assert "Latency" not in status_card(ActivityLog(), voice="x").to_text()


def test_status_card_will_not_call_a_thin_sample_a_pass():
    """Under budget on six queries is not the exit criterion, and must not read as one."""
    log = ActivityLog()
    for _ in range(6):
        log.timed("voice", 900.0)
    text = status_card(log, voice="x").to_text()
    assert "6/30 queries" in text
    assert "✅" not in text


def test_status_card_grades_against_the_budget():
    slow, fast = ActivityLog(), ActivityLog()
    for _ in range(30):
        slow.timed("voice", 4000.0)     # over the 2.5s bar
        fast.timed("voice", 1200.0)     # under it
    assert "❌" in status_card(slow, voice="x").to_text()
    assert "✅" in status_card(fast, voice="x").to_text()


def test_status_card_shows_where_the_time_went():
    log = ActivityLog()
    for _ in range(30):
        log.timed("voice", 4000.0)
        log.timed("stt", 800.0)
        log.timed("route", 2100.0)
    text = status_card(log, voice="x").to_text()
    assert "stt" in text and "route" in text
