"""Model spend — the ledger, the totals, and the balance warning.

The point of the module is a number on a status card, so most of these are about what
that number must not do: hide a fast-path query, lose a session, or go quiet when the
balance is gone. The last one has a history — the roadmap records a depleted balance
being read as a 13-point router regression, because every HTTP 429 arrived as a Decline.
"""
from __future__ import annotations

from dataclasses import dataclass

from palintel import spend


@dataclass
class FakeUsage:
    """Shaped like GeminiUsage. The two backends share no base class, which is why
    `charge_from` reads fields by name."""
    usd: float = 0.004
    model: str = "gemini-3.6-flash"
    input: int = 4000
    output: int = 300
    thoughts: int = 120
    cache_read: int = 3800


def test_a_fast_path_answer_is_logged_at_zero_rather_than_not_at_all(tmp_path):
    """**What fraction of play reaches the model is the same question as what it costs.**
    Logging only billed calls would answer one of the two and make the other need a
    second file."""
    log = spend.SpendLog("s1", root=tmp_path)
    log.record(spend.charge_from(None, tool="find_resource_nodes", path="fast"))
    log.record(spend.charge_from(FakeUsage(), tool="find_item_source", path="model"))

    assert log.queries == 2 and log.billed == 1
    rows = spend.all_charges(tmp_path)
    assert len(rows) == 2
    assert [r["billed"] for r in rows] == [False, True]


def test_thinking_tokens_are_folded_into_output(tmp_path):
    """Gemini prices thoughts as output and reports them separately, and they are 77% of
    the bill. A token count on a card that did not explain the cost beside it would be
    worse than no token count."""
    charge = spend.charge_from(FakeUsage(), tool="x", path="model")
    assert charge.output_tokens == 300 + 120


def test_an_unpriced_model_is_billed_but_free(tmp_path):
    """`billed` is carried rather than inferred from `usd == 0`: an unpriced model also
    costs 0.0, and "we called a model we cannot price" is a different fact from "the fast
    path answered"."""
    charge = spend.charge_from(FakeUsage(usd=0.0, model="something-new"),
                               tool="x", path="model")
    assert charge.billed and charge.usd == 0.0


def test_totals_span_sessions(tmp_path):
    for session in ("s1", "s2"):
        log = spend.SpendLog(session, root=tmp_path)
        log.record(spend.charge_from(FakeUsage(usd=0.01), tool="x", path="model"))
    assert spend.spent(tmp_path) == 0.02
    assert {r["session"] for r in spend.all_charges(tmp_path)} == {"s1", "s2"}


def test_a_truncated_row_does_not_lose_the_rest_of_the_file(tmp_path):
    """A process killed mid-write leaves half a line. Skipping the file would lose every
    earlier row in it."""
    log = spend.SpendLog("s1", root=tmp_path)
    log.record(spend.charge_from(FakeUsage(usd=0.01), tool="x", path="model"))
    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"usd": 0.5, "tool"')       # died here
    rows = spend.all_charges(tmp_path)
    assert len(rows) == 1 and rows[0]["usd"] == 0.01


def test_by_tool_ranks_the_expensive_classes_first(tmp_path):
    """The number the branch-keyword backlog item is waiting for: `item_source` cannot be
    fast-pathed while items stay out of the lexicon, so if it is not near the top the
    assumption behind that entry is wrong."""
    log = spend.SpendLog("s1", root=tmp_path)
    for _ in range(3):
        log.record(spend.charge_from(FakeUsage(usd=0.01), tool="find_item_source",
                                     path="model"))
    log.record(spend.charge_from(None, tool="find_resource_nodes", path="fast"))
    ranked = spend.by_tool(spend.all_charges(tmp_path))
    assert ranked[0][0] == "find_item_source"
    assert ranked[0][3] == 0.03


# ------------------------------------------------------------------ the balance

def test_a_healthy_balance_reports_without_shouting(tmp_path):
    log = spend.SpendLog("s1", root=tmp_path)
    log.record(spend.charge_from(FakeUsage(usd=1.0), tool="x", path="model"))
    line = spend.describe(log, balance_usd=20.0, warn_below=2.0, root=tmp_path)
    assert "$19.00 left" in line and "**" not in line.split("left")[0][-12:]


def test_a_low_balance_is_emphasised(tmp_path):
    log = spend.SpendLog("s1", root=tmp_path)
    log.record(spend.charge_from(FakeUsage(usd=19.0), tool="x", path="model"))
    assert "**$1.00 of $20.00 left**" in spend.describe(
        log, balance_usd=20.0, warn_below=2.0, root=tmp_path)


def test_an_exhausted_balance_says_what_will_actually_happen(tmp_path):
    """**The failure this module exists for.** A depleted balance arrives as HTTP 429,
    every 429 becomes a Decline, and the roadmap records one run reading that as a
    13-point router regression before anyone checked."""
    log = spend.SpendLog("s1", root=tmp_path)
    log.record(spend.charge_from(FakeUsage(usd=25.0), tool="x", path="model"))
    line = spend.describe(log, balance_usd=20.0, warn_below=2.0, root=tmp_path)
    assert "EXHAUSTED" in line and "look like a decline" in line


def test_no_configured_balance_reports_totals_and_claims_nothing(tmp_path):
    log = spend.SpendLog("s1", root=tmp_path)
    log.record(spend.charge_from(FakeUsage(usd=1.0), tool="x", path="model"))
    line = spend.describe(log, balance_usd=0.0, root=tmp_path)
    assert "all time" in line and "left" not in line


def test_a_sub_penny_total_is_not_rounded_to_nothing(tmp_path):
    """Two decimals renders a real session as "$0.00", which reads as "nothing was spent"
    when the truth is half a cent - and the whole point of the line is that small numbers
    accumulate into a balance."""
    log = spend.SpendLog("s1", root=tmp_path)
    log.record(spend.charge_from(FakeUsage(usd=0.0048), tool="x", path="model"))
    line = spend.describe(log, root=tmp_path)
    assert "$0.0048" in line and "all time $0.00 " not in line


def test_the_session_and_the_all_time_total_are_separate(tmp_path):
    """They answer different questions - "is this session unusually expensive" and "how
    much have I spent" - and one figure would answer neither."""
    old = spend.SpendLog("s0", root=tmp_path)
    old.record(spend.charge_from(FakeUsage(usd=5.0), tool="x", path="model"))
    now = spend.SpendLog("s1", root=tmp_path)
    now.record(spend.charge_from(FakeUsage(usd=0.5), tool="x", path="model"))
    line = spend.describe(now, root=tmp_path)
    assert "this session $0.5000" in line and "all time $5.50" in line


def test_a_broken_directory_never_raises(tmp_path):
    """Same rule capture.py and saves.py follow: diagnostics must degrade, never take
    down the answer a player is waiting for."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    log = spend.SpendLog("s1", root=blocker)
    assert not log.enabled
    log.record(spend.charge_from(FakeUsage(), tool="x", path="model"))
    assert log.total > 0          # still counted in memory for the status line
