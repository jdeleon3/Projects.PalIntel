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

from .execution import ResourceResult, SpawnResult
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


KIND_LABEL = {"alpha": "field alpha", "predator": "predator"}


def spawn_card(result: SpawnResult) -> Card:
    """Where a Pal is found. Same shape as the resource card, one column different.

    That column is `encounter_share`, and it is the field that stops this being a worse
    answer than no answer. A coordinate alone reads as "go here and you will find one";
    for a Pal sitting at 2% weight in a shared sheet, standing there for ten minutes and
    seeing nothing is the normal outcome. The percentage is what makes the card honest
    about that rather than the player concluding the data is wrong.
    """
    if not result.in_overworld:
        # Not a miss. The game genuinely never places this one in the world - a tower
        # boss, a raid boss, a dungeon-only species - and "keep looking" is wrong advice.
        return Card(
            title=f"{result.pal} isn't found in the overworld",
            lines=[f"**{result.pal}** has no wild spawn on the map. It comes from a "
                   f"tower, a raid, a dungeon or breeding, not from a location I can "
                   f"point you at."],
            colour=TIER_DECLINE,
        )

    if not result.areas:
        detail = " at night" if result.kind is None else ""
        return Card(
            title=f"No {result.pal} spawns found",
            lines=[f"Nothing matched in my data{detail}."],
            colour=TIER_DECLINE,
        )

    lines = []
    if result.kind_substituted:
        # The player asked where to find one and the only answer is a level 55 boss.
        # Leading with that, because walking in expecting an ordinary encounter is how
        # a technically-correct coordinate gets someone killed.
        lines.append(f"_The only {result.pal} out there is a "
                     f"{KIND_LABEL.get(result.kind, result.kind)}._")

    for i, a in enumerate(result.areas, 1):
        bits = [f"**{i}. ({a.map_x:.0f}, {a.map_y:.0f})**"]
        bits.append(f"lvl {a.level_min}" if a.level_min == a.level_max
                    else f"lvl {a.level_min}-{a.level_max}")
        if result.near is not None:
            bits.append(f"{a.distance_to(*result.near):.0f} units away")
        # A one-point area is a single spawner, not a region. Saying "1 spawn point"
        # sets the right expectation for standing there.
        bits.append(f"{a.spawn_points} spawn point"
                    f"{'s' if a.spawn_points != 1 else ''}")
        if a.kind != "normal":
            bits.append(KIND_LABEL.get(a.kind, a.kind))
        elif a.encounter_share < 0.99:
            bits.append(f"{a.encounter_share:.0%} of spawns here")
        if a.night_only:
            bits.append("night only")
        lines.append(SEP.join(bits))

    footer = f"{result.total_available} area{'s' if result.total_available != 1 else ''} known"
    if result.near is None:
        # Say what the ordering means rather than letting "1." imply nearest.
        footer += SEP + "sorted by likelihood (no position known)"
    return Card(title=f"{result.pal} locations", lines=lines, footer=footer,
                colour=TIER_FACT)


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


def plain(text: str) -> str:
    """Escape Discord markdown in an interpolated value.

    `hey_pal` carries a lone underscore, and Discord pairs it with the next one anywhere
    in the same embed - the `_(answered queries)_` in the latency header - italicising
    everything between and swallowing both. The status card rendered the model as
    "heypal" and left a stray `_` behind, which is a garbled diagnostic on the one card
    read when something is already wrong.

    Only values interpolated *into* a template need this. The templates' own `**` and `_`
    are deliberate.
    """
    for ch in ("\\", "_", "*", "`", "~", "|"):
        text = text.replace(ch, "\\" + ch)
    return text


def status_card(log, *, voice: str, save: str = "not configured",
                router: str = "", window_label: str = "last hour") -> Card:
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
    lines = [f"**Voice:** {plain(voice)}",
             # Worth its own line: a stale or unread save is invisible in the answers -
             # "nearest" silently falls back to ranking by cluster size and still returns
             # a confident-looking coordinate.
             f"**Save:** {plain(save)}",
             # The router's full name carries the cue width and the backstop floor, so
             # this line answers "is the change I just made actually running" - which a
             # long-lived process and a fast edit loop otherwise make unanswerable from
             # the one screen the player is looking at.
             *([f"**Router:** {plain(router)}"] if router else []),
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

    Answers are graded; declines are reported beside them and are not. See
    activity.TIMED_KINDS for why that split is a judgement about what the bar measures
    rather than a way of flattering it - and note that it only holds while declines stay
    *visible*, which is what the un-graded line is for. A decline drifting to eight
    seconds should be as obvious here as an answer doing the same.

    Absent until something has actually been timed: a status card that reports 0ms
    before the first query reads like a passing grade nobody earned.
    """
    from .activity import DECLINE_KINDS, GRADED_KINDS

    totals = {k: log.percentiles(k) for k in GRADED_KINDS}
    declines = {k: log.percentiles(k) for k in DECLINE_KINDS}
    if not any(totals.values()) and not any(declines.values()):
        return []

    out = ["", "__Latency__ _(answered queries)_"]
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

    shown = [(k, s) for k, s in declines.items() if s]
    if shown:
        out.append("- _Declines (not graded):_ " + ", ".join(
            f"{k.split('_')[0]} p50 **{s[1] / 1000:.1f}s**, p95 **{s[2] / 1000:.1f}s** "
            f"(n={s[0]})" for k, s in shown))

    stages = [(k, log.percentiles(k)) for k in ("stt", "route", "post")]
    stages = [(k, s) for k, s in stages if s]
    if stages:
        # Across every query, answered and declined both: this is the diagnostic for
        # where time goes, and excluding declines would hide the stage they are slow in.
        out.append("- where it goes (p50, all queries): "
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



# How many things to name when saying what we can answer. Four fitted comfortably; the
# eighteen that Phase 2 derived do not, and a card that answers "I didn't catch that"
# with a wall of nouns has stopped being an answer. The caller orders the list by how
# much data backs each, so the truncation keeps what a player is most likely to want.
MAX_NAMED_OPTIONS = 6


def decline_card(decline: Decline) -> Card:
    lines = ["I didn't catch that."]
    if decline.unrecognized:
        # Naming the unrecognised token lets the player retry precisely, and is the
        # visible half of never silently coercing a low-confidence match.
        lines.append(f"I couldn't match: **{decline.unrecognized}**")
    if decline.known_options:
        shown = decline.known_options[:MAX_NAMED_OPTIONS]
        rest = len(decline.known_options) - len(shown)
        opts = ", ".join(o.replace("_", " ") for o in shown)
        more = f" _and {rest} more_" if rest > 0 else ""
        lines.append(f"I can currently find: **{opts}**{more}")
    lines.append(f"_{decline.reason}_")
    return Card(title="Didn't understand", lines=lines, colour=TIER_DECLINE)
