"""The nine tower leaders, and the Pal each one fights with.

One parser, two consumers — `build_lexicon.py` turns the human names into a third entity
kind so *"how do I beat Victor"* resolves at all, and `build_bosses.py` attaches them to
the `GYM_` rows so the counter card can name the fight the way the player does. Sharing
the parse is not tidiness: the lexicon is built *before* bosses.json exists, so the
alternative was either a circular dependency or two copies of the same join.

**Two independent tables state this, and they agree.** That is unusual in this project
and worth the space, because the first version of this file was written believing only
one of them existed and describing the result as two stacked inferences.

    pal_names_flat.json      PAL_NAME_SnowBoss                       -> "Victor & Shadowbeak"
    DT_UniqueNPCText.json    BOSSNAME_DEMO_SNOWYMOUNTAIN_LEADER      -> "VICTOR"
                             BOSSNAME_DEMO_SNOWYMOUNTAIN_LEADER_PAL  -> "SHADOWBEAK"

The name table is the **primary** source and it is not an inference at all: one string,
written by the game, naming both halves of one fight. The text table is the
**cross-check** - it arrives at the same nine pairs by an entirely different route, and
`validate` fails the build if the two ever disagree. `corroborated` records the agreement
per row.

**This corrects a claim the repo made in three places**, and then corrects the
correction. `build_bosses.py`, the roadmap and STATUS all said no table links a `GYM_`
Pal to its tower; the 2026-08-11 batch found `DT_UniqueNPCText` and recorded the pairing
as an inference "strong at 8 pairs with no orphans". Both readings were short. The name
table had the answer stated plainly the whole time, in a file this project already
extracts and already builds the lexicon from - and it carries a **ninth** pair,
`Zenara & Astralym`, which the text table's `BOSSNAME_DEMO_*` keys do not have. The
earlier note that Astralym's tower simply has no leader was an artefact of reading one
table.

**What is still derived**, and it is one step rather than two: reaching `GYM_BlackGriffon`
from the name "Shadowbeak" goes through the boss dataset's own prefix inference. That is
declared in `build_bosses.py` and flagged per row.

**Still genuinely absent**, and this file must not be read as supplying them: tower
ORDINALS (nothing says Victor's is the fifth), faction names like "PAL Genetic Research
Unit", and any tower boss level.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# `PAL_NAME_SnowBoss` -> region token `Snow`. The primary source's key shape.
NAME_KEY = re.compile(r"^PAL_NAME_(?P<region>\w+?)Boss$")
# `BOSSNAME_DEMO_<REGION>_LEADER`, and the same key with `_PAL` on the end. Anchored so
# `NAME_VILLAGELEADER01` - the one other row in that table with LEADER in its key, and a
# placeholder whose text is literally "-" - cannot be read as a tenth tower.
LEADER_KEY = re.compile(r"^BOSSNAME_DEMO_(?P<region>[A-Z0-9]+)_LEADER(?P<pal>_PAL)?$")

# How the two tables spell the same place. Deliberately NOT used to join them - the join
# is on the pair of NAMES, which is exact - but kept so a row can say which region it
# belongs to using the more descriptive of the two spellings. `Dessert` is the game's
# own misspelling of Desert and `Grass` is its word for the starting plain.
_REGION_ALIAS = {
    "Snow": "SNOWYMOUNTAIN", "Grass": "PLAIN", "Dessert": "DESERT",
    "Forest": "FOREST", "Volcano": "VOLCANO", "Viking": "VIKING",
    "Sakurajima": "SAKURAJIMA", "Sorajima": "SORAJIMA", "Last": "WORLDTREE",
}


class LeaderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Leader:
    """One tower's human boss and the Pal fought alongside them."""
    region: str          # descriptive region token, e.g. SNOWYMOUNTAIN
    leader: str          # display-cased human name, e.g. Victor
    pal: str             # display-cased Pal name, e.g. Shadowbeak
    # True when DT_UniqueNPCText independently produced the same pair. False means only
    # the name table has it, which is the case for exactly one row today (Zenara &
    # Astralym) and is a weaker claim worth carrying rather than flattening.
    corroborated: bool = False

    @property
    def both(self) -> str:
        """How the game names the pair, and how a card should: "Victor & Shadowbeak"."""
        return f"{self.leader} & {self.pal}"


def _text(row: dict) -> str:
    """The English string out of a `TextData` cell.

    `SourceString` rather than `LocalizedString`: they agree on all sixteen rows here,
    and the source string is the one that does not change with the game's locale. The
    project has already shipped Japanese item names once by reading whichever copy a
    filename happened to resolve to (STATUS, "things that shipped wrong").
    """
    return ((row or {}).get("TextData") or {}).get("SourceString", "").strip()


def from_name_table(raw_dir: Path) -> dict[str, Leader]:
    """The primary source: `PAL_NAME_*Boss` rows whose value names two things.

    Keyed by region token. Returns empty when the file is absent, because every consumer
    of this is an enrichment - a lexicon without leader entries and a boss dataset
    without leader names are both complete answers to narrower questions.
    """
    path = raw_dir / "pal_names_flat.json"
    if not path.exists():
        return {}

    out: dict[str, Leader] = {}
    for row in json.loads(path.read_text(encoding="utf-8")):
        m = NAME_KEY.match(row.get("key", ""))
        name = (row.get("name") or "").strip()
        if not m or " & " not in name:
            # `PAL_NAME_RAID_YakushimaBoss002` is "Moon Lord" - a raid boss with no human
            # beside it. The ampersand is what distinguishes a paired fight, and it is
            # the game's own punctuation rather than a convention read into the string.
            continue
        leader, _, pal = name.partition(" & ")
        region = m.group("region")
        out[_REGION_ALIAS.get(region, region.upper())] = Leader(
            region=_REGION_ALIAS.get(region, region.upper()),
            leader=leader.strip(), pal=pal.strip())
    return out


def from_text_table(raw_dir: Path) -> dict[str, Leader]:
    """The cross-check: `BOSSNAME_DEMO_<REGION>_LEADER` and its `_PAL` sibling.

    A different table, a different key shape, and the pair split across two rows rather
    than joined in one. That independence is the whole value - it reaches the same
    answer without sharing a single assumption with the name table.
    """
    path = raw_dir / "tables" / "DT_UniqueNPCText.json"
    if not path.exists():
        return {}

    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = (doc[0] if isinstance(doc, list) else doc)
    rows = rows.get("Rows", rows)

    humans: dict[str, str] = {}
    pals: dict[str, str] = {}
    for key, row in rows.items():
        m = LEADER_KEY.match(key)
        if not m:
            continue
        value = _text(row)
        if not value or value == "-":
            continue
        # The table shouts: ZOE, GRIZZBOLT. Cards do not, and `Shadowbeak` is also how
        # every other dataset in the project spells it.
        (pals if m.group("pal") else humans)[m.group("region")] = value.title()

    return {r: Leader(region=r, leader=humans[r], pal=pals[r], corroborated=True)
            for r in humans if r in pals}


def parse(raw_dir: Path) -> list[Leader]:
    """Every tower leader, name table first, marked with whether the text table agrees."""
    primary = from_name_table(raw_dir)
    check = from_text_table(raw_dir)
    if not primary:
        # No name table. Fall back to the cross-check alone rather than returning
        # nothing: eight of nine is a better answer than none, and `corroborated` on a
        # row that had no primary to corroborate is still true of what produced it.
        return sorted(check.values(), key=lambda l: l.leader)

    out = []
    for region, lead in primary.items():
        other = check.get(region)
        out.append(Leader(region=region, leader=lead.leader, pal=lead.pal,
                          corroborated=other is not None
                          and (other.leader, other.pal) == (lead.leader, lead.pal)))
    return sorted(out, key=lambda l: l.leader)


def validate(raw_dir: Path, leaders: list[Leader]) -> list[str]:
    """Cross-table checks, as a list of complaints (empty is a pass).

    Returned rather than raised so the caller decides. `build_bosses.py` fails the build
    on these, because a silently half-ingested mapping would publish some towers with a
    leader and some without, and nothing on a card could tell those apart from a tower
    that genuinely has none.

    **A disagreement between the two tables is the one that matters.** Either would look
    entirely reasonable alone, and shipping the wrong human beside the right Pal is the
    well-formed-and-wrong failure this project is organised against.
    """
    problems = []
    check = from_text_table(raw_dir)
    primary = from_name_table(raw_dir)

    for region, lead in check.items():
        other = primary.get(region)
        if other is None:
            # The text table knows a region the name table does not. Not fatal on its own
            # - but it means the primary source is incomplete, which is worth stopping on
            # because everything downstream treats it as authoritative.
            problems.append(f"{lead.both} is in DT_UniqueNPCText and not in the name "
                            f"table - the primary source has a hole at {region}")
        elif (other.leader, other.pal) != (lead.leader, lead.pal):
            problems.append(f"the two tables disagree about {region}: name table says "
                            f"{other.both!r}, text table says {lead.both!r}")

    seen: dict[str, str] = {}
    for lead in leaders:
        if lead.pal in seen:
            # Two humans sharing one Pal would make the reverse lookup ambiguous, and the
            # card resolves in that direction.
            problems.append(f"{lead.pal} is claimed by both {seen[lead.pal]} "
                            f"and {lead.leader}")
        seen[lead.pal] = lead.leader
    return problems
