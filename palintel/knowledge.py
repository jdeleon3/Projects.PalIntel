"""Knowledge base and entity lexicon — the local, deterministic half of the system.

Everything here is loaded from versioned files at startup and queried in memory. No
network, no model. See Docs/02-data-model.md.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- entities

def squash(s: str) -> str:
    """Strip everything that exists only because of ASR tokenisation.

    Speech-to-text renders one invented word as several English ones: "Leezpunk"
    arrives as "Lee's bunk", "Mycora" as "my Korra". Comparing across that split with
    the spaces intact drops similarity far below any usable bar, even when the letters
    line up almost exactly.
    """
    return re.sub(r"[^a-z]", "", s.lower())


def phonetic(word: str) -> str:
    """Compact consonant skeleton, used alongside edit distance rather than alone."""
    w = squash(word)
    if not w:
        return ""
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks"),
                 ("gh", "g"), ("kn", "n"), ("wr", "r"), ("mb", "m")):
        w = w.replace(a, b)
    out = w[0].upper() + re.sub(r"[aeiou]", "", w[1:]).upper()
    return re.sub(r"(.)\1+", r"\1", out)


# Common English function words. An n-gram made only of these cannot be an entity, but
# left in it generates spurious matches that outrank the real one: "should I" scores
# well against several Pal names purely by letter overlap, burying the correct
# candidate. This is a general linguistic filter, NOT the query-template word list used
# during evaluation - production sees arbitrary phrasing and cannot assume templates.
STOPWORDS = frozenset("""
a an and any are as at be been but by can could did do does for from get give go had
has have he her him his how i if in is it its me my no not of on or our out say she
should so some tell that the their them then there these they this those to us use
used want was we were what when where which who why will with would you your
""".split())

# The wake word addresses the assistant; it is never an entity. Left in, it is matched
# against the lexicon on EVERY utterance - "pal" scores 0.57 against "coal", which was
# enough to answer a question about a Pal with a coal location.
WAKE_WORDS = frozenset({"hey", "pal", "palintel", "ok", "okay"})


@dataclass(frozen=True)
class Candidate:
    """A possible entity reading of part of an utterance."""
    canonical: str
    # "pal" | "resource" | "leader". A leader is one of the eight humans who owns a tower
    # - Victor, Zoe - and it is a third kind rather than an alias of the Pal they fight
    # with because those are different fights: `BOSS_BlackGriffon` is a field alpha and
    # `GYM_BlackGriffon` is Victor's tower, and both are called Shadowbeak. Every check
    # in this project tests for "pal" or "resource" explicitly, so the new value is inert
    # everywhere it has not been asked for.
    kind: str
    score: float
    matched_text: str

    def __str__(self) -> str:
        return f"{self.canonical} ({self.score:.2f} from {self.matched_text!r})"


class Lexicon:
    """Canonical entity names, and the ranking of transcript fragments against them.

    **This class ranks. It does not decide.** No threshold, no rejection — it returns
    the best candidates with scores and lets the router choose using sentence context
    it alone possesses. Thresholding here discarded entities that were correctly ranked
    first: 61.5% accepted versus 79.5% present in the top 3, 92.3% in the top 10.
    See Docs/adr/0016-entity-resolution-in-router.md.
    """

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.game_version: str = data["game_version"]
        self._forms: dict[str, tuple[str, list[tuple[str, str]]]] = {}

        # Paldeck slot per Pal. A base form and its element variant ("Menasting" and
        # "Menasting Terra", internally DarkScorpion and DarkScorpion_Ground) share an
        # index, which makes this the game's own definition of a variant family - better
        # than matching on a name prefix, which only happens to agree.
        self._family: dict[str, int] = {}

        # Tower leader -> the Pal they fight with. Not used to resolve the fight - that
        # goes through bosses.json, which names a character id and so cannot confuse the
        # tower with the field alpha - but the pair is what a card says out loud.
        self._leader_pal: dict[str, str] = {}

        for p in data["pals"]:
            surfaces = [p["canonical"].lower(), *(a.lower() for a in p["aliases"])]
            self._forms[p["canonical"]] = ("pal", [(s, squash(s)) for s in surfaces])
            if p.get("zukan_index") is not None:
                self._family[p["canonical"]] = p["zukan_index"]
        for r in data["resources"]:
            surfaces = [r["canonical"].replace("_", " ").lower(),
                        *(a.lower() for a in r["aliases"])]
            self._forms[r["canonical"]] = ("resource", [(s, squash(s)) for s in surfaces])

        # Optional, so a lexicon.json built before the leader ingest still loads. The
        # eight are an enrichment: without them "how do I beat Victor" declines, which
        # is what it did before and is not wrong, only unhelpful.
        for lead in data.get("leaders", []):
            surfaces = [lead["canonical"].lower(),
                        *(a.lower() for a in lead["aliases"])]
            self._forms[lead["canonical"]] = ("leader",
                                              [(s, squash(s)) for s in surfaces])
            self._leader_pal[lead["canonical"]] = lead["pal"]

        self._phonetic = {c: phonetic(c) for c in self._forms}

    @property
    def canonical_names(self) -> list[str]:
        return sorted(self._forms)

    def pals(self) -> list[str]:
        return sorted(c for c, (k, _) in self._forms.items() if k == "pal")

    def resources(self) -> list[str]:
        return sorted(c for c, (k, _) in self._forms.items() if k == "resource")

    def leaders(self) -> list[str]:
        """The eight tower leaders. Deliberately absent from `pals()`, which generates
        the routers' Pal enum - Victor is not a species and must not become selectable
        as one."""
        return sorted(self._leader_pal)

    def leader_pal(self, leader: str) -> str | None:
        return self._leader_pal.get(leader)

    def same_family(self, a: str, b: str) -> bool:
        """True when two Pals are the base and variant of one Paldeck slot.

        Naming both is a different failure from naming an unrelated Pal: the answer is
        over-specified rather than wrong, and on a second screen it renders as two
        titled cards the player picks between. Naming Pyrin when Pierdon was meant has
        no such reading.
        """
        fa, fb = self._family.get(a), self._family.get(b)
        return fa is not None and fa == fb and a != b

    def family(self, name: str) -> list[str]:
        """Every Pal sharing `name`'s Paldeck slot, including itself. Always 1 or 2."""
        f = self._family.get(name)
        if f is None:
            return [name]
        return sorted(c for c, i in self._family.items() if i == f)

    def rank(self, text: str, limit: int = 10) -> list[Candidate]:
        """Rank entities against an utterance, best first. Never filters."""
        words = re.findall(r"[a-z']+", text.lower())
        # 3-grams matter: word-splitting can yield three tokens, not just two
        # ("the nurse I grew down" for Aegidron).
        grams = []
        for n in (1, 2, 3):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                # Drop all-stopword n-grams. They cannot name an entity, and their
                # letter overlap outranks genuine matches: without this, "health
                # sphere" -> Helzephyr falls out of the top 5 entirely.
                if all(w in STOPWORDS or w in WAKE_WORDS for w in gram):
                    continue
                # A gram containing the wake word is address, not content.
                if any(w in WAKE_WORDS for w in gram):
                    continue
                grams.append(" ".join(gram))
        squashed = [(g, squash(g)) for g in grams if squash(g)]
        if not squashed:
            return []

        out: list[Candidate] = []
        for canon, (kind, surfaces) in self._forms.items():
            best, best_text = 0.0, ""
            cp = self._phonetic[canon]
            for g, gs in squashed:
                for s, ss in surfaces:
                    score = max(SequenceMatcher(None, g, s).ratio(),
                                SequenceMatcher(None, gs, ss).ratio())
                    if score > best:
                        best, best_text = score, g
                # Phonetic agreement only counts on keys long enough to be informative;
                # three-letter keys collide constantly.
                if cp and len(cp) >= 4 and phonetic(gs) == cp and best < 0.95:
                    best, best_text = 0.95, g
            if best > 0:
                out.append(Candidate(canon, kind, round(best, 3), best_text))

        # Ties break toward the more specific entity. "Quartz ore" matches both `quartz`
        # and `ore` at 1.00; without this the winner is whichever the lexicon happened
        # to load first, which answered with ore. A longer exact match carries more
        # information, and the compound's head noun is the generic one.
        out.sort(key=lambda c: (-c.score, -len(c.canonical), c.canonical))
        return out[:limit]


# ---------------------------------------------------------------------------- nodes

@dataclass(frozen=True)
class ResourceNode:
    node_id: str
    resource: str
    map_x: float
    map_y: float
    node_count: int
    spread: float
    min_player_level: int | None
    danger: str | None
    area_hint: str | None

    def distance_to(self, x: float, y: float) -> float:
        return math.dist((self.map_x, self.map_y), (x, y))


@dataclass(frozen=True)
class RanchDrop:
    item: str
    stack: int = 1
    chance_percent: int | None = None

    def label(self) -> str:
        out = self.item
        if self.stack > 1:
            out += f" x{self.stack}"
        if self.chance_percent is not None:
            out += f" ({self.chance_percent}%)"
        return out


@dataclass(frozen=True)
class Ranch:
    """What a Pal produces when assigned to a Ranch.

    The only entity in the knowledge base whose facts are not extracted from the game
    files - the mapping is not in any of the 284 data tables, so it comes from the
    community wiki with the pak's roster as a cross-check (ADR-0014's amendment). That
    provenance is weaker than everything else on a Tier 1 card, so `verified` travels
    with it and the card attributes the source rather than presenting it as extracted
    fact.
    """
    drops: list[RanchDrop]
    per_cycle: int
    food: int
    verified: bool = True


@dataclass(frozen=True)
class PalDrop:
    """One item a Pal yields when defeated or captured.

    The mirror of `Dropper`: same rows, read the other way. Both come from one collection
    in build_pal_drops so the resource card's "also drops from" line and a "what does X
    drop" answer cannot disagree about the same fact.
    """
    item: str
    rate: float
    low: int
    high: int
    alpha_only: bool = False
    # The level band this row describes. 0 is the ordinary creature; 70 and 80 are the
    # endgame tables, which drop entirely different things in far larger quantities.
    min_level: int = 0

    def amount(self) -> str:
        return str(self.low) if self.low == self.high else f"{self.low}-{self.high}"


@dataclass(frozen=True)
class Dropper:
    """A Pal that yields a resource when defeated or captured.

    `alpha_only` is carried rather than hidden because it changes the claim: the drop
    table names boss variants separately, and where only that row exists the drop is the
    alpha's, not an ordinary encounter's. Currently zero published droppers are
    alpha-only - the field exists so a future patch introducing one cannot quietly
    overstate what a card promises.
    """
    pal: str
    rate: float
    low: int
    high: int
    alpha_only: bool = False
    min_level: int = 0

    def amount(self) -> str:
        return str(self.low) if self.low == self.high else f"{self.low}-{self.high}"


@dataclass(frozen=True)
class SpawnArea:
    """Somewhere a Pal can be encountered in the overworld.

    An area is a place to stand, not a pinpoint: `spawn_points` spawner actors within
    `spread` of the reported coordinate. `encounter_share` is the weight share of the
    sheets involved - the chance a spawner here rolls this species at all - and it is the
    difference between "one of the three things in this field" and "a 1-in-100 roll".
    Reporting a location without it sends the player to camp a spot they will never see
    the Pal at. See tools/ingest/build_pal_spawns.py.
    """
    area_id: str
    pal: str
    kind: str          # "normal" | "alpha" | "predator"
    map_x: float
    map_y: float
    spawn_points: int
    spread: float
    level_min: int
    level_max: int
    night_only: bool
    encounter_share: float

    def distance_to(self, x: float, y: float) -> float:
        return math.dist((self.map_x, self.map_y), (x, y))

    @property
    def density(self) -> float:
        """Expected encounters if you stand here. The ordering key when position is
        unknown, and the reason a rare Pal's 40-point area can outrank a common one's."""
        return self.spawn_points * self.encounter_share


# The order a Pal's wild level band falls through, identical to execution.SPAWN_KINDS and
# for the same reason: a Pal only ever placed as a field alpha (Necromus, Paladius) has a
# real level, and reporting "no level" for it would be a different and wrong answer.
_BAND_KINDS = ("normal", "alpha", "predator")


def _load_attributes(base: Path,
                     spawns: list["SpawnArea"]) -> tuple[dict[str, "PalAttributes"], dict[str, str]]:
    """Join typing, work suitability and wild level into one row per Pal.

    Optional in exactly the way the drop and ranch datasets are: `work.json` has its own
    ingest step, and a checkout that has not run it answers every other class normally
    and declines this one.

    **The level band is computed from `spawns`, not stored.** It is the same list the
    location card reads, so "Anubis, lvl 68-72" on a spawn card and a level filter here
    cannot drift apart - which they would the moment a second file recorded the same
    fact. `elements.json` is read here even though `counters.py` already reads it,
    because the two need different keys: a boss is a character id and a card names a
    species.
    """
    work_path = base / "work.json"
    if not work_path.exists():
        return {}, {}
    work_raw = json.loads(work_path.read_text(encoding="utf-8"))
    jobs: dict[str, str] = work_raw.get("jobs", {})

    typing: dict[str, tuple[str, ...]] = {}
    elements_path = base / "elements.json"
    if elements_path.exists():
        for p in json.loads(elements_path.read_text(encoding="utf-8"))["pals"]:
            if p.get("name"):
                # 439 of the 739 typed rows have no display name - summons, quest actors,
                # boss variants. setdefault keeps the first, which is the base row.
                typing.setdefault(p["name"], tuple(p["elements"]))

    # The DISTINCT bands, not their min and max. Collapsing them is the trap: Grizzbolt
    # is 18-22 in three areas and 80 in seventeen, so "lvl 18-80" is arithmetically true,
    # reads as one continuous range, and would answer "an electric Pal at level 45" with
    # a species that appears at no such level anywhere. Well-formed and wrong, which is
    # the shape CLAUDE.md says bad data takes in this project.
    bands: dict[str, dict[str, set[tuple[int, int]]]] = {}
    for a in spawns:
        bands.setdefault(a.pal, {}).setdefault(a.kind, set()).add(
            (a.level_min, a.level_max))

    suitability = {e["name"]: e for e in work_raw["entries"]}

    out: dict[str, PalAttributes] = {}
    for name in set(suitability) | set(typing):
        by_kind = bands.get(name, {})
        kind = next((k for k in _BAND_KINDS if k in by_kind), None)
        found = sorted(by_kind.get(kind, ()))
        entry = suitability.get(name)
        out[name] = PalAttributes(
            name=name,
            elements=typing.get(name, ()),
            work=dict(entry["levels"]) if entry else {},
            best_work=entry["best"] if entry else None,
            bands=tuple(found), level_kind=kind,
        )
    return out, jobs


@dataclass(frozen=True)
class Mount:
    """A Pal you can ride, and the two speeds the game actually distinguishes.

    **`unlock_level` is the PLAYER's level**, from the saddle's technology - the one
    place in this project where a level on a card is not the Pal's. That is what makes
    *"the fastest mount I can get at level 60"* answerable as a fact rather than as the
    uncalibrated judgement STATUS's 2026-08-11 decision refused: the saddle unlocks at a
    stated level, and no "how far above your level can you cope" constant is involved.

    `None` means no technology row unlocks this saddle - two of the 108 - and it must
    never be read as "available now". A level filter excludes them and says how many.

    **There is no flight speed.** A flying mount's ridden speed is `ride`, the same field
    a ground mount uses, so flying and ground are one category here. That is the pak's
    distinction, not a simplification: see tools/ingest/build_mounts.py for the seven
    flight signals that were measured and falsified.
    """
    name: str
    character_id: str
    unlock_level: int | None
    ride: int | None           # RideSprintSpeed - on land AND in the air
    swim: int | None           # SwimDashSpeed

    def speed(self, medium: str | None) -> int | None:
        """Speed in `medium`, or the better of the two when none was asked for.

        "The fastest mount I can get" with no medium named is asking how fast you can
        travel on it, so the answer is whichever of its two speeds is higher - a max over
        two stated numbers, with `fastest_medium` naming which one won so the card never
        implies a boat is fast on land.
        """
        if medium == "land":
            return self.ride
        if medium == "water":
            return self.swim
        return max((s for s in (self.ride, self.swim) if s), default=None)

    @property
    def fastest_medium(self) -> str | None:
        if self.ride is None and self.swim is None:
            return None
        return "water" if (self.swim or 0) > (self.ride or 0) else "land"

    def available_at(self, player_level: int) -> bool:
        """True when the saddle's technology is unlocked at `player_level`.

        False for the two with no technology row. Excluding them is the conservative
        reading: their availability is unknown, and a card claiming you can get one at
        level 60 would be asserting something the pak does not say.
        """
        return self.unlock_level is not None and self.unlock_level <= player_level


@dataclass(frozen=True)
class BaseFeatures:
    """What the world says about a place, beyond what is minable there.

    Three signals, all extracted, and the first is worth more than the other two: the game
    marks 32 spots with `BP_BaseCampPopularArea_C` and that is the designers' own answer
    to where a base goes. See tools/ingest/build_base_features.py for the two independent
    corroborations it was given before being used.

    `roughness` is a **proxy** — the height spread of placed actors inside one base radius
    — and every card built on it has to say so. It separates a plateau from a cliff. It
    does not know about no-build zones, and it measures the ground where things were
    placed rather than the ground everywhere.
    """
    # Grid cells one base radius across, "gx,gy" -> height standard deviation in cm.
    roughness: dict[str, int]
    # The bar for "flat enough", calibrated as the 75th percentile of the 32 marked
    # areas' own roughness. Derived from the game rather than chosen.
    flat_cm: int
    radius: float
    popular_areas: tuple[tuple[float, float], ...]
    # (x, y, kind) with kind in {"water", "river", "ocean"}.
    water: tuple[tuple[float, float, str], ...]
    # The 32 marked areas' own roughness and water distance, sorted, as the yardstick a
    # rating is measured against. Terrain and water only - see `deposit_deciles`.
    marked_roughness: tuple[int, ...] = ()
    marked_water: tuple[float, ...] = ()
    # Deposits and distinct resources within a base radius, at every node cluster on the
    # map, as deciles. **A different yardstick on purpose**: the marked areas hold a
    # median of THREE deposits, so the designers are marking flat ground near water and
    # not resource-rich ground, and scoring resources against them would say almost
    # anything is excellent.
    deposit_deciles: tuple[int, ...] = ()
    kind_deciles: tuple[int, ...] = ()

    @staticmethod
    def _percentile(value: float, sorted_values, higher_is_better: bool) -> int | None:
        """Where `value` falls in a reference distribution, 0-100.

        Returns None on an empty reference rather than 50, because "no yardstick" and
        "exactly average" are different answers and only one of them is true.
        """
        if not sorted_values:
            return None
        below = sum(1 for v in sorted_values if v < value)
        pct = round(100 * below / len(sorted_values))
        return pct if higher_is_better else 100 - pct

    def roughness_percentile(self, value: int) -> int | None:
        """How flat, against the marked areas. 100 = flatter than all of them."""
        return self._percentile(value, self.marked_roughness, higher_is_better=False)

    def water_percentile(self, value: float) -> int | None:
        return self._percentile(value, self.marked_water, higher_is_better=False)

    @property
    def water_bar(self) -> float | None:
        """How close counts as "near water", from the marked areas' own median.

        **Not one base radius, which is what the first version used and which made the
        card contradict itself**: it marked water 11 units away as a fail while also
        reporting it was closer than 78% of the marked areas. Measured, those areas sit a
        median of 23 units from water — three times a base radius — so a
        within-the-radius bar is stricter than the standard the designers themselves
        build to, and a criterion nobody can meet is not a criterion.
        """
        if not self.marked_water:
            return None
        mid = len(self.marked_water) // 2
        return self.marked_water[mid]

    def deposit_percentile(self, value: int) -> int | None:
        return self._percentile(value, self.deposit_deciles, higher_is_better=True)

    def roughness_at(self, x: float, y: float) -> int | None:
        """Ground roughness in cm, or None where too few actors stand to say.

        None is a real answer and must not be rendered as flat: a cell with three actors
        in it is somewhere nobody placed anything, which is as likely to be a cliff face
        as a meadow.
        """
        return self.roughness.get(f"{int(x // self.radius)},{int(y // self.radius)}")

    def is_flat(self, x: float, y: float) -> bool | None:
        r = self.roughness_at(x, y)
        return None if r is None else r <= self.flat_cm

    def nearest_water(self, x: float, y: float) -> tuple[float, str] | None:
        """(distance in map units, kind) of the closest water, or None if none is loaded."""
        best = None
        for wx, wy, kind in self.water:
            d = math.dist((x, y), (wx, wy))
            if best is None or d < best[0]:
                best = (d, kind)
        return best

    def nearest_marked_area(self, x: float, y: float) -> float | None:
        if not self.popular_areas:
            return None
        return min(math.dist((x, y), a) for a in self.popular_areas)


@dataclass(frozen=True)
class PalAttributes:
    """What a Pal *is*, as opposed to where it is or what it drops.

    The three axes the attribute search filters on, joined into one row so a query can
    apply all three without three lookups. Every field is extracted: `elements` from
    `ElementType1/2`, `work` from the `WorkSuitability_*` columns, and the level band
    from the same spawn areas the location card reads - so a level printed here and a
    level printed on a spawn card cannot disagree.

    `bands` is empty for the Pals the overworld never places. That is a real state and
    not missing data: a tower boss has no wild level, so it cannot match a level filter
    and the card must not imply it was considered and rejected.
    """
    name: str
    elements: tuple[str, ...]
    # Job enum ("Mining") -> level. Only jobs the Pal can actually do; absent means zero.
    work: dict[str, int]
    best_work: str | None
    # Every DISTINCT (low, high) this Pal is placed at, ascending. Several rather than
    # one because the ranges are disjoint: Grizzbolt is (18, 22) and (70, 72) and
    # (70, 80) and (80, 80), and merging those into 18-80 claims levels it never has.
    bands: tuple[tuple[int, int], ...] = ()
    # Which encounter the bands came from. "normal" for almost everything; "alpha" or
    # "predator" for the handful only ever placed as one, mirroring `find_pal_spawns`'
    # fall-through so the two answers describe the same creature.
    level_kind: str | None = None

    @property
    def level_min(self) -> int | None:
        return self.bands[0][0] if self.bands else None

    @property
    def level_max(self) -> int | None:
        return max(hi for _, hi in self.bands) if self.bands else None

    def band_at(self, level: int) -> tuple[int, int] | None:
        """The band containing `level`, or None.

        Containment, not a ceiling. STATUS's 2026-08-11 decision is that "level" means
        the PAL's level, and the exact reading of "an electric Pal that is level 60" is
        one that actually turns up at 60 - not every Pal weaker than 60, which is a
        different question and one the player can still ask ("Pals up to 60"). The
        matching band is returned rather than a bool because the card should print the
        range the player will actually meet, not the species' whole spread.
        """
        return next((b for b in self.bands if b[0] <= level <= b[1]), None)

    def spawns_at(self, level: int) -> bool:
        return self.band_at(level) is not None


@dataclass
class KnowledgeBase:
    game_version: str
    lexicon: Lexicon
    nodes: list[ResourceNode] = field(default_factory=list)
    spawns: list[SpawnArea] = field(default_factory=list)
    # Pals the game knows but the overworld never places: tower pairs, raid bosses, the
    # Terraria collab, dungeon-only species. Held separately so "Jetragon does not spawn
    # in the overworld" and "I have no data for that" stay different answers - only one
    # of them is true, and a player acts differently on each.
    pals_without_areas: frozenset[str] = frozenset()
    # resource -> the Pals that drop it, best rate first. A second way to get the thing,
    # which matters most exactly when the first one is out of reach: a player who cannot
    # survive a level-40 mining spot may still be able to farm a Pal for it. Empty when
    # the dataset is absent, which is normal - it is built by its own ingest step.
    droppers: dict[str, list[Dropper]] = field(default_factory=dict)
    # Pal -> what it drops. 302 Pals drop something; the rest drop nothing at all, which
    # is a real answer rather than missing data.
    pal_drops: dict[str, list[PalDrop]] = field(default_factory=dict)
    # Item -> the Pals that drop it. 151 items. Deliberately NOT in the lexicon: these
    # are ordinary English words (Arrow, Bone, Leather, Horn) and adding them to the
    # corrector would pull spurious candidates into every query, including the ones that
    # name no item at all. They live in the tool enum, where the router reaches for them
    # only when the sentence is asking where something comes from.
    item_sources: dict[str, list[Dropper]] = field(default_factory=dict)
    # Pal -> ranch output. Empty when the dataset is absent, which is normal: it has its
    # own ingest step and the answer is complete without it.
    ranch: dict[str, Ranch] = field(default_factory=dict)
    # Where the ranch facts came from, carried so the card can attribute them. Empty
    # string when there is no ranch data to attribute.
    ranch_source: str = ""
    # Pal -> element, work and wild level band. What the attribute search selects over.
    # Empty when work.json is absent, which turns that one class off and leaves every
    # other answer intact.
    attributes: dict[str, "PalAttributes"] = field(default_factory=dict)
    # Job enum -> the game's own label ("EmitFlame" -> "Kindling"). From the UI text
    # table, so a card prints what the player reads in game rather than a pak enum.
    jobs: dict[str, str] = field(default_factory=dict)
    # Pal -> saddle, unlock level and speeds, for the 108 that can be ridden. Empty when
    # mounts.json is absent, which turns the mount filter off and leaves the rest of the
    # attribute search working.
    mounts: dict[str, "Mount"] = field(default_factory=dict)
    # How far a base's Pals reach, in map units. `BaseCampAreaRange` read from the pak and
    # converted through the same transform every coordinate here uses - see
    # tools/ingest/build_base_camp.py. None when base_camp.json is absent, which turns
    # base siting off: a radius is the entire question that class asks, and guessing one
    # would put a coordinate on a card backed by nothing.
    base_radius: float | None = None
    # The other three base-siting signals - marked areas, water, terrain roughness - from
    # tools/ingest/build_base_features.py. None when base_features.json is absent, which
    # leaves siting answering on resource density alone: exactly what it did before.
    base_features: "BaseFeatures | None" = None

    @classmethod
    def load(cls, version: str = "1.0.2", root: Path | None = None) -> "KnowledgeBase":
        base = (root or REPO) / "data" / version
        lexicon = Lexicon(base / "lexicon.json")

        raw = json.loads((base / "resource_nodes.json").read_text(encoding="utf-8"))
        nodes = []
        for n in raw["nodes"]:
            nodes.append(ResourceNode(
                node_id=n["node_id"], resource=n["resource"],
                map_x=n["map_x"], map_y=n["map_y"],
                node_count=n["node_count"], spread=n.get("spread_map_units", 0.0),
                min_player_level=n.get("min_player_level"),
                danger=n.get("danger"), area_hint=n.get("area_hint"),
            ))

        spawn_raw = json.loads((base / "pal_spawns.json").read_text(encoding="utf-8"))
        spawns = [SpawnArea(
            area_id=a["area_id"], pal=a["pal"], kind=a["kind"],
            map_x=a["map_x"], map_y=a["map_y"],
            spawn_points=a["spawn_points"], spread=a["spread_map_units"],
            level_min=a["level_min"], level_max=a["level_max"],
            night_only=a["night_only"], encounter_share=a["encounter_share"],
        ) for a in spawn_raw["areas"]]

        # Optional: the bot answers every Q1 query without it, just without the extra
        # line, so a checkout that has not run build_pal_drops.py still works.
        droppers: dict[str, list[Dropper]] = {}
        pal_drops: dict[str, list[PalDrop]] = {}
        item_sources: dict[str, list[Dropper]] = {}
        drop_path = base / "pal_drops.json"
        if drop_path.exists():
            drop_raw = json.loads(drop_path.read_text(encoding="utf-8"))
            droppers = {res: [Dropper(pal=d["pal"], rate=d["rate"], low=d["min"],
                                      high=d["max"], alpha_only=d["alpha_only"],
                                      min_level=d.get("min_level", 0))
                              for d in ds]
                        for res, ds in drop_raw["by_resource"].items()}
            item_sources = {item: [Dropper(pal=d["pal"], rate=d["rate"], low=d["min"],
                                          high=d["max"],
                                          alpha_only=d["alpha_only"],
                                          min_level=d.get("min_level", 0))
                                   for d in ds]
                            for item, ds in drop_raw.get("by_item", {}).items()}
            pal_drops = {pal: [PalDrop(item=d["item"], rate=d["rate"], low=d["min"],
                                       high=d["max"], alpha_only=d["alpha_only"],
                                       min_level=d.get("min_level", 0))
                               for d in ds]
                         for pal, ds in drop_raw.get("by_pal", {}).items()}

        ranch: dict[str, Ranch] = {}
        ranch_source = ""
        ranch_path = base / "ranch_drops.json"
        if ranch_path.exists():
            ranch_raw = json.loads(ranch_path.read_text(encoding="utf-8"))
            ranch_source = ranch_raw.get("source", "")
            ranch = {e["pal"]: Ranch(
                drops=[RanchDrop(item=d["item"], stack=d.get("stack", 1),
                                 chance_percent=d.get("chance_percent"))
                       for d in e["drops"]],
                per_cycle=e["per_cycle"], food=e["food"],
                verified=e.get("roster_verified", True)) for e in ranch_raw["entries"]}

        attributes, jobs = _load_attributes(base, spawns)

        mounts: dict[str, Mount] = {}
        mount_path = base / "mounts.json"
        if mount_path.exists():
            mounts = {m["name"]: Mount(
                name=m["name"], character_id=m["character_id"],
                unlock_level=m["unlock_level"],
                ride=m["ride_speed"], swim=m["swim_speed"])
                for m in json.loads(
                    mount_path.read_text(encoding="utf-8"))["entries"]}

        base_radius: float | None = None
        base_path = base / "base_camp.json"
        if base_path.exists():
            base_radius = json.loads(
                base_path.read_text(encoding="utf-8"))["map_units"]

        base_features: BaseFeatures | None = None
        features_path = base / "base_features.json"
        if features_path.exists():
            raw_f = json.loads(features_path.read_text(encoding="utf-8"))
            base_features = BaseFeatures(
                roughness=raw_f["roughness"],
                flat_cm=raw_f["flat_cm"],
                radius=raw_f["radius_map_units"],
                popular_areas=tuple((a["map_x"], a["map_y"])
                                    for a in raw_f["popular_areas"]),
                water=tuple((w["map_x"], w["map_y"], w["kind"])
                            for w in raw_f["water"]),
                marked_roughness=tuple(sorted(
                    p["roughness_cm"] for p in raw_f.get("marked_area_profile", ())
                    if p.get("roughness_cm") is not None)),
                marked_water=tuple(sorted(
                    p["water_distance"] for p in raw_f.get("marked_area_profile", ())
                    if p.get("water_distance") is not None)),
                deposit_deciles=tuple(
                    raw_f.get("site_deciles", {}).get("deposits", ())),
                kind_deciles=tuple(
                    raw_f.get("site_deciles", {}).get("resource_kinds", ())),
            )

        return cls(game_version=raw["game_version"], lexicon=lexicon, nodes=nodes,
                   spawns=spawns,
                   pals_without_areas=frozenset(spawn_raw["pals_without_areas"]),
                   droppers=droppers, pal_drops=pal_drops,
                   item_sources=item_sources, ranch=ranch,
                   ranch_source=ranch_source,
                   attributes=attributes, jobs=jobs, mounts=mounts,
                   base_radius=base_radius, base_features=base_features)

    def job_label(self, job: str) -> str:
        """The game's word for a job enum, falling back to the enum itself."""
        return self.jobs.get(job, job)

    def summary(self) -> dict[str, object]:
        by_res: dict[str, int] = {}
        for n in self.nodes:
            by_res[n.resource] = by_res.get(n.resource, 0) + 1
        return {
            "game_version": self.game_version,
            "pals": len(self.lexicon.pals()),
            "resources": self.lexicon.resources(),
            "node_clusters": len(self.nodes),
            "by_resource": dict(sorted(by_res.items())),
            "spawn_areas": len(self.spawns),
            "pals_locatable": len({s.pal for s in self.spawns}),
            # Both are optional datasets, so 0 here is the difference between "nothing
            # matched" and "that class is not loaded" - which is otherwise invisible from
            # the outside, and is exactly the distinction `/palintel status` exists for.
            "pals_with_attributes": len(self.attributes),
            "work_jobs": len(self.jobs),
            "tower_leaders": len(self.lexicon.leaders()),
            "base_radius_map_units": self.base_radius,
        }
