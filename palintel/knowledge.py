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


@dataclass
class KnowledgeBase:
    game_version: str
    lexicon: Lexicon
    nodes: list[ResourceNode] = field(default_factory=list)

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

        return cls(game_version=raw["game_version"], lexicon=lexicon, nodes=nodes)

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
        }
