"""Reading a capture session: the fold, the runs, and the proposals.

The module's job is to hand a human candidates, not to decide. So most of these are about
what it must NOT do: turn an inference into a label, count one stubborn question three
times, or guess a name from a run of failures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))
import analyse_session as an  # noqa: E402

SESSION = Path(__file__).resolve().parents[1] / "data" / "sessions" / "20260811-191709"


def write_log(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "s1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return d


def utt(uid, at, heard, outcome="answered", entity=None, path="model"):
    return {"uid": uid, "at": at, "heard": heard, "path": path, "tool": "t",
            "entity": entity, "outcome": outcome, "label": "auto"}


# ------------------------------------------------------------------ the fold

def test_feedback_joins_to_its_utterance_through_the_message_id(tmp_path):
    """The log is append-only: a button press minutes later writes its own line keyed by
    the Discord message, not by the clip. Folding the three row kinds is the reader's job
    and this is the reader."""
    d = write_log(tmp_path, [
        utt("a", 1.0, "tell me about Lani", outcome="declined"),
        {"uid": "a", "message_id": 999},
        {"message_id": 999, "feedback": "misheard", "label": "user", "at": 60.0},
    ])
    turns = an.load(d)
    assert len(turns) == 1
    assert turns[0].misheard and turns[0].feedback == ["misheard"]


def test_feedback_for_an_unknown_message_is_dropped_not_crashed(tmp_path):
    d = write_log(tmp_path, [
        utt("a", 1.0, "hello"),
        {"message_id": 12345, "feedback": "misheard", "label": "user"},
    ])
    assert an.load(d)[0].feedback == []


# ------------------------------------------------------------------ failure runs

def test_a_run_of_attempts_at_one_name_is_one_group(tmp_path):
    """A run of three pronunciations of one word is worth MORE than a single miss and
    must not be three times the weight of one."""
    d = write_log(tmp_path, [
        utt("a", 0, "tell me about Lani", outcome="declined"),
        utt("b", 20, "tell me about Lening", outcome="declined"),
        utt("c", 40, "tell me about Leneen", outcome="declined"),
    ])
    runs = an.failure_runs(an.load(d))
    assert len(runs) == 1 and len(runs[0]) == 3


def test_an_answer_ends_a_run(tmp_path):
    d = write_log(tmp_path, [
        utt("a", 0, "tell me about Lani", outcome="declined"),
        utt("b", 20, "tell me about Leneen", entity="Lyleen"),
        utt("c", 40, "where is coal", outcome="declined"),
    ])
    runs = an.failure_runs(an.load(d))
    assert [len(r) for r in runs] == [1, 1]


def test_unrelated_failures_are_separate_runs(tmp_path):
    d = write_log(tmp_path, [
        utt("a", 0, "tell me about Lani", outcome="declined"),
        utt("b", 10, "where should I put a base", outcome="declined"),
    ])
    assert len(an.failure_runs(an.load(d))) == 2


def test_a_long_pause_ends_a_run(tmp_path):
    """Past the window they are two attempts at the same thing on either side of doing
    something else, not one run."""
    d = write_log(tmp_path, [
        utt("a", 0, "tell me about Lani", outcome="declined"),
        utt("b", an.REPHRASE_WINDOW_S + 30, "tell me about Lani", outcome="declined"),
    ])
    assert len(an.failure_runs(an.load(d))) == 2


# ------------------------------------------------------------------ rephrases

def test_a_failure_followed_by_a_success_proposes_the_entity(tmp_path):
    d = write_log(tmp_path, [
        utt("a", 0, "how do I beat Exo", outcome="declined"),
        utt("b", 40, "how do I beat Axel", entity="Axel"),
    ])
    props = an.rephrases(an.load(d))
    assert len(props) == 1
    assert props[0].entity == "Axel" and props[0].surface == "exo"


def test_a_proposal_is_marked_human_confirmed_only_by_a_button(tmp_path):
    """A misheard row says the transcript was wrong without any guessing at all. It is
    the only ground truth in the file, and the only thing that changes the word."""
    rows = [utt("a", 0, "how do I beat Exo", outcome="declined"),
            utt("b", 40, "how do I beat Axel", entity="Axel")]
    assert not an.rephrases(an.load(write_log(tmp_path, rows)))[0].human_confirmed

    d2 = write_log(tmp_path / "x", rows + [{"uid": "a", "message_id": 7},
                                           {"message_id": 7, "feedback": "misheard"}])
    assert an.rephrases(an.load(d2))[0].human_confirmed


def test_the_surface_is_the_likeliest_token_not_the_whole_difference(tmp_path):
    """*"Play Pal with this Gilderoy and Dromatite drop"* differs from its retry by four
    words, and an alias built from that phrase would match sentences it has nothing to do
    with - the failure harvest_aliases holds ordinary-word candidates for review over."""
    d = write_log(tmp_path, [
        # The real pair, verbatim from the 2026-08-11 session.
        utt("a", 0, "Play Pal with this Gilderoy and Dromatite drop.",
            outcome="declined"),
        utt("b", 64, "Hey pal, what does Gidra and Dromatide drop?", entity="Gildra"),
    ])
    p = an.rephrases(an.load(d))[0]
    assert p.surface == "gilderoy"
    assert " " in p.surface_all         # the full difference travels for review


def test_a_different_question_in_the_same_frame_is_not_a_rephrase(tmp_path):
    """The frame is identical and the subject is not. Similarity alone cannot tell this
    from a real pair - see the module docstring - so the window and the ordering do the
    work and a human makes the call."""
    d = write_log(tmp_path, [
        utt("a", 0, "tell me about Lani", outcome="declined"),
        utt("b", an.REPHRASE_WINDOW_S + 60, "tell me about Orserk", entity="Orserk"),
    ])
    assert an.rephrases(an.load(d)) == []


def test_only_the_first_matching_success_is_proposed(tmp_path):
    """One failure, one hypothesis. Proposing every later success would bury the reviewer
    in variants of one guess."""
    d = write_log(tmp_path, [
        utt("a", 0, "how do I beat Exo", outcome="declined"),
        utt("b", 20, "how do I beat Axel", entity="Axel"),
        utt("c", 40, "how do I beat Auri", entity="Auri"),
    ])
    props = an.rephrases(an.load(d))
    assert len(props) == 1 and props[0].entity == "Axel"


# ------------------------------------------------- the real session, as a regression

def test_the_real_session_reproduces_its_known_findings():
    """**The session has a known-correct answer for three of its ten manglings**, because
    somebody worked the Lani run through by hand in August and those aliases are in the
    lexicon. Anything that stops reproducing this has broken the reader."""
    if not (SESSION / "log.jsonl").exists():
        pytest.skip("the reference session is not present")
    turns = an.load(SESSION)
    assert len(turns) == 41
    assert sum(1 for t in turns if t.feedback) == 9
    assert sum(1 for t in turns if t.misheard) == 6

    props = {p.surface: p.entity for p in an.rephrases(turns)}
    # The hand-mined run, recovered automatically.
    assert props.get("lening") == "Lyleen" and props.get("lani") == "Lyleen"
    # The one nobody got to.
    assert props.get("gilderoy") == "Gildra"
    # Every proposal in this session sits on a human button press.
    assert all(p.human_confirmed for p in an.rephrases(turns))


def test_a_misheard_with_no_rephrase_stays_unresolved():
    """`expected: null`. Guessing the name from a run of failures is writing fiction."""
    if not (SESSION / "log.jsonl").exists():
        pytest.skip("the reference session is not present")
    turns = an.load(SESSION)
    explained = {p.failed.uid for p in an.rephrases(turns)}
    unresolved = [t for t in turns if t.misheard and t.uid not in explained]
    assert len(unresolved) == 1


# --- coverage: the ledger against the corpus -----------------------------------
#
# Added after 2026-08-13, where the two files disagreed by a factor of 21 in the same
# directory and nothing anywhere divided one by the other. The defect was found by a
# player noticing a missing button, which is not a detection strategy.

def _with_ledger(tmp_path, captured: int, billed: int) -> Path:
    d = write_log(tmp_path, [utt(f"u{i}", float(i), "q") for i in range(captured)])
    (d / "costs.jsonl").write_text(
        "\n".join(json.dumps({"at": float(i), "usd": 0.0}) for i in range(billed)),
        encoding="utf-8")
    return d


def test_a_channel_that_answers_without_capturing_is_reported(tmp_path, capsys):
    """The 2026-08-13 shape exactly: 64 billed, 3 captured."""
    an.coverage(_with_ledger(tmp_path, 3, 64), 3)
    out = capsys.readouterr().out
    assert "3/64" in out
    assert "61 queries were answered and billed but never reached the corpus" in out


def test_full_coverage_says_so_without_raising_an_alarm(tmp_path, capsys):
    an.coverage(_with_ledger(tmp_path, 9, 9), 9)
    out = capsys.readouterr().out
    assert "9/9" in out and "100%" in out
    assert "never reached the corpus" not in out


def test_a_session_with_no_ledger_is_not_an_error(tmp_path, capsys):
    """Eval corpora and pre-spend-tracking sessions have no costs.jsonl, and neither is a
    fault. Reporting one as a capture defect would train the reader to ignore the line."""
    d = write_log(tmp_path, [utt("u0", 0.0, "q")])
    an.coverage(d, 1)
    assert "nothing to compare against" in capsys.readouterr().out


def test_the_ratio_is_printed_whatever_it_is(tmp_path, capsys):
    """No threshold, deliberately. A bar invites tuning the bar, and the signal that
    mattered was so far off that no bar was needed to see it."""
    for captured, billed in ((62, 64), (1, 2), (64, 64)):
        an.coverage(_with_ledger(tmp_path, captured, billed), captured)
        assert f"{captured}/{billed}" in capsys.readouterr().out
