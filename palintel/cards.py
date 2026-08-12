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

from .execution import (AttributeResult, BaseSiteResult, DropsResult,
                        ItemSourceResult, PalInfoResult, ResourceResult, SpawnResult)
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
                roster: str = "", router: str = "", artwork: str = "",
                spend: str = "", window_label: str = "last hour") -> Card:
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
             # Its own line rather than folded into Save, because it fails separately and
             # silently: a counter card that says "I haven't read your Pals" looks like a
             # deliberate caveat rather than a roster read that never happened, which is
             # exactly how it went unnoticed through a whole play session.
             *([f"**Roster:** {plain(roster)}"] if roster else []),
             # The router's full name carries the cue width and the backstop floor, so
             # this line answers "is the change I just made actually running" - which a
             # long-lived process and a fast edit loop otherwise make unanswerable from
             # the one screen the player is looking at.
             *([f"**Router:** {plain(router)}"] if router else []),
             # Enabled-in-config and actually-loaded are different states, and they look
             # identical from the channel: a card simply arrives without a picture. This
             # is the only place that distinction is visible.
             *([f"**Cards:** {plain(artwork)}"] if artwork else []),
             # A prepaid balance running out is SILENT: every 429 arrives as a Decline,
             # and the roadmap records one run reading that as a 13-point router
             # regression. This line is what turns it into a number.
             *([f"**Spend:** {spend}"] if spend else []),
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


def pal_info_card(result: PalInfoResult, job_label=lambda j: j) -> Card:
    """What we know about one Pal, on one card. **Tier 1 — gathered, not computed.**

    Deliberately a summary and deliberately not exhaustive: it exists because "tell me
    about X" was the most-asked shape in the first play session and every one of them got
    a card built for a different question. Each line here points at a card that answers
    that part properly, so this is an index rather than a replacement.

    It says what it does NOT have, too. A Pal with no wild spawn, no ranch output and no
    drops is a real answer - the Terraria collab Pals are exactly that - and a card that
    just omitted the missing lines would read as though nobody had looked.
    """
    if not result.known:
        return Card(title=f"I don't have anything on {result.pal}",
                    lines=[f"**{result.pal}** is a name I know, and it isn't in any of "
                           f"my datasets - no spawns, no drops, no stats."],
                    colour=TIER_DECLINE)

    lines = []
    head = f"**{result.pal}**"
    if result.elements:
        head += f" is {_elements(result.elements)}"
    if result.bands:
        # The bands the location card would print, in the same words, so two cards about
        # one Pal cannot appear to disagree about its level.
        span = ", ".join(f"{lo}-{hi}" if lo != hi else str(lo)
                         for lo, hi in result.bands[:3])
        kind = "" if result.level_kind == "normal" else f" ({result.level_kind})"
        head += f", found at level {span}{kind}"
    lines.append(head + ".")

    if not result.in_overworld:
        # Same sentence the spawn card leads with, for the same reason: "keep looking" is
        # wrong advice for a tower boss.
        lines.append("_No wild spawn - it comes from a tower, a raid, a dungeon or "
                     "breeding._")

    if result.work:
        lines.append("")
        lines.append("Works: " + SEP.join(f"**{job_label(j)}** {v}"
                                          for j, v in result.work))

    lines.append(_mount_line(result))

    lines += _ranch_summary(result)

    if result.drops:
        lines.append(f"Drops **{result.drops}** different item"
                     f"{'s' if result.drops != 1 else ''}.")

    # Point at the cards that answer each part properly. This card is an index.
    lines += ["", f"_Ask \"where can I find {result.pal}\" or \"what does "
                  f"{result.pal} drop\" for the detail._"]
    return Card(title=result.pal, lines=lines, colour=TIER_FACT)


def _mount_line(result: PalInfoResult) -> str:
    """Rideability, **always present, including when the answer is no.**

    This line is unconditional and that is the whole design. *"Can I ride X"* routes
    here, and an info card that simply omitted the line when a Pal has no saddle would
    make silence carry the answer - which reads as "nobody looked", not as "no". Stating
    it costs one line on every card and removes the ambiguity from all of them.

    Preferred over a special rendering mode for the same reason. A card that reshapes
    itself depending on which question was asked has two behaviours to learn and two to
    keep right; a card that always says everything has one.

    Which medium is named the way `build_mounts.py` names it: land covers flying and
    ground because the pak gives them one speed column, and water is separate because
    `SwimDashSpeed` is separate.
    """
    m = result.mount
    if m is None:
        return "Not rideable - it has no saddle."
    at = (f", saddle at player level {m.unlock_level}"
          if m.unlock_level is not None
          else ", though nothing in the game files unlocks its saddle")
    where = "faster in water" if m.fastest_medium == "water" else "a land mount"
    return f"Rideable{at} - {where}."


def _ranch_summary(result: PalInfoResult) -> list[str]:
    """The ranch line, carrying the same `(unofficial)` marker the spawn card uses.

    Not a shortcut: those facts are community-sourced where everything else on this card
    is extracted (ADR-0014's amendment), and the marker is the whole point of the line
    rather than a hedge on it.
    """
    if result.ranch is None:
        return []
    shown = result.ranch.drops[:MAX_RANCH_ITEMS]
    rest = len(result.ranch.drops) - len(shown)
    items = ", ".join(f"**{d.label()}**" for d in shown)
    return [f"Ranch: {items}{f' _+{rest} more_' if rest > 0 else ''} _(unofficial)_"]


# How many matches to list. Five fits the glance a card gets mid-play, and the filters
# routinely leave 40+ - "44 Fire Pals" is an index, not an answer. Same reasoning as
# MAX_DROPPERS and MAX_NAMED_OPTIONS, and the footer always says how many were behind it.
MAX_MATCHES = 5


def attribute_card(result: AttributeResult) -> Card:
    """Which Pals match a description. **Tier 1, and green, despite reading like advice.**

    The colour is the honest call and it is worth stating why, because the counter card
    next door is amber for what looks like the same shape. That one *computes* a
    recommendation: it reasons about type effectiveness and produces something the game
    never said. This one selects rows and orders them by an integer the game states -
    Mining 6 is a column, not a conclusion - so it is the same kind of claim as a
    coordinate.

    What the card must therefore be careful about is the ORDER, not the values. Two
    caveats earn their space:

    * **Highest level first is not a ranking.** STATUS's decision, with its own caveat
      attached: highest is a proxy for strongest and nothing more. The footer says so
      rather than letting "1." imply the card knows which Pal is better.
    * **A widened level filter is a different answer.** When nothing spawns at exactly
      the level asked for, the nearest bands come back - and the card leads with the
      fact, because "the closest thing to an electric Pal at 60" and "an electric Pal at
      60" are not the same claim.
    """
    if result.mounts_only:
        noun = {"land": "land mounts", "water": "water mounts"}.get(
            result.medium, "mounts")
        title = f"{_element(result.element)} {noun}" if result.element else noun.title()
        if result.unowned_only:
            title = f"{noun.title()} you don't have yet"
        elif result.player_level is not None:
            # "player level", spelled out. Every other card in this file prints a Pal's
            # level, and one word meaning two things across two cards is the ambiguity
            # STATUS's decision exists to contain.
            title += f" at player level {result.player_level}"
    else:
        title = f"{_element(result.element)} Pals" if result.element else "Pals"
    if result.work:
        title += f" for {result.work_label or result.work}"
    if result.level is not None:
        # "at", not "around", even when the filter widened. The leading line below owns
        # that caveat, and a title that hedges makes every card read as approximate.
        title += f" at level {result.level}"

    if result.unowned_only and not result.roster_known:
        # **Not a list of every mount.** "Which ones don't I have" against an unread
        # roster would return all 108 and read as "you own none of these" - a confident
        # claim about a set nobody looked at, which is the same failure the counter card
        # separates `roster_known` to avoid. Saying so is the answer.
        return Card(
            title="I haven't read your Pals",
            lines=["I can't tell you which mounts you're missing without looking at "
                   f"your save. I know of **{result.total_available}** rideable Pals in "
                   "total.",
                   "", "_Point me at a save directory and ask again._"],
            colour=TIER_DECLINE)

    if not result.matches:
        # Nothing matched every filter. Say which combination came up empty rather than
        # a bare "no results", because dropping one of two filters is usually the fix and
        # the player cannot guess which one was the problem.
        return Card(title=f"No {title.lower()}",
                    lines=["Nothing in my data matches all of that.",
                           _filters_line(result)],
                    colour=TIER_DECLINE)

    lines = []
    if result.level is not None and not result.level_exact:
        # Leading, not a footnote. The player asked for 60 and is being shown 57.
        lines.append(f"_Nothing spawns at exactly level {result.level} - "
                     f"these are the closest._")

    for i, m in enumerate(result.matches, 1):
        bits = [f"**{i}. {m.pal}**", _elements(m.elements)]
        if m.work_level is not None:
            # The job that was asked about, and only that one. A Pal's best job is a
            # different fact and printing it here answers a question nobody asked.
            bits.append(f"{result.work_label or result.work} {m.work_level}")
        if m.mount is not None:
            bits.append(_speed_label(m, result))
            if m.mount.unlock_level is not None:
                bits.append(f"saddle at lvl {m.mount.unlock_level}")
            else:
                # Never blank. A mount with no known unlock is a different state from one
                # you can build now, and the list is sorted by speed so it can sit at the
                # top of a card the player is about to act on.
                bits.append("_unlock unknown_")
        else:
            bits.append(m.band_label())
        lines.append(SEP.join(bits))

    if result.without_a_band:
        # Not "no match" - not considered. A tower boss has no wild level, so a level
        # filter cannot rule it in or out, and silently dropping it would let the card
        # imply a completeness it does not have.
        lines += ["", f"_{result.without_a_band} more match but have no wild spawn, "
                      f"so I can't check their level._"]

    if result.unlock_unknown:
        # Not "too high a level" - unknown. Two saddles have no technology row at all, so
        # nothing in the game files says how you get them, and a level filter cannot rule
        # them in or out.
        lines += ["", f"_{result.unlock_unknown} more can be ridden but no technology "
                      f"unlocks their saddle, so I can't tell what level you need._"]

    shown = len(result.matches)
    footer = f"{shown} of {result.total_available} shown"
    if result.mounts_only:
        footer += f"{SEP}{_medium_note(result)}"
    elif result.work:
        # The number is the game's own suitability column, not a rating this project
        # invented - and not a claim about the Pal being good, which nothing here
        # measures.
        footer += f"{SEP}{result.work_label or result.work} level is the game's own"
    elif result.level is None:
        footer += f"{SEP}sorted by highest level, which is not a ranking"
    return Card(title=title, lines=lines, footer=footer, colour=TIER_FACT)


def _speed_label(m, result: AttributeResult) -> str:
    """A mount's speed, always saying which medium it is in.

    The medium is never dropped, even when it was asked for. With no medium named the
    card ranks by whichever of a Pal's two speeds is higher, so Faleris Aqua leads on
    2520 - a *swim* speed - and printing the bare number would read as how fast it runs.
    """
    if m.speed is None:
        return "no speed data"
    where = "in water" if m.speed_medium == "water" else "on land"
    return f"{m.speed} {where}"


def _medium_note(result: AttributeResult) -> str:
    """Say what "land" covers, because it covers more than the word does.

    **Flying and ground mounts are one category, and that is the game's doing.** The pak
    has no flight flag - seven signals were measured and falsified - but more decisively
    it has no flight *speed*: a flyer's ridden speed is `RideSprintSpeed`, the same
    column a ground mount uses. So separating them would not produce two rankings, it
    would produce the same ranking twice with an invented label on each. The card says
    flyers are in the list rather than letting "land" quietly exclude them.
    """
    if result.medium == "water":
        return "swim speed"
    covers = "flying and ground mounts share one speed in the game files"
    if result.medium == "land":
        return covers
    return f"fastest of land and water{SEP}{covers}"


def _filters_line(result: AttributeResult) -> str:
    """What was actually asked for, so an empty card is diagnosable."""
    bits = []
    if result.element:
        bits.append(f"element **{_element(result.element)}**")
    if result.work:
        bits.append(f"job **{result.work_label or result.work}**")
    if result.level is not None:
        bits.append(f"level **{result.level}**")
    return "_Looked for: " + ", ".join(bits) + "._" if bits else ""


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
    * **A tower is named the way the player names it.** Asked about Victor, the card
      titles itself "Victor & Shadowbeak" - which is the game's own name for the fight,
      not a rendering this file invented - and says "Victor's tower", so the reader can
      tell it apart from the field alpha of the same species. See `_leader_note` for why
      that pairing is NOT footnoted as derived.
    """
    name = result.boss_name or result.boss_id
    # The player asked about a human; answering about a Pal alone would read as a
    # non-sequitur even though it is the same fight.
    title_name = f"{result.leader} & {name}" if result.leader else name
    kind = {"tower": "tower boss", "raid": "raid boss", "alpha": "field alpha"}.get(
        result.kind, result.kind)
    if result.leader:
        # "Victor's tower" rather than "tower boss": there are nine towers and the
        # generic label cannot tell the player which one this is.
        kind = f"{result.leader}'s tower"

    head = f"**{name}** is {_elements(result.boss_elements)}"
    if result.level is not None:
        head += f", level {result.level}"
    lines = [f"{head} ({kind})."]

    if not result.roster_known:
        # NOT the same card as "you own nothing that works", and the difference is the
        # whole point. Reading the roster costs a full Level.sav parse, so it is often
        # absent - and asserting a fact about a set nobody looked at is precisely the
        # confidently-wrong answer this project refuses. The typing half is still a fact
        # about the boss and is worth saying on its own.
        lines.append("")
        lines.append(f"**{_elements(result.counter_elements)}** is what beats it.")
        return Card(title=f"How to fight {title_name}", lines=lines, colour=TIER_ADVICE,
                    footer="I haven't read your Pals, so this isn't filtered to what "
                           "you own" + _leader_note(result))

    if not result.candidates:
        # Not a decline: the question was understood and answered, and the answer is
        # that the roster is missing something. Saying which something is the point.
        lines.append("")
        lines.append(f"**Nothing you own is strong against it.** "
                     f"{_elements(result.counter_elements)} is what beats it - "
                     f"catching one is the shortest route.")
        return Card(title=f"How to fight {title_name}", lines=lines, colour=TIER_ADVICE,
                    footer=f"checked {result.owned_considered} of your Pals"
                           + _leader_note(result))

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
    footer += _leader_note(result)
    return Card(title=f"How to fight {title_name}", lines=lines, colour=TIER_ADVICE,
                footer=footer)


def _leader_note(result: "CounterResult") -> str:
    """Caveat the human-to-Pal pairing only where it has a single source.

    **This footnote used to fire on every leader and it was wrong to.** The pairing is
    not inferred: `pal_names_flat.json` states it in one string - `PAL_NAME_SnowBoss` is
    "Victor & Shadowbeak" - and `DT_UniqueNPCText` reaches the same answer independently,
    with the build failing if they disagree. Caveating a double-sourced fact on every
    card would spend the reader's attention on the wrong thing and make the caveat mean
    nothing when it matters.

    The one derived step that remains - reaching `GYM_BlackGriffon` from the name
    "Shadowbeak" - is the prefix inference the `name_derived` footnote already names.
    """
    if not result.leader or result.leader_corroborated:
        return ""
    return (f"{SEP}the {result.leader}/{result.boss_name or result.boss_id} pairing has "
            f"only one source in the game files")


# What each blocker is called on a card, and the order the summary lists them in - the
# gate the player can do least about first, which is the same order `progression._blocker`
# resolves them in.
_BLOCKER_LABELS = (
    ("level", ("needs", "need", "a higher level")),
    ("prerequisite", ("needs", "need", "an earlier technology")),
    ("tower", ("needs", "need", "a tower boss beaten")),
    ("points", ("costs", "cost", "more points than you have")),
)
# The per-line form, which is always singular - it labels one technology.
_BLOCKER_LINE = {key: f"{singular} {tail}"
                 for key, (singular, _, tail) in _BLOCKER_LABELS}

_CURRENCY_WORD = {"ancient": "ancient pt", "technology": "pt"}


def progression_card(result: "ProgressionResult") -> Card:
    """What to research next. **Tier 2, amber, and the colour is the honest part.**

    Every value on it is stated by the game - a required level, a cost, a currency, a
    prerequisite - so it would be tempting to call this Tier 1 the way the attribute card
    is. It is not, and the difference is the same one that makes the counter card amber:
    the *selection and the order* are a recommendation. "What should I unlock next" asks
    for a judgement, and the judgement being computed rather than generated does not make
    it a fact.

    Three things are said out loud rather than assumed away:

    * **The level may be inferred, and only downward.** With no player level readable
      from the save, the filter uses the highest `required_level` among the technologies
      already unlocked. That is a floor, not a level: it can hide something available and
      can never offer something that is not. The card says which one it used.
    * **The two point pools are never summed.** Each line names its own currency, because
      an ancient technology is bought with a different number and a card that added them
      would say you can afford something you cannot.
    * **Order is a proxy.** Most advanced first, which is not "best" - the same caveat the
      attribute card carries, for the same reason: the data supports "unlocks later" and
      nothing else.
    """
    goal = result.goal
    title = "What to research next"
    if result.currency == "ancient":
        # The pool has to be in the title, because every line below prints a cost and
        # only the currency word tells the reader which of their two balances it comes
        # out of. A list filtered to one pool that does not say so is the same silently
        # dropped filter the mount work found.
        title = "What to spend ancient points on"
    if goal:
        title += f" - {goal}"

    if not result.save_known:
        # NOT the same card as "you have researched everything". Reading the save is what
        # makes this class answerable at all, and asserting a next step against a set
        # nobody looked at is the confidently-wrong answer this project refuses.
        return Card(
            title="I haven't read your save",
            lines=["I can't tell you what to research next without seeing which "
                   "technologies you already have.",
                   "", "_Point me at a save directory and ask again._"],
            colour=TIER_DECLINE)

    if not result.candidates:
        # Two very different empties, and the player acts differently on each.
        if result.total_locked:
            return Card(
                title=title,
                lines=[f"**Nothing is researchable right now.** "
                       f"{result.total_locked} technolog"
                       f"{'y is' if result.total_locked == 1 else 'ies are'} still "
                       f"locked{f' under {goal}' if goal else ''}, and "
                       f"{_blocked_phrase(result)}."],
                colour=TIER_ADVICE, footer=_progression_footer(result))
        return Card(
            title=title,
            lines=[f"**You have researched everything"
                   f"{f' under {goal}' if goal else ''}.**"],
            colour=TIER_ADVICE, footer=_progression_footer(result))

    lines = []
    for i, c in enumerate(result.candidates, 1):
        t = c.tech
        bits = [f"**{i}. {t.name}**",
                f"lvl {t.required_level}",
                f"{t.cost} {_CURRENCY_WORD[t.currency]}"]
        if not c.researchable:
            # Every line says whether it is actually available. A mixed list without this
            # reads as five things to go and do, and some of them cannot be done.
            bits.append(f"_{_BLOCKER_LINE[c.blocked_by.value]}_")
        if t.requires_research:
            # Lab research is a separate system this project cannot read from the save,
            # so it is neither filtered on nor hidden. Naming it is the only honest move.
            bits.append(f"_also needs lab research {t.requires_research}_")
        lines.append(SEP.join(bits))
        if t.unlocks and t.unlocks != (t.name,):
            # What you actually get, when the technology's name does not already say it.
            lines.append(f"    {', '.join(t.unlocks[:3])}")

    # The count that is actually actionable, first. "Of 471 locked, 134 need a higher
    # level" leaves the reader to subtract, and the number they wanted was how many they
    # could go and buy right now.
    ready = result.total_locked - sum(result.blocked.values())
    tail = _blocked_phrase(result)
    lines += ["", f"_{ready} of {result.total_locked} still locked "
                  f"{'are' if ready != 1 else 'is'} researchable now"
                  + (f"; {tail}" if tail else "") + "._"]

    return Card(title=title, lines=lines, colour=TIER_ADVICE,
                footer=_progression_footer(result))


def corpus_card(result: "LookupResult") -> Card:
    """The game's own explanation, quoted. **Tier 3, blue, and every line has a source.**

    Blue rather than green or amber because this is reference: not a fact computed from a
    table and not advice computed from one, but a passage from a document. The reader
    should be able to tell at a glance that they are looking at something the game says
    rather than something this project worked out.

    **Nothing here is generated.** ADR-0011 describes grounded *synthesis* with a
    citation; this quotes the chunk verbatim instead, which is a smaller promise and a
    much easier one to keep - there is no model in the path, so a summary cannot drift
    from the text it summarises. See palintel/corpus.py for why that trade was taken.

    A decline is a first-class outcome and not a failure: ADR-0011 makes "not in my
    sources" mandatory when nothing clears the relevance bar, and the corpus is the game's
    own text, so a great many reasonable Palworld questions are genuinely not in it.
    """
    if not result.grounded:
        return Card(
            title="Not in my sources",
            lines=["I only quote what the game itself says, and nothing in there "
                   "answers that.",
                   "",
                   f"_Closest match scored {result.best_score:.2f} against a "
                   f"{result.floor:.2f} bar, over {result.chunks_searched} passages._"],
            colour=TIER_DECLINE)

    top = result.passages[0]
    lines = [top.chunk.text, "", f"— *{top.chunk.citation}*"]
    for extra in result.passages[1:]:
        # A second passage is shown compressed. Two full quotes is a wall, and the second
        # is a "you may also want" rather than part of the answer.
        first = extra.chunk.text.split("\n")[0]
        lines += ["", f"**Also:** {first}", f"— *{extra.chunk.citation}*"]
    return Card(title=top.chunk.title, lines=lines, colour=TIER_REFERENCE,
                footer=f"quoted from the game's own text{SEP}"
                       f"match {top.score:.2f}{SEP}nothing here was rewritten")


def _resource_word(resource: str) -> str:
    return resource.replace("_", " ")


# How a criterion reads. Unknown is its own mark and never a cross: "I could not check"
# and "no" are different answers, and a rating that shows four crosses when two of them
# were unmeasurable is overstating what it knows.
_MARK = {True: "✅", False: "❌", None: "❔"}


def base_criteria_card(result: "BaseCriteria") -> Card:
    """What this system checks about a base site. **Amber, and the colour is the point.**

    The risk on this card is a reader taking it as *"Palworld says these four things
    matter"*. It does not — the game states none of them, and the four levers are the
    community's framing. Amber says this is the computation talking, which is exactly
    what it is: the rating card's own rule, written out.

    **Every bar names where it came from**, because a criterion with an unsourced
    threshold is the `min_player_level` failure wearing a new coat — a number that looks
    calibrated, is not, and gets believed. And the gaps are on the card rather than in a
    docstring, because a list of four criteria reads as a complete account of the problem
    and this one is not.
    """
    lines = []
    for name, bar, source in result.checks:
        lines.append(f"**{name}** — {bar}")
        lines.append(f"    _{source}_")

    lines += ["", "**What I can't check:**"]
    for name, why in result.gaps:
        lines.append(f"- **{name}** — {why}")

    lines += ["", "_Ask \"where should I put a base for coal\" to search, or "
                  "\"rate this spot\" to judge where you're standing._"]
    return Card(
        title="What I look for in a base site",
        lines=lines, colour=TIER_ADVICE,
        footer=f"my criteria, not the game's{SEP}"
               f"every bar is calibrated against the {result.marked_areas} areas the "
               f"game marks itself, or against the whole map")


def base_rating_card(result: "BaseRating") -> Card:
    """How good one place is. **Tier 2, amber, and deliberately not a score out of ten.**

    "How good is this base location" asks for a judgement, and this project does not ship
    uncalibrated judgements — the `min_player_level` rule has been one since Phase 1 and
    STATUS still lists it as unpaid. So there is no 1-10, no letter grade, and no weighted
    total. What there is:

    * **Four criteria, each pass / fail / unknown** against a bar taken from the game.
    * **A count**, "3 of 4", which claims that four things were checked and three held —
      and claims nothing about their relative worth.
    * **A percentile** beside each, so the reader sees the margin rather than a bare
      pass. Flatness and water are ranked against the 32 areas the game marks itself;
      resources against every node cluster on the map, because the marked areas hold a
      median of three deposits and were plainly not chosen for them.

    A weighted score would be a better-looking card and a worse claim. It would assert
    that flat ground is worth some fraction of a base site, which nobody has measured.
    """
    if not result.on_the_map:
        # **Not a 0 of 4.** Outside the extent of everything extracted, every criterion
        # fails for the same uninteresting reason, and a card reading "0 of 4" is a
        # judgement about a bad base site rather than the truth - which is that the
        # coordinate is not somewhere this can speak about at all. The two look identical
        # to a reader and only one of them is worth acting on.
        return Card(
            title="That's off my map",
            lines=[f"**({result.map_x:.0f}, {result.map_y:.0f})** is outside everything "
                   f"I have data for, so anything I said about it would be a guess "
                   f"dressed as a score.",
                   "",
                   "_Check the coordinate — the in-game map runs roughly "
                   "−1990 to 940 across and −2010 to 1640 down._"],
            colour=TIER_DECLINE)

    title = result.label
    if result.resources:
        # What the base is FOR belongs in the title: the same coordinate scores
        # differently for quartz than in general, and two cards that differ only in a
        # criterion line would be indistinguishable in scrollback.
        title += " for " + " + ".join(_resource_word(r) for r in result.resources)
    title += f" — {result.score} of {result.checkable}"
    if result.checkable < len(result.criteria):
        title += f" ({len(result.criteria) - result.checkable} unknown)"

    lines = [f"**({result.map_x:.0f}, {result.map_y:.0f})**", ""]
    for c in result.criteria:
        bits = [f"{_MARK[c.met]} **{c.name}**", c.detail]
        if c.percentile is not None:
            # The margin, not just the verdict. "Flat, and flatter than 80% of the
            # places the game marks" is a different answer from a bare tick.
            bits.append(f"_better than {c.percentile}%_")
        lines.append(SEP.join(bits))

    if result.covered:
        # **Everything in range, not just what was asked about.** A spot chosen for
        # quartz that also sits on 30 stone and a river is information the player wants
        # and did not think to ask for - and it is the difference between a verdict and
        # something they can act on. Ordered by count, with the asked-for resource
        # marked so it is findable in a long line.
        listed = sorted(result.covered.items(), key=lambda kv: (-kv[1], kv[0]))
        lines += ["", "**In range:** " + SEP.join(
            (f"**{n} {_resource_word(r)}**" if r in result.resources
             else f"{n} {_resource_word(r)}")
            for r, n in listed)]

    if result.wild_levels:
        low, high = result.wild_levels
        # A fact, not a danger label. The danger RULE is the uncalibrated one; the levels
        # are extracted from the same spawn dataset every location card reads.
        lines.append(f"Wild Pals nearby: **level {low}-{high}**")

    footer = (f"within {result.radius:.1f} map units"
              f"{SEP}a count of criteria met, not a score - nothing here weighs them"
              f"{SEP}I still can't see no-build zones")
    return Card(title=title, lines=lines, colour=TIER_ADVICE, footer=footer)


def base_site_card(result: BaseSiteResult) -> Card:
    """Where to put a base. **Tier 2, amber, and the caveat is load-bearing.**

    Every number here is stated by the game - a coordinate from the node dataset, a
    deposit count, a radius read out of `BaseCampAreaRange`. What makes it advice rather
    than fact is the word "should": the card selects a coordinate and calls it a good
    place to build.

    **And it must never let that read as "you can build here."** Nothing found in the pak
    says whether ground is flat, whether a spot is underwater, or whether it sits in a
    no-build zone. A card that omitted this would produce the project's signature failure
    in a new place: a well-formed, in-bounds, correctly transformed coordinate pointing at
    a cliff face. The footer says it every time, not only when the site looks suspect,
    because the card cannot tell which ones look suspect.
    """
    named = " + ".join(_resource_word(r) for r in result.resources)
    title = f"Base sites for {named}"

    if not result.sites:
        return Card(
            title=f"No base site for {named}",
            lines=["I don't have node clusters for that."],
            colour=TIER_DECLINE)

    lines = []
    if result.complete_sites == 0 and len(result.resources) > 1:
        # Leading, not a footnote. "Nothing reaches both" is the answer to the question
        # actually asked, and burying it under three partial sites reads as though one of
        # them covered everything.
        lines.append(f"_No single base reaches all of {named}. "
                     f"These cover the most of it._")

    for i, s in enumerate(result.sites, 1):
        bits = [f"**{i}. ({s.map_x:.0f}, {s.map_y:.0f})**"]
        bits += [f"{n} {_resource_word(r)}"
                 for r, n in sorted(s.covered.items(), key=lambda kv: (-kv[1], kv[0]))]
        if s.distance is not None:
            bits.append(f"{s.distance:.0f} units away")
        lines.append(SEP.join(bits))

        # The terrain line, and it leads with the strongest signal there is. "The game
        # marks this as a base camp area" is the designers' own judgement, not ours.
        terrain = []
        if s.in_marked_area(result.radius):
            terrain.append("**the game marks this as a base camp area**")
        if s.flat is True:
            terrain.append(f"flat ground (±{s.roughness / 100:.1f}m)")
        elif s.flat is False:
            terrain.append(f"_uneven ground (±{s.roughness / 100:.1f}m)_")
        elif result.features_known:
            # Never blank and never "flat". Too few placed actors to measure is a real
            # state, and it is as likely to be a cliff as a meadow.
            terrain.append("_ground unknown - nothing placed near enough to measure_")
        if s.water is not None:
            near_water, kind = s.water
            terrain.append(f"{kind} {near_water:.0f} units away"
                           if near_water > result.radius else f"**{kind} in range**")
        if terrain:
            lines.append("    " + SEP.join(terrain))

        if s.missing:
            lines.append(f"    _no {', '.join(_resource_word(r) for r in s.missing)} "
                         f"in range_")
        if s.also:
            # Free information, and often the deciding one. Capped at three: past that it
            # is a list of everything on the map near a popular spot.
            lines.append("    also in range: "
                         + ", ".join(f"{_resource_word(r)} {n}" for r, n in s.also[:3]))

    footer = f"within {result.radius:.1f} map units, which is a base's own reach"
    if len(result.resources) > 1:
        # Only meaningful for a multi-resource question. With one named resource every
        # candidate covers it by construction, so "327 of 327" is a tautology dressed as
        # a statistic.
        footer += (f"{SEP}{result.complete_sites} of {result.considered} spots reach "
                   f"all of it")
    # **The caveat changed and it had to be narrowed, not dropped.** The card used to say
    # "I can't tell you if the ground is flat or buildable", and half of that is no longer
    # true: terrain roughness is measured from the height of every placed actor inside the
    # radius, calibrated against the 32 spots the game itself marks as base camp areas.
    #
    # The other half is still true and is now the whole caveat. Roughness separates a
    # plateau from a cliff; it does not know about no-build zones, and it measures the
    # ground where things were PLACED rather than the ground everywhere. Weakening the
    # sentence to match what is actually known is the point - a caveat that overstates is
    # ignored, and then it is not there when it matters.
    if result.features_known:
        footer += (f"{SEP}flat means within ±{result.flat_cm / 100:.1f}m, measured like "
                   f"the game's own base areas"
                   f"{SEP}I still can't see no-build zones")
    else:
        footer += (f"{SEP}I can't tell you if the ground is flat or buildable - "
                   f"base_features.json isn't built")
    return Card(title=title, lines=lines, footer=footer, colour=TIER_ADVICE)


def _blocked_phrase(result: "ProgressionResult") -> str:
    """"40 need a higher level, 3 need a tower boss beaten".

    Present so a card showing five rows out of a hundred cannot read as though a hundred
    were considered and ninety-five rejected on merit.
    """
    counts = {b.value: n for b, n in result.blocked.items()}
    parts = [f"{counts[key]} {singular if counts[key] == 1 else plural} {tail}"
             for key, (singular, plural, tail) in _BLOCKER_LABELS if counts.get(key)]
    return ", ".join(parts)


def _progression_footer(result: "ProgressionResult") -> str:
    bits = []
    if result.level is not None:
        if result.level_is_a_floor:
            # The one derived number on this card, and it is named as one. It is a floor
            # from the technologies already unlocked, so anything gated between it and
            # the player's real level is being wrongly shown as out of reach - which is
            # the safe direction, and the sentence says which direction it is.
            bits.append(f"assuming you're at least level {result.level}, from what "
                        f"you've already unlocked - say your level for a sharper answer")
        else:
            bits.append(f"player level {result.level}")
    else:
        # Neither stated nor inferable. The level gate did not run, and a list that looks
        # filtered but is not is worse than one that says so.
        bits.append("no level known, so nothing is filtered by level")

    have = []
    if result.points is not None:
        have.append(f"{result.points} pt")
    if result.ancient_points is not None:
        have.append(f"{result.ancient_points} ancient pt")
    if have:
        bits.append("you have " + " and ".join(have))
    bits.append("most advanced first, which is not a ranking")
    return SEP.join(bits)
