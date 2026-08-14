"""Build the canonical entity lexicon from PAK-extracted tables.

The lexicon is load-bearing infrastructure, not a lookup table. One source of truth
feeds four consumers (see Docs/adr/0007-entity-lexicon-boundary.md):

  1. STT keyterm boosting      - bias the acoustic model toward Palworld proper nouns
  2. Fuzzy transcript repair   - map mangled tokens back to canonical names
  3. LLM enum constraints      - generate the tool schemas' entity parameters
  4. Corpus entity tagging     - hybrid retrieval for Tier 3

Input : data/raw/*.json          (extracted from Pal-Windows.pak)
Output: data/<version>/lexicon.json

Usage: python tools/ingest/build_lexicon.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import _leaders
from _resources import UNPLACED_RESOURCES, derive

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# Placeholder strings that appear in untranslated or deliberately hidden rows. These
# are not names and must never reach the lexicon: "Unidentified Pal" is the game's
# mask for unreleased content and would otherwise become a matchable entity that
# resolves to three different Pals at once.
PLACEHOLDERS = {"en_text", "ja_text", "None", "", "Unidentified Pal"}

# Seed aliases for names whose spelling and pronunciation diverge badly enough that
# STT is unlikely to recover them unaided. This set is expected to GROW from observed
# failures - every misrecognition found in evaluation becomes a permanent alias.
SEED_ALIASES: dict[str, list[str]] = {
    "Aegidron": ["adrion"],
    "Anubis": ["anubus", "a newbis"],
    "Astralym": ["astrum"],
    "Carnibora": ["carbonara"],
    "Cattiva": ["cat eva", "cateva", "kativa"],
    "Cawgnito": ["cattivignito"],
    "Celesdir": ["celadir"],
    "Chillet": ["chill it", "chilet", "shall it", "chillette"],
    "Daedream": ["daedrum"],
    "Dazzi": ["dazzy"],
    "Depresso": ["depress oh", "de presso", "espresso", "depresa"],
    "Digtoise": ["dig toise", "dig tortoise", "digtois", "dictoise", "dick choice", "dictuis"],
    "Direhowl": ["direhalls", "dara hal"],
    "Dynamoff": ["dymoth"],
    "Eidrolon": ["illdreon"],
    "Faleris": ["fal aris", "phaleris", "valeris", "feleris"],
    "Fenglope": ["findlope"],
    "Finsider": ["fensideers"],
    "Foxparks": ["fox parks", "fox sparks", "foxpark"],
    "Frostplume": ["frostbloom"],
    "Galeclaw": ["galakclaw"],
    "Gloopie": ["gloopy"],
    "Grizzbolt": ["grizz bolt", "grizzly bolt", "gris bolt"],
    "Gumoss": ["gomoss", "gizmos"],
    "Helzephyr": ["hellsphere"],
    "Incineram": ["incinerate", "in cinerham", "incineran"],
    "Jetragon": ["jit dragon"],
    "Jormuntide": ["jormun tide", "your mun tide", "jorman tide", "jormuntied"],
    "Knocklem": ["knock limit"],
    "Lamball": ["lamb ball", "lambhall", "lam ball", "landball"],
    "Leezpunk": ["leithbunk"],
    # A failure RUN from the 2026-08-11 play session, and the first aliases in this file
    # harvested from unscripted speech rather than from prompts read off a list. Three
    # attempts at one name in ninety seconds - "Lani", "Lening", "Leneen" - two declined
    # and the third answered with the wrong class. Swept against 281 transcripts (271
    # eval + the 41 from that session): worst unrelated match 0.714, under both floors.
    "Lyleen": ["lani", "lening", "leneen"],
    "Lifmunk": ["life monk", "lif munk", "liftmunk", "lifmonk", "live monk"],
    "Mozzarina": ["moserina", "maserina"],
    "Mycora": ["micora"],
    "Neptilius": ["aptilius"],
    "Nitewing": ["night wing", "nitewin", "knight wing"],
    "Nyafia": ["nifia", "nefia"],
    "Omascul": ["omniscole", "amazkul"],
    "Orserk": ["ozurk"],
    "Pengullet": ["pen gullet", "penguin let", "pengulet"],
    "Petallia": ["penelia"],
    "Pierdon": ["pyridon", "pyrdun"],
    "Prunelia": ["pirelia"],
    "Sibelyx": ["silbix"],
    # "Celine" from play, "celery" from the eval set - the same name failing two ways.
    "Selyne": ["celine", "celery"],
    "Silvance": ["sylvans", "silvents"],
    "Solmora": ["syllamora"],
    "Surfent": ["surfin'"],
    "Suzaku": ["suzuki"],
    "Tanzee": ["tan zee", "tansy", "tanzy", "tanzi"],
    "Teafant": ["t event"],
    "Tetroise": ["titrois"],
    "Vanwyrm": ["fan worm", "fanworm", "van wurmworth"],
    "Verdash": ["virdach"],
    "Vixy": ["vixi"],
    "Whalaska": ["walexka"],
    "Whalaska Ignis": ["velasco ignis"],
    "Wispaw": ["wispond"],
    "Wistella": ["it's vastillia"],
    "Woolipop": ["wall e pop"],
    "Xenolord": ["zendelord"],
    "Xenovader": ["zinnovator"],
}

# Resource ALIASES, hand-maintained. The resource *set* is no longer written here - it is
# derived from the game's item categories in _resources.py, the same derivation the node
# ingest uses, so the lexicon cannot know about a resource the data does not have or miss
# one it does. What stays hand-written is the part no table contains: how speech-to-text
# mangles the word. These are ordinary English words, so the failure mode is homophones
# rather than the novel morphology the Pal names suffer from.
#
# A resource with no entry here is still in the lexicon; it simply carries only the
# aliases generated from its own name. Entries grow from observed failures.
RESOURCE_ALIASES: dict[str, list[str]] = {
    "ore": ["oar", "ore deposit", "ore node"],
    "coal": ["cole", "kohl", "coel"],
    "sulfur": ["sulphur", "sulfa", "sulfer"],
    "quartz": ["quarts", "kwartz", "pure quartz"],
    "crude_oil": ["crude oil", "cruel oil", "screwed oil"],
    "stone": ["stones", "rock", "rocks"],
    "wood": ["logs", "timber", "lumber"],
    "paldium_fragment": ["paldium", "palladium", "pal dium", "paldium fragments"],
    "hexolite_quartz": ["hexolite", "hexalite quartz", "hexolight"],
    "chromite": ["chromium", "cromite", "chrome ore"],
    "soralite": ["sorolite", "solarite", "sky island ore"],
    "paloxite": ["paloxide", "pal oxite", "world tree ore"],
    "nightstar_sand": ["nightstar", "night star sand", "night stone"],
    "red_berries": ["berries", "red berry", "berry"],
    "cavern_mushroom": ["cave mushroom", "cavern mushrooms"],
    "mushroom": ["mushrooms"],
}

# Aliases this short, or this common, cause more false matches than they fix. "or" as
# an alias for "ore" is a fair spoken homophone but matches "for" at 0.80 similarity,
# which silently tags half the corpus. "call" for "coal" and "courts" for "quartz" fail
# the same way. Precision matters more than recall here: ADR-0007 treats a confident
# wrong entity as worse than an admitted miss.
MIN_ALIAS_LEN = 4
ALIAS_STOPWORDS = {
    "or", "oar", "awe", "call", "courts", "oil", "for", "more", "your", "our",
    "all", "coil", "cold", "gold", "goal", "tall", "sort", "short",
}


def metaphone_key(word: str) -> str:
    """Phonetic skeleton for fuzzy matching.

    Prefers jellyfish's metaphone when available; otherwise falls back to a compact
    consonant-skeleton reduction. The fallback is deliberately simple - it exists so
    the pipeline never hard-depends on an optional package, and matching always pairs
    the phonetic key with edit distance rather than relying on it alone.
    """
    try:
        import jellyfish

        return jellyfish.metaphone(word)
    except ImportError:
        pass

    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks"),
                 ("gh", "g"), ("kn", "n"), ("wr", "r"), ("mb", "m")):
        w = w.replace(a, b)
    head = w[0].upper()
    tail = re.sub(r"[aeiou]", "", w[1:])
    out = head + tail.upper()
    return re.sub(r"(.)\1+", r"\1", out)


def safe_aliases(aliases: list[str]) -> list[str]:
    """Drop aliases too short or too common to be safe fuzzy-match targets."""
    out = []
    for a in aliases:
        a = a.strip()
        if not a or a.lower() in ALIAS_STOPWORDS:
            continue
        # Multi-word aliases are inherently specific, so the length floor applies to
        # single tokens only.
        if " " not in a and len(a) < MIN_ALIAS_LEN:
            continue
        out.append(a)
    return out


def variants(name: str) -> list[str]:
    """Spacing and punctuation variants STT plausibly emits for a canonical name."""
    out = set()
    low = name.lower()
    out.add(low)
    if " " in low:
        out.add(low.replace(" ", ""))
        out.add(low.replace(" ", "-"))
    if "-" in low:
        out.add(low.replace("-", " "))
        out.add(low.replace("-", ""))
    # Split camel-ish compounds: "Foxparks" -> "fox parks" is handled by seeds, but
    # multi-word forms benefit from their parts being searchable.
    out.discard(low)
    return sorted(out)


def build(version: str) -> dict:
    names_path = RAW / "pal_names_flat.json"
    if not names_path.exists():
        sys.exit(f"missing {names_path} - run the pak extractor first")

    raw_names = json.loads(names_path.read_text(encoding="utf-8"))

    # A canonical display name maps to MANY internal ids: event and quest variants
    # (Horus / Horus_Oilrig, SUMMON_*, Quest_*) all share one player-facing name.
    # Collapsing to a single id would silently drop rows on any later join.
    by_name: dict[str, list[str]] = {}
    dropped: list[str] = []

    for row in raw_names:
        key, name = row.get("key", ""), (row.get("name") or "").strip()
        if name in PLACEHOLDERS or not name or name.startswith("PAL_NAME"):
            dropped.append(key)
            continue
        internal = key[len("PAL_NAME_"):] if key.startswith("PAL_NAME_") else key
        by_name.setdefault(name, []).append(internal)

    # Join to the parameter table for Paldeck membership, so downstream code can tell
    # a real Pal from a summon or quest actor without re-reading the raw tables.
    params = {}
    param_path = RAW / "pal_monster_parameter.json"
    if param_path.exists():
        params = json.loads(param_path.read_text(encoding="utf-8")).get("Rows", {})

    pals: list[dict] = []
    for name, ids in by_name.items():
        zukan = [params[i]["ZukanIndex"] for i in ids
                 if i in params and isinstance(params[i].get("ZukanIndex"), int)
                 and params[i]["ZukanIndex"] > 0]
        pals.append({
            "canonical": name,
            "internal_ids": sorted(ids),
            "zukan_index": min(zukan) if zukan else None,
            "in_paldeck": bool(zukan),
            "aliases": sorted(set(safe_aliases(SEED_ALIASES.get(name, []) + variants(name)))),
            "phonetic": metaphone_key(name),
        })

    # The nine tower leaders, as a THIRD entity kind rather than as aliases of the Pals
    # they fight with. Aliasing was the shorter route and it destroys the thing the
    # feature is for: "Victor" would collapse to "Shadowbeak" during ranking, and by the
    # time the counter branch saw it there would be no way to tell the tower fight from
    # the field alpha of the same species - which is a different creature at a different
    # level. `Candidate.kind` already carries "pal" and "resource"; a third value is
    # inert everywhere that checks for those two and visible where it is asked for.
    #
    # Note the pairs are ALREADY in `pals` above, as "Victor & Shadowbeak" - that is a
    # PAL_NAME_ row and this function does not filter it out. The leader entry is what
    # makes the human half addressable on its own, which is how players actually speak.
    #
    # No seeded manglings. Every alias in this file above was harvested from a recording
    # of this speaker; none of these names has ever been recorded, and inventing what
    # STT might do to "Bjorn" would put a guess in the same list as nine measured facts.
    # They grow from observed failures like everything else here.
    #
    # `safe_aliases`' four-character floor is deliberately NOT applied. It exists to stop
    # short homophone guesses ("or" for "ore") matching half the corpus, and "Zoe" is not
    # a guess - it is the game's spelling of a proper noun. Measured before adding:
    # across the 271 A5 transcripts the highest score any of them reaches against an
    # unrelated fragment is 0.667 ("one" -> zoe, "wally" -> lily, "magics" -> marcus),
    # well under both MIN_CONFIDENT (0.78) and PAL_CONFIDENT (0.85). None of them can
    # claim a query on that corpus.
    leaders = [{
        "canonical": lead.leader,
        # Which tower Pal this human fights alongside. Carried for readability and for
        # the boss join to be checkable by eye; the authoritative link to a character id
        # is made in build_bosses.py, which is where the derivation is declared.
        "pal": lead.pal,
        "region": lead.region,
        "aliases": sorted(set(variants(lead.leader))),
        "phonetic": metaphone_key(lead.leader),
    } for lead in _leaders.parse(RAW)]

    _, display = derive()
    resource_names = {**display, **UNPLACED_RESOURCES}
    resources = [{
        "canonical": c,
        "display": resource_names[c],
        "aliases": sorted(set(safe_aliases(
            RESOURCE_ALIASES.get(c, [])
            + ([c.replace("_", " ")] if "_" in c else [])
            + ([resource_names[c].lower()] if resource_names[c].lower() != c else [])))),
        "phonetic": metaphone_key(c.replace("_", " ")),
    } for c in sorted(resource_names)]

    return {
        "lexicon_version": 1,
        "game_version": version,
        "source": "DT_PalNameText_Common (en), extracted from Pal-Windows.pak",
        "notes": (
            "Aliases are seeded by hand and grow from observed STT failures. "
            "Matches below the confidence threshold are never silently coerced - "
            "the card names the unrecognized token instead."
        ),
        "leaders_note": (
            "The nine tower leaders. STATED by pal_names_flat.json, whose PAL_NAME_"
            "<Region>Boss rows read 'Victor & Shadowbeak' in one string, and "
            "independently confirmed by DT_UniqueNPCText's BOSSNAME_DEMO_<REGION>_"
            "LEADER / _LEADER_PAL pairs; the two agree on all eight they share and "
            "build_bosses.py fails if they stop. A THIRD entity kind, not aliases of "
            "the Pals they fight with: collapsing Victor into Shadowbeak during ranking "
            "would lose the difference between the tower fight and the field alpha of "
            "the same species. See tools/ingest/_leaders.py."
        ),
        "stats": {
            "pals": len(pals),
            "pals_in_paldeck": sum(1 for p in pals if p["in_paldeck"]),
            "internal_ids_mapped": sum(len(p["internal_ids"]) for p in pals),
            "resources": len(resources),
            "leaders": len(leaders),
            "dropped_placeholder_rows": len(dropped),
            "seeded_alias_entries": sum(1 for p in pals if p["canonical"] in SEED_ALIASES),
        },
        "dropped_keys": sorted(dropped),
        "pals": sorted(pals, key=lambda p: p["canonical"]),
        "resources": resources,
        "leaders": sorted(leaders, key=lambda l: l["canonical"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    lex = build(args.version)
    dest = REPO / "data" / args.version / "lexicon.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(lex, indent=2, ensure_ascii=False), encoding="utf-8")

    s = lex["stats"]
    print(f"lexicon -> {dest}")
    print(f"  pals              {s['pals']}")
    print(f"  resources         {s['resources']}")
    print(f"  tower leaders     {s['leaders']}"
          + (f"  ({', '.join(l['canonical'] + ' & ' + l['pal'] for l in lex['leaders'])})"
             if lex["leaders"] else "  - DT_UniqueNPCText not extracted"))
    print(f"  seeded aliases    {s['seeded_alias_entries']}")
    print(f"  dropped rows      {s['dropped_placeholder_rows']}")
    if lex["dropped_keys"]:
        print(f"  dropped: {', '.join(lex['dropped_keys'][:10])}"
              + (" ..." if len(lex["dropped_keys"]) > 10 else ""))


if __name__ == "__main__":
    main()
