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
    "Lifmunk": ["life monk", "lif munk", "liftmunk", "lifmonk", "live monk"],
    "Jormuntide": ["jormun tide", "your mun tide", "jorman tide", "jormuntied"],
    "Depresso": ["depress oh", "de presso", "espresso", "depresa"],
    "Chillet": ["chill it", "chilet", "shall it", "chillette"],
    "Faleris": ["fal aris", "phaleris", "valeris", "feleris"],
    "Digtoise": ["dig toise", "dig tortoise", "digtois", "dictoise"],
    "Foxparks": ["fox parks", "fox sparks", "foxpark"],
    "Pengullet": ["pen gullet", "penguin let", "pengulet"],
    "Tanzee": ["tan zee", "tansy", "tanzy"],
    "Cattiva": ["cat eva", "cateva", "kativa"],
    "Lamball": ["lamb ball", "lambhall", "lam ball"],
    "Nitewing": ["night wing", "nitewin", "knight wing"],
    "Incineram": ["incinerate", "in cinerham", "incineran"],
    "Anubis": ["anubus", "a newbis"],
    "Grizzbolt": ["grizz bolt", "grizzly bolt", "gris bolt"],
}

# Resource vocabulary for Q1. Small, closed, and hand-maintained: these are ordinary
# English words whose STT failure modes are homophones rather than novel morphology.
RESOURCES: dict[str, list[str]] = {
    "ore": ["oar", "or", "awe"],
    "coal": ["cole", "kohl", "call", "coel"],
    "sulfur": ["sulphur", "sulfa", "sulfer"],
    "quartz": ["quarts", "courts", "kwartz"],
    "crude_oil": ["crude oil", "oil", "cruel oil"],
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
            "aliases": sorted(set(SEED_ALIASES.get(name, []) + variants(name))),
            "phonetic": metaphone_key(name),
        })

    resources = [{
        "canonical": c,
        "aliases": sorted(set(a + ([c.replace("_", " ")] if "_" in c else []))),
        "phonetic": metaphone_key(c.replace("_", " ")),
    } for c, a in RESOURCES.items()]

    return {
        "lexicon_version": 1,
        "game_version": version,
        "source": "DT_PalNameText_Common (en), extracted from Pal-Windows.pak",
        "notes": (
            "Aliases are seeded by hand and grow from observed STT failures. "
            "Matches below the confidence threshold are never silently coerced - "
            "the card names the unrecognized token instead."
        ),
        "stats": {
            "pals": len(pals),
            "pals_in_paldeck": sum(1 for p in pals if p["in_paldeck"]),
            "internal_ids_mapped": sum(len(p["internal_ids"]) for p in pals),
            "resources": len(resources),
            "dropped_placeholder_rows": len(dropped),
            "seeded_alias_entries": sum(1 for p in pals if p["canonical"] in SEED_ALIASES),
        },
        "dropped_keys": sorted(dropped),
        "pals": sorted(pals, key=lambda p: p["canonical"]),
        "resources": resources,
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
    print(f"  seeded aliases    {s['seeded_alias_entries']}")
    print(f"  dropped rows      {s['dropped_placeholder_rows']}")
    if lex["dropped_keys"]:
        print(f"  dropped: {', '.join(lex['dropped_keys'][:10])}"
              + (" ..." if len(lex["dropped_keys"]) > 10 else ""))


if __name__ == "__main__":
    main()
