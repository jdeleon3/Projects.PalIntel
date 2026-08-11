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
from pathlib import Path

from .execution import (DropsResult, ItemSourceResult, ResourceResult,
                        SpawnResult)
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
    # Artwork, attached after the card is built and never load-bearing. Both are None on
    # every path that cannot illustrate itself - assets missing, a coordinate on no
    # published map, a Pal with no icon - and the card is complete without them. Nothing
    # here is a value the player acts on; the numbers stay in `lines`.
    image: bytes | None = None          # a rendered map crop, JPEG
    thumbnail: Path | None = None       # a Pal icon on disk, PNG

    def to_text(self) -> str:
        out = [self.title, "-" * len(self.title), *self.lines]
        if self.footer:
            out += ["", self.footer]
        return "\n".join(out)

    def attachments(self, index: int = 0) -> dict[str, str]:
        """Filenames for whatever artwork this card carries.

        Named per card index because one message can hold several - a Paldeck slot with
        a variant renders two - and Discord matches `attachment://` by filename, so two
        cards sharing one would both show the first card's picture.
        """
        names = {}
        if self.image is not None:
            names["image"] = f"map{index}.jpg"
        if self.thumbnail is not None:
            names["thumbnail"] = f"icon{index}.png"
        return names

    def to_embed(self, index: int = 0) -> dict:
        names = self.attachments(index)
        return {
            "title": self.title,
            "description": "\n".join(self.lines),
            "color": self.colour,
            "footer": {"text": self.footer} if self.footer else None,
            "image": ({"url": f"attachment://{names['image']}"}
                      if "image" in names else None),
            "thumbnail": ({"url": f"attachment://{names['thumbnail']}"}
                          if "thumbnail" in names else None),
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
        # The drops line belongs here most of all. "No coal you can survive" plus "a
        # Blazamut drops 10" is a usable answer; the same card without it is a dead end,
        # and the alternative route matters precisely when the locations are unreachable.
        if result.resource in NOT_PLACED:
            return Card(title=f"{name} has no node locations",
                        lines=[NOT_PLACED[result.resource]] + _dropper_lines(result),
                        colour=TIER_DECLINE)
        return Card(
            title=f"No {name} found",
            lines=["Nothing matched in my data."
                   + (" Try without a level limit." if result.level_filtered else "")]
            + _dropper_lines(result),
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

    lines += _dropper_lines(result)

    footer = f"{result.total_available} {name.lower()} clusters known"
    if result.near is None:
        # Say what the ordering means rather than letting "1." imply nearest.
        footer += SEP + "sorted by size (no position known)"
    return Card(title=f"{name} locations", lines=lines, footer=footer, colour=TIER_FACT)


# How many droppers to name. Ore has 8 and red berries 8; past three the line stops being
# a line and the card is read at a glance mid-play. Same reasoning as MAX_NAMED_OPTIONS.
MAX_DROPPERS = 3


def _dropper_lines(result: ResourceResult) -> list[str]:
    """"Also drops from" - the other way to get the thing.

    It earns a place on a locations card because it is most useful exactly when the
    locations are not: the nearest coal may sit in a level 40 zone, and farming a
    Blazamut is a route a player can take at a level where walking there is not.

    Ordered by drop rate, and the amount is shown rather than the rate - "4-5" is what a
    player plans a trip around, whereas "100%" only says the drop is guaranteed and every
    published dropper is at 100% anyway, so printing it would add a column of the same
    number. An alpha-only dropper says so, because that is a different fight.
    """
    if not result.droppers:
        return []
    # One entry per Pal. The dataset keys droppers by (pal, level band) so a species
    # that drops the same thing at level 0 and level 70 appears twice - correct in the
    # data, and "Pierdon Cryst, Pierdon Cryst" on a card. Keeping the first keeps the
    # ordinary encounter, since the list is sorted by band ascending.
    seen: set[str] = set()
    unique = [d for d in result.droppers
              if not (d.pal in seen or seen.add(d.pal))]
    shown = unique[:MAX_DROPPERS]
    rest = len(unique) - len(shown)
    named = ", ".join(f"**{d.pal}** ({d.amount()}{', alpha' if d.alpha_only else ''})"
                      for d in shown)
    more = f" _+{rest} more_" if rest > 0 else ""
    return ["", f"Also drops from: {named}{more}"]


KIND_LABEL = {"alpha": "field alpha", "predator": "predator"}

# Vixy produces seven different things. Past three the line stops being a line, same
# reasoning as MAX_DROPPERS and MAX_NAMED_OPTIONS.
MAX_RANCH_ITEMS = 3


def _ranch_lines(result: SpawnResult) -> list[str]:
    """"Ranch:" - what this Pal makes if you assign it, marked as unofficial.

    Every other value on this card is extracted from the game's own files; these come
    from a community wiki, because the mapping is in blueprint bytecode and none of the
    284 data tables carries it (ADR-0014's amendment). That is a weaker claim than the
    coordinates above it, so it does not get to sit in the same voice - `(unofficial)`
    is the whole point of the line, not a hedge on it.

    The marker replaces a full source URL, which cost a line on every ranchable Pal's
    card to repeat the same address. Attribution still travels with the *data*:
    `provenance` and `source` are fields on ranch_drops.json, and
    Docs/03-data-ingestion.md section 7 asks for source attribution on Tier 3 cards,
    which this is not.

    An entry the pak's roster could not corroborate escalates the same parenthetical
    rather than adding a second one - there is exactly one (Mau Cryst), and it is a real
    answer with a real caveat.
    """
    ranch = result.ranch
    if ranch is None:
        return []

    shown = ranch.drops[:MAX_RANCH_ITEMS]
    rest = len(ranch.drops) - len(shown)
    items = ", ".join(f"**{d.label()}**" for d in shown)
    more = f" _+{rest} more_" if rest > 0 else ""
    mark = ("_(unofficial)_" if ranch.verified else
            "_(unofficial - the game files don't list this one as ranchable)_")
    return ["", f"Ranch: {items}{more} {mark}"]


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
                   f"point you at."] + _ranch_lines(result),
            colour=TIER_DECLINE,
        )

    if not result.areas:
        # Say which filter emptied it. "No Depresso spawns found" after "what about the
        # alpha?" reads as missing data; "Depresso has no field alpha" is the actual
        # answer, and it is one the player can act on.
        if result.kind and result.kind != "normal":
            what = KIND_LABEL.get(result.kind, result.kind)
            return Card(title=f"{result.pal} has no {what}",
                        lines=[f"**{result.pal}** is only found as an ordinary wild "
                               f"spawn - there's no {what} version of it."]
                              + _ranch_lines(result),
                        colour=TIER_DECLINE)
        return Card(
            title=f"No {result.pal} spawns found",
            lines=["Nothing matched in my data."] + _ranch_lines(result),
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
            # "<1%" rather than "0%". 1,432 of 19,272 areas sit under half a percent, and
            # rounding them to zero says the encounter never happens when the honest
            # statement is that it is rare - a false claim in the one field that exists
            # to stop the player camping a spot for nothing.
            share = (f"{a.encounter_share:.0%}" if a.encounter_share >= 0.005
                     else "<1%")
            bits.append(f"{share} of spawns here")
        if a.night_only:
            bits.append("night only")
        lines.append(SEP.join(bits))

    lines += _ranch_lines(result)

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
                router: str = "", artwork: str = "", window_label: str = "last hour") -> Card:
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
             # Enabled-in-config and actually-loaded are different states, and they look
             # identical from the channel: a card simply arrives without a picture. This
             # is the only place that distinction is visible.
             *([f"**Cards:** {plain(artwork)}"] if artwork else []),
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
    from .activity import ART_KINDS, DECLINE_KINDS, GRADED_KINDS

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

    art = [(k, log.percentiles(k)) for k in ART_KINDS]
    art = [(k, s) for k, s in art if s]
    if art:
        # Its own line, below the graded ones and labelled as after the answer, because
        # the whole claim of ADR-0017 is that this time is not the player's. Reported
        # separately from each other too: a slow render is a data problem and a slow
        # upload is a network one, and one figure could not tell them apart.
        out.append("- _artwork (after the answer):_ " + ", ".join(
            f"{k.split('_')[1]} p50 **{s[1]:.0f}ms**, p95 **{s[2]:.0f}ms** (n={s[0]})"
            for k, s in art))
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
    if decline.needs_restatement:
        # Not "I didn't catch that" - we caught it perfectly and simply have nothing for
        # it to refer to. Saying so asks for something specific and achievable, which a
        # generic apology does not, and it is ADR-0013's requirement that expired context
        # be named rather than silently ignored.
        return Card(
            title="What was that about?",
            # ASCII only: the CLI renderer runs on a cp1252 console, where an em-dash
            # arrives as a replacement character.
            lines=["I've forgotten what we were talking about - say the name again.",
                   f"_{decline.reason}_"],
            colour=TIER_DECLINE,
        )

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


# A Pal drops 5 items at the median and 14 at the most. Six keeps the common case whole
# while stopping Dandilord from filling the screen.
MAX_DROPS = 6


def drops_card(result: DropsResult) -> Card:
    """What a Pal yields, with the alpha-only half kept separate.

    The split is the whole point. Vanwyrm's list is mostly alpha drops, and a flat list
    would read as "kill Vanwyrms for Ancient Civilization Parts" - true only of a level
    50 field boss, and an expensive thing to learn by trying.
    """
    if not result.known:
        return Card(title=f"No drop data for {result.pal}",
                    lines=["That Pal isn't in the drop table."],
                    colour=TIER_DECLINE)
    if not result.total:
        # A real answer: the game gives this one nothing.
        return Card(title=f"{result.pal} drops nothing",
                    lines=[f"**{result.pal}** yields no items when defeated or caught."],
                    colour=TIER_DECLINE)

    def block(drops: list) -> list[str]:
        shown = drops[:MAX_DROPS]
        rest = len(drops) - len(shown)
        out = [SEP.join([f"**{d.item}**", d.amount()]
                        + ([f"{d.rate:.0f}%"] if d.rate < 100 else []))
               for d in shown]
        if rest > 0:
            out.append(f"_+{rest} more_")
        return out

    lines = block(result.ordinary) if result.ordinary else [
        "_Nothing from an ordinary one._"]
    if result.alpha_only:
        # Named as a different fight, not as a footnote. This is the line that stops the
        # card promising a drop the player cannot get from the Pal they will meet.
        lines += ["", "__Alpha only__"] + block(result.alpha_only)
    if result.high_level:
        # Same reasoning, one band up. The endgame table is a different creature: a
        # level 80 Chillet drops 30-50 Ancient Relics, an ordinary one drops leather.
        band = min(d.min_level for d in result.high_level)
        lines += ["", f"__Level {band}+ only__"] + block(result.high_level)

    return Card(title=f"{result.pal} drops", lines=lines,
                footer=f"{result.total} item{'s' if result.total != 1 else ''}",
                colour=TIER_FACT)


def item_source_card(result: ItemSourceResult) -> Card:
    """Which Pals drop a named item, ordinary encounters first.

    78 Pals drop Leather and one drops a Cloth Outfit Schematic. The cap matters more
    here than on any other card, and so does the ordering: the reader wants the easiest
    source, not an exhaustive index.
    """
    if not result.known:
        return Card(title=f"No drop data for {result.item}",
                    lines=["Nothing in my data drops that."], colour=TIER_DECLINE)
    if not result.total:
        return Card(title=f"Nothing drops {result.item}",
                    lines=[f"No Pal yields **{result.item}**."], colour=TIER_DECLINE)

    def block(rows: list) -> list[str]:
        shown = rows[:MAX_DROPPERS]
        rest = len(rows) - len(shown)
        out = [SEP.join([f"**{d.pal}**", d.amount()]
                        + ([f"{d.rate:.0f}%"] if d.rate < 100 else []))
               for d in shown]
        if rest > 0:
            out.append(f"_+{rest} more_")
        return out

    lines = block(result.ordinary) if result.ordinary else [
        "_No ordinary encounter drops this._"]
    if result.alpha_only and not result.ordinary:
        # Only worth the space when there is no easier source. A player who can already
        # farm this from a wild Pal does not need the boss list.
        lines += ["", "__Alpha only__"] + block(result.alpha_only)
    if result.high_level and not result.ordinary and not result.alpha_only:
        band = min(d.min_level for d in result.high_level)
        lines += ["", f"__Level {band}+ only__"] + block(result.high_level)

    return Card(title=f"{result.item} comes from", lines=lines,
                footer=f"{result.total} source{'s' if result.total != 1 else ''}",
                colour=TIER_FACT)


# Display names for the pak's element enum. `Electricity` and `Leaf` are the game's
# internal spellings; the player sees Electric and Grass in game, and a card that says
# "Leaf" is describing a table rather than answering a question.
ELEMENT_DISPLAY = {"Electricity": "Electric", "Leaf": "Grass", "Earth": "Ground",
                   "Normal": "Neutral"}


def _element(name: str) -> str:
    return ELEMENT_DISPLAY.get(name, name)


def _elements(names) -> str:
    return "/".join(_element(n) for n in names)


def counter_card(result: "CounterResult") -> Card:
    """The plan for fighting one boss. **The project's first Tier 2 card.**

    Amber, not green, and the colour is the honest part: every other card in this file
    reports facts extracted from the game, while this one reports a *computation over*
    them. [ADR-0010](../Docs/adr/0010-three-tier-answer-model.md) separates the two, and
    a player who cannot tell which they are looking at will trust both equally.

    Two things are said out loud rather than assumed away:

    * **The boss's name is inferred.** No table names a `GYM_`/`RAID_`/`BOSS_` row - it
      comes from stripping the prefix and joining to the base tribe. Where that happened
      the footer says so, because the alternative is a card asserting a name the game
      never states.
    * **Owning nothing effective is an answer.** It names the element that would work,
      which costs nothing to compute and is the difference between "no" and "not yet".
    """
    name = result.boss_name or result.boss_id
    kind = {"tower": "tower boss", "raid": "raid boss", "alpha": "field alpha"}.get(
        result.kind, result.kind)

    head = f"**{name}** is {_elements(result.boss_elements)}"
    if result.level is not None:
        head += f", level {result.level}"
    lines = [f"{head} ({kind})."]

    if not result.candidates:
        # Not a decline: the question was understood and answered, and the answer is
        # that the roster is missing something. Saying which something is the point.
        lines.append("")
        lines.append(f"**Nothing you own is strong against it.** "
                     f"{_elements(result.counter_elements)} is what beats it - "
                     f"catching one is the shortest route.")
        return Card(title=f"How to fight {name}", lines=lines, colour=TIER_ADVICE,
                    footer=f"checked {result.owned_considered} of your Pals")

    lines.append("")
    for m in result.candidates:
        bits = [f"**{m.name or m.character_id}**", _elements(m.elements),
                f"deals {m.offense:g}x"]
        # Only worth a word when it is not 1x. "takes 1x" on every line is noise that
        # makes the lines that matter harder to see.
        if m.defense != 1.0:
            bits.append("takes " + ("half" if m.defense < 1 else "double"))
        lines.append(SEP.join(bits))

    footer = (f"{len(result.candidates)} shown"
              f"{SEP}checked {result.owned_considered} of your Pals")
    # Typing is the ONLY thing scored, so candidates routinely tie exactly - against a
    # single-element boss every counter is 2x/0.5x and the order is then alphabetical.
    # Presenting that as a ranking would be the card asserting something the data does
    # not say. Levels and stats would break the tie; calibrating that is a roadmap item
    # and has not happened.
    if len({(m.offense, m.defense) for m in result.candidates}) == 1 \
            and len(result.candidates) > 1:
        footer += f"{SEP}equally matched on type - order is arbitrary"
    if result.name_derived:
        footer += f"{SEP}name inferred from the character id"
    return Card(title=f"How to fight {name}", lines=lines, colour=TIER_ADVICE,
                footer=footer)
