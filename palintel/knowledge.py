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
    kind: str          # "pal" | "resource"
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

        for p in data["pals"]:
            surfaces = [p["canonical"].lower(), *(a.lower() for a in p["aliases"])]
            self._forms[p["canonical"]] = ("pal", [(s, squash(s)) for s in surfaces])
            if p.get("zukan_index") is not None:
                self._family[p["canonical"]] = p["zukan_index"]
        for r in data["resources"]:
            surfaces = [r["canonical"].replace("_", " ").lower(),
                        *(a.lower() for a in r["aliases"])]
            self._forms[r["canonical"]] = ("resource", [(s, squash(s)) for s in surfaces])

        self._phonetic = {c: phonetic(c) for c in self._forms}

    @property
    def canonical_names(self) -> list[str]:
        return sorted(self._forms)

    def pals(self) -> list[str]:
        return sorted(c for c, (k, _) in self._forms.items() if k == "pal")

    def resources(self) -> list[str]:
        return sorted(c for c, (k, _) in self._forms.items() if k == "resource")

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

        return cls(game_version=raw["game_version"], lexicon=lexicon, nodes=nodes,
                   spawns=spawns,
                   pals_without_areas=frozenset(spawn_raw["pals_without_areas"]),
                   droppers=droppers, pal_drops=pal_drops,
                   item_sources=item_sources, ranch=ranch,
                   ranch_source=ranch_source)

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
        }
