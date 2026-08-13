"""Model spend — what gameplay actually costs, and how much balance is left.

**Only the router costs money.** STT is local and free since
[ADR-0015](../Docs/adr/0015-local-gpu-stt.md), map crops and icons are local CPU, and
every card is templated. So one ledger over one caller covers the whole bill.

Three things this is for, in the order they matter:

1. **The balance is prepaid and running out of it is silent.** The roadmap records an
   eval that reported a 13-point router regression which was in fact a depleted balance:
   every HTTP 429 arrived as a `Decline` and was scored as an honest miss. A number on
   `/palintel status` is what turns that from a mystery into a line item.
2. **Which classes cost money.** `item_source` cannot be fast-pathed while items stay out
   of the lexicon, so it should dominate - and the branch-keyword backlog item is waiting
   on evidence of exactly that.
3. **What fraction of play reaches the model at all.** Every query is logged, billed or
   not, so the fast-path share falls out of the same file rather than needing a second one.

## Where it lives

`data/sessions/<session>/costs.jsonl`, beside the capture clips, one row per query. Evals
write there too under a `eval-<date>` session, because they are the dominant spend and a
balance that ignored them would be wrong in the direction that matters.

**No cached total.** Totals are computed by scanning the session files, which are a few
kilobytes each. This project has been bitten more than once by a recorded number that
went stale - `main` being behind, the breeding gate, the roster - and a spend total is
exactly the kind of thing that would quietly drift from the rows it claims to summarise.

## It never raises into the answer path

The same rule `capture.py` and `saves.py` follow. A full disk must degrade the ledger,
never the answer a player is waiting for.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("palintel.spend")

REPO = Path(__file__).resolve().parents[1]
SESSIONS = REPO / "data" / "sessions"
LEDGER = "costs.jsonl"


@dataclass(frozen=True)
class Charge:
    """One query's cost. `usd` is 0.0 for a fast-path answer, which is the point."""
    at: float
    tool: str                       # the tool chosen, or "decline"
    path: str                       # fast | model | backstop
    usd: float
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    # False for a fast-path answer. Kept explicitly rather than inferred from usd == 0,
    # because an unpriced model also costs 0.0 and the two are different facts.
    billed: bool = False
    who: str = ""

    def as_json(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def charge_from(usage, tool: str, path: str, who: str = "") -> Charge:
    """A `Charge` from whatever usage object the active backend produced.

    Gemini's `GeminiUsage` and Anthropic's `Usage` do not share a base class and both
    predate this module, so the fields are read by name with fallbacks rather than by
    requiring a protocol neither of them implements today. `None` - the fast path, which
    made no call - is a real and common input.
    """
    if usage is None:
        return Charge(at=time.time(), tool=tool, path=path, usd=0.0, billed=False,
                      who=who)
    return Charge(
        at=time.time(), tool=tool, path=path,
        usd=float(getattr(usage, "usd", 0.0) or 0.0),
        model=str(getattr(usage, "model", "")),
        input_tokens=int(getattr(usage, "input", 0) or 0),
        # Gemini prices thinking tokens as output and reports them separately, so they
        # are folded in here - otherwise the token count on a card would not explain the
        # cost beside it, and thinking is 77% of the bill.
        output_tokens=int(getattr(usage, "output", 0) or 0)
        + int(getattr(usage, "thoughts", 0) or 0),
        cached_tokens=int(getattr(usage, "cache_read", 0) or 0),
        billed=True, who=who)


@dataclass
class SpendLog:
    """Appends charges for one session. Errors are swallowed and logged, never raised."""
    session: str
    root: Path = SESSIONS
    total: float = 0.0
    queries: int = 0
    billed: int = 0
    _ok: bool = field(default=True, repr=False)

    def __post_init__(self) -> None:
        self.dir = Path(self.root) / self.session
        self.path = self.dir / LEDGER
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("spend log disabled: %s", e)
            self._ok = False

    @property
    def enabled(self) -> bool:
        return self._ok

    def record(self, charge: Charge) -> None:
        self.queries += 1
        if charge.billed:
            self.billed += 1
            self.total += charge.usd
        if not self._ok:
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(charge.as_json()) + "\n")
        except OSError as e:
            log.warning("spend: could not append %s: %s", self.path, e)


def all_charges(root: Path = SESSIONS) -> list[dict]:
    """Every charge ever logged, across every session. Cheap: a few KB per session."""
    out: list[dict] = []
    if not Path(root).exists():
        return out
    for path in sorted(Path(root).glob(f"*/{LEDGER}")):
        session = path.parent.name
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                row["session"] = session
                out.append(row)
        except (OSError, json.JSONDecodeError) as e:
            # A truncated final line is normal if the process died mid-write. Skipping
            # the file would lose every earlier row in it, so the failure is reported and
            # the rest is kept.
            log.warning("spend: could not read %s: %s", path, e)
    return out


def spent(root: Path = SESSIONS) -> float:
    return sum(r.get("usd", 0.0) for r in all_charges(root))


def by_tool(rows: list[dict]) -> list[tuple[str, int, int, float]]:
    """(tool, queries, billed, usd), dearest first.

    The number the branch-keyword backlog item is waiting for: `item_source` cannot be
    fast-pathed while items stay out of the lexicon, so if it is not near the top of this
    list the assumption behind that entry is wrong.
    """
    agg: dict[str, list] = {}
    for r in rows:
        a = agg.setdefault(r.get("tool") or "?", [0, 0, 0.0])
        a[0] += 1
        a[1] += bool(r.get("billed"))
        a[2] += r.get("usd", 0.0)
    return sorted(((t, n, b, u) for t, (n, b, u) in agg.items()),
                  key=lambda x: -x[3])


def by_user(rows: list[dict]) -> list[tuple[str, int, int, float]]:
    """(who, queries, billed, usd), dearest first.

    **The question one shared prepaid balance makes worth asking.** Spend is $0.0048 a
    request and a party of four asking freely is four times the burn, so "who is spending
    it" stops being idle curiosity the moment more than one person can ask. `who` has been
    written on every charge since the ledger existed and nothing has ever read it back.

    Note `billed` against `queries`: the interesting number is usually not the money but
    the share reaching the model at all, which is what says whether somebody's phrasings
    are missing the fast path.
    """
    agg: dict[str, list] = {}
    for r in rows:
        a = agg.setdefault(r.get("who") or "(unattributed)", [0, 0, 0.0])
        a[0] += 1
        a[1] += bool(r.get("billed"))
        a[2] += r.get("usd", 0.0)
    return sorted(((w, n, b, u) for w, (n, b, u) in agg.items()),
                  key=lambda x: -x[3])


def describe_users(rows: list[dict], limit: int = 5) -> str:
    """Per-person spend for `/palintel status`, or "" when only one person has asked.

    Empty for a single speaker on purpose: a breakdown of one is noise, and the status
    card is already dense. It appears exactly when it starts meaning something.
    """
    users = by_user(rows)
    if len(users) < 2:
        return ""
    shown = users[:limit]
    parts = [f"{w} {n}q" + (f"/${u:.3f}" if u >= 0.0005 else "")
             for w, n, _b, u in shown]
    if len(users) > limit:
        parts.append(f"+{len(users) - limit} more")
    return " | ".join(parts)


def describe(session: "SpendLog | None", balance_usd: float = 0.0,
             warn_below: float = 0.0, root: Path = SESSIONS) -> str:
    """One line for `/palintel status`.

    Reports the session and the all-time total separately, because they answer different
    questions - "is this session unusually expensive" and "how much have I spent" - and a
    single figure would answer neither.
    """
    rows = all_charges(root)
    total = sum(r.get("usd", 0.0) for r in rows)
    if session is None and not rows:
        return "nothing logged yet"

    def money(usd: float) -> str:
        # Adaptive precision. Two decimals renders a real session as "$0.00", which reads
        # as "nothing was spent" when the truth is "half a cent" - and the whole point of
        # the line is that small numbers accumulate into a balance.
        return f"${usd:.2f}" if usd >= 1 else f"${usd:.4f}"

    parts = []
    if session is not None:
        share = (f", {session.billed}/{session.queries} reached the model"
                 if session.queries else "")
        parts.append(f"this session {money(session.total)}{share}")
    parts.append(f"all time {money(total)} over {len(rows)} queries")

    if balance_usd > 0:
        left = balance_usd - total
        parts.append(f"**${left:.2f} of ${balance_usd:.2f} left**"
                     if left <= warn_below else f"${left:.2f} left")
        if left <= 0:
            # The failure this exists for. A depleted balance arrives as HTTP 429, every
            # 429 becomes a Decline, and a run once reported that as a 13-point router
            # regression before anyone checked the balance.
            parts.append("**BALANCE EXHAUSTED - every model call will 429 and look "
                         "like a decline**")
    return " | ".join(parts)
