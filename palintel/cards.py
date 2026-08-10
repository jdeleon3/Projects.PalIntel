"""Card rendering — typed results to display, with no model in the path.

Templated, never generated. Factual fields are interpolated straight from typed results,
which makes coordinate fabrication structurally impossible rather than merely unlikely.
See Docs/adr/0006-templated-cards.md.

Two renderers over one card model: plain text for the CLI harness, and a Discord embed
dict for the bot. They must never diverge in content - only in presentation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .execution import ResourceResult
from .tools import Decline

# Tier colours, so a reader can tell fact from recommendation from reference at a glance
# without reading carefully (Docs/adr/0010-three-tier-answer-model.md).
TIER_FACT = 0x2E7D32       # green
TIER_ADVICE = 0xF9A825     # amber
TIER_REFERENCE = 0x1565C0  # blue
TIER_DECLINE = 0x757575    # grey


@dataclass
class Card:
    title: str
    lines: list[str] = field(default_factory=list)
    footer: str = ""
    colour: int = TIER_FACT

    def to_text(self) -> str:
        out = [self.title, "-" * len(self.title), *self.lines]
        if self.footer:
            out += ["", self.footer]
        return "\n".join(out)

    def to_embed(self) -> dict:
        return {
            "title": self.title,
            "description": "\n".join(self.lines),
            "color": self.colour,
            "footer": {"text": self.footer} if self.footer else None,
        }


SEP = " | "  # ASCII: Windows consoles mangle typographic separators

# Resources the player can name but which are not placed nodes. Extraction found no
# spawner class for crude oil in the overworld, so "no results" would be technically
# true and actively misleading - it reads as "none nearby" rather than "not a thing I
# can locate". See Docs/03-data-ingestion.md.
NOT_PLACED = {
    "crude_oil": "Crude oil isn't a mineable node - it comes from oil rigs, "
                 "so there are no map locations to give you.",
}


def resource_card(result: ResourceResult) -> Card:
    name = result.resource.replace("_", " ").title()

    if not result.nodes:
        if result.resource in NOT_PLACED:
            return Card(title=f"{name} has no node locations",
                        lines=[NOT_PLACED[result.resource]], colour=TIER_DECLINE)
        return Card(
            title=f"No {name} found",
            lines=["Nothing matched in my data."
                   + (" Try without a level limit." if result.level_filtered else "")],
            footer=f"{result.total_available} {name.lower()} clusters known",
            colour=TIER_DECLINE,
        )

    lines = []
    for i, n in enumerate(result.nodes, 1):
        bits = [f"**{i}. ({n.map_x:.0f}, {n.map_y:.0f})**",
                f"{n.node_count} deposit{'s' if n.node_count != 1 else ''}"]
        if result.near is not None:
            bits.append(f"{n.distance_to(*result.near):.0f} units away")
        if n.danger:
            bits.append(f"danger: {n.danger}")
        if n.min_player_level:
            bits.append(f"lvl {n.min_player_level}+")
        if n.area_hint:
            bits.append(n.area_hint.replace("_", " "))
        lines.append(SEP.join(bits))

    footer = f"{result.total_available} {name.lower()} clusters known"
    if result.near is None:
        # Say what the ordering means rather than letting "1." imply nearest.
        footer += SEP + "sorted by size (no position known)"
    return Card(title=f"{name} locations", lines=lines, footer=footer, colour=TIER_FACT)


def clarify_card(options: list[str]) -> Card:
    """Ask which of several entities was meant, instead of answering all of them.

    Two answers render as two cards the reader picks between. More than that stops being
    a set of options and becomes a wall, and the reader ends up doing the disambiguation
    the router declined to do - so past the cap the honest move is to ask.

    Distinct from decline_card: nothing failed here. The query was understood and the
    entity was not narrowed, so the question is specific rather than an apology.
    """
    return Card(
        title="Which one?",
        # ASCII bullet: Discord renders "- " as a list, and the CLI renderer runs on a
        # cp1252 console where a U+2022 arrives as a replacement character.
        lines=[f"That could be **{len(options)}** different Pals:",
               *(f"- {o}" for o in options),
               "", "_Ask again naming one of them._"],
        colour=TIER_DECLINE,
    )


def status_card(log, *, voice: str, save: str = "not configured",
                window_label: str = "last hour") -> Card:
    """Report what the pipeline has actually seen, stage by stage.

    The breakdown is the whole point. ADR-0004 flags wake-word false negatives as silent
    failures, but "voice is broken" has four distinct causes that feel identical to the
    player, and each shows a different shape here: no activations at all points at the
    detector or the mic; activations without transcripts means it fired on noise;
    transcripts without answers means routing. Reporting a single health figure would
    throw away exactly the information needed to tell them apart.
    """
    from .activity import duration

    c = log.counts()
    fired = c.get("wake", 0)
    lines = [f"**Voice:** {voice}",
             # Worth its own line: a stale or unread save is invisible in the answers -
             # "nearest" silently falls back to ranking by cluster size and still returns
             # a confident-looking coordinate.
             f"**Save:** {save}",
             f"**Up:** {duration(log.uptime())}",
             "",
             f"__In the {window_label}__",
             f"- Wake word fired: **{fired}**",
             f"- Transcribed: **{c.get('heard', 0)}**"
             + (f"  (+{c['empty']} silent)" if c.get("empty") else ""),
             f"- Answered: **{c.get('answered', 0)}**"
             + (f"  ({c['declined']} declined)" if c.get("declined") else "")]

    if c.get("failed"):
        lines.append(f"- Errors: **{c['failed']}** (see the log)")
    if c.get("undelivered"):
        # A card that was built and never reached the channel. Its own line because it is
        # the only failure the player experiences as the bot simply ignoring them, and it
        # used to be counted as an answer.
        lines.append(f"- **Not delivered: {c['undelivered']}** (built, send failed)")
    if c.get("overflow"):
        # Dropped input frames present as a wake word that intermittently misses, which
        # is the hardest voice failure to diagnose from the outside.
        lines.append(f"- Audio dropped: **{c['overflow']}** (mic overruns)")

    lines += _latency_lines(log)

    last = log.ago("wake")
    lines.append("")
    lines.append(f"Last activation: **{last}**" if last
                 else "_No activation yet this session._")
    return Card(title="PalIntel status", lines=lines, colour=TIER_REFERENCE)


# The Phase 1 exit bars, from 00-overview.md. Here rather than in the roadmap prose so
# the card can say whether the session passed instead of leaving the reader to compare.
BUDGET_MS = {"voice": 2500.0, "text": 1500.0}
MIN_SAMPLES = 30          # the exit criterion's own "over >= 30 real queries each"


def _latency_lines(log) -> list[str]:
    """End-to-end p95 against the budget, and the stage breakdown when it misses.

    Absent until something has actually been timed: a status card that reports 0ms
    before the first query reads like a passing grade nobody earned.
    """
    totals = {k: log.percentiles(k) for k in ("voice", "text")}
    if not any(totals.values()):
        return []

    out = ["", "__Latency__"]
    for kind, stats in totals.items():
        if stats is None:
            continue
        n, p50, p95 = stats
        budget = BUDGET_MS[kind]
        # Three states, not two. "Under budget on 6 queries" is not a pass - the exit
        # criterion asks for 30 - and reporting it as one is how a bar gets quietly
        # cleared on a sample too small to mean anything.
        if n < MIN_SAMPLES:
            verdict = f"⏳ {n}/{MIN_SAMPLES} queries"
        else:
            verdict = "✅" if p95 <= budget else "❌"
        out.append(f"- {kind.title()}: p50 **{p50 / 1000:.1f}s**, "
                   f"p95 **{p95 / 1000:.1f}s** / {budget / 1000:.1f}s  {verdict}")

    stages = [(k, log.percentiles(k)) for k in ("stt", "route", "post")]
    stages = [(k, s) for k, s in stages if s]
    if stages:
        out.append("- where it goes (p50): "
                   + ", ".join(f"{k} **{s[1] / 1000:.2f}s**" for k, s in stages))
    return out


def recent_card(log, limit: int = 12) -> Card:
    """The last few queries, one line each, with where the time went.

    The counts on the status card answer "is voice working"; they cannot answer "what
    happened to the thing I just said", which is the question actually asked when one
    query appears to vanish. The text and the routing time were already being recorded -
    only nothing displayed them, so the data to diagnose a missing answer existed and
    was unreachable from Discord, which is where the person asking is standing.

    Route time is the tell: ~0.1s means the fast path answered it, seconds mean the
    model did. That distinction is otherwise invisible in play.
    """
    from .activity import ago

    events = [e for e in log.since() if e.kind in ("route", "empty", "undelivered")]
    if not events:
        return Card(title="Recent queries", colour=TIER_REFERENCE,
                    lines=["_Nothing yet this session._"])

    now = time.monotonic()
    lines = []
    for e in events[-limit:]:
        when = ago(now - e.at)
        if e.kind == "route":
            path = "fast" if e.ms is not None and e.ms < 300 else "model"
            lines.append(f"`{e.ms / 1000:4.1f}s` {path:<5} {e.detail}  _{when}_")
        elif e.kind == "empty":
            # Fired, recorded, transcribed to nothing. The signature of the detector
            # triggering on noise rather than on speech.
            lines.append(f"`  -  ` empty  _(no speech in {e.detail})_  _{when}_")
        else:
            lines.append(f"`  -  ` **UNDELIVERED** {e.detail}  _{when}_")
    return Card(title="Recent queries", lines=lines, colour=TIER_REFERENCE)



def decline_card(decline: Decline) -> Card:
    lines = ["I didn't catch that."]
    if decline.unrecognized:
        # Naming the unrecognised token lets the player retry precisely, and is the
        # visible half of never silently coercing a low-confidence match.
        lines.append(f"I couldn't match: **{decline.unrecognized}**")
    if decline.known_options:
        opts = ", ".join(o.replace("_", " ") for o in decline.known_options)
        lines.append(f"I can currently find: **{opts}**")
    lines.append(f"_{decline.reason}_")
    return Card(title="Didn't understand", lines=lines, colour=TIER_DECLINE)
