"""Build the patch-note dataset — first-party, from Steam's news API.

Input : the Steam news API for app 1623730, cached at data/raw/steam_news.json
Output: data/<version>/patch_notes.json

**The first dataset in this project that does not come from the pak**, and the first that
needs the network. Both are worth stating rather than sliding past.

It is still first-party: `steam_community_announcements` is Pocketpair posting to their
own Steam page, so this is the developers describing their own changes. That is the same
provenance class as the help guide, arriving by a different road. The 132 press articles
in the same feed - PC Gamer, PCGamesN, VG247 - are third-party and are **excluded**.

The network call is an *ingest-time* fetch, not a runtime one. `--refresh` writes
`data/raw/steam_news.json` and every other run reads that file, so a rebuild needs no
network and [ADR-0003](../../Docs/adr/0003-local-first-process.md)'s local-first runtime is
untouched.

## Why patch notes are worth having at all

Two reasons, and the second is the one that matters:

1. They explain changes the in-game help never updates. The help guide describes the game
   in general; a patch note says Jetragon's technologies moved from level 79 to 70.
2. **They date everything else.** A strategy guide written before 1.0 may be describing a
   game that no longer exists, and a patch timeline is what makes that checkable. If the
   community corpus in `corpus-sources.md` is ever taken, this is the dataset that lets a
   card say "as of the patch that guide predates".

## The filter, which was measured rather than assumed

Steam tags 19 of the 115 first-party posts `patchnotes`. **Filtering on the tag alone
drops 40 real patch notes, including "Palworld v1.0 - Official Release Changelog"** - the
single most important one there is. That is the "I searched for it is only as strong as
the term searched for" failure, arriving for the third time in this repository.

So a post is a patch note when it is first-party **and** carries the tag **or** names a
version in its title **and** has a changelog-shaped body. The last clause exists because
the version pattern alone also catches "Palworld 1.0 Official Launch Trailer is OUT!",
which is an announcement. Every count is printed, so a rule that starts sweeping in
marketing is visible rather than silent.

Usage:
    python tools/ingest/build_patch_notes.py --refresh --version 1.0.2   # fetch, then build
    python tools/ingest/build_patch_notes.py --version 1.0.2             # build from cache
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

APP_ID = 1623730
NEWS_URL = ("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
            f"?appid={APP_ID}&count=500&maxlength=0&format=json")

# Pocketpair posting to their own Steam page. Everything else in this feed is a press
# outlet syndicated into it, which is third-party content with a licence question and no
# claim to describe the game authoritatively.
FIRST_PARTY = "steam_community_announcements"

# A version in the title: v1.0.3, 0.4.11, "Ver 1.0". Also the words a changelog uses.
VERSION_IN_TITLE = re.compile(
    r"\bv(?:er)?\.?\s?\d+\.\d+(?:\.\d+)*\b|\b\d+\.\d+(?:\.\d+)+\b"
    r"|changelog|patch note|hotfix", re.I)

# What a changelog body looks like. Pocketpair use these two markers for sections and
# bullets in every patch note and in none of the sale announcements.
CHANGELOG_BODY = re.compile(r"[▼・]|\[h3\]|\bbug fix|\bfixed an issue|\badjust", re.I)

# The version the post is about, for ordering and for dating other datasets.
VERSION = re.compile(r"v(?:er)?\.?\s?(\d+\.\d+(?:\.\d+)*)", re.I)

# Steam BBCode. `[p]` is a paragraph, `[h3]` a section heading, and the rest is styling,
# links and embeds that carry nothing once the text is extracted.
_PARA = re.compile(r"\[/?p\]", re.I)
_HEAD = re.compile(r"\[h(\d)\](.*?)\[/h\1\]", re.I | re.S)
# `[*]` opens a list item and `[/*]` closes it. The closer is NOT matched by the general
# tag pattern below - `[/` followed by `*` is not `[/` followed by a letter - and 25
# chunks shipped with a bare `[/*]` in the middle of a sentence before that was noticed.
_LIST_ITEM = re.compile(r"\[/?\*\]", re.I)
_ANY_TAG = re.compile(r"\[/?[a-z][^\]]{0,200}\]", re.I)
_HTML_TAG = re.compile(r"<[^>]{0,300}>")

# Steam escapes literal brackets in post text as `\[Patch Notice]`. Those have to be
# protected BEFORE tag stripping, or the tag pattern eats the bracketed phrase and leaves
# the backslash behind - which is how every v1.0.3 section came out titled `\`.
_ESC_OPEN, _ESC_CLOSE = "\x00", "\x01"

# Sections a card should never quote: the boilerplate every post ends with.
BOILERPLATE = re.compile(
    r"^\s*(?:follow us|join our|official (?:site|website|discord|twitter)"
    r"|palworld official|©|report a bug|contact)", re.I)

# Below this a "section" is a heading with nothing under it.
MIN_CHARS = 60


def fetch() -> list[dict]:
    with urllib.request.urlopen(NEWS_URL, timeout=60) as response:
        return json.load(response)["appnews"]["newsitems"]


def clean(body: str) -> str:
    """Steam BBCode to plain text, keeping paragraph and heading structure."""
    # Protect escaped literal brackets first - see _ESC_OPEN.
    text = body.replace("\\[", _ESC_OPEN).replace("\\]", _ESC_CLOSE)
    text = _HEAD.sub(lambda m: f"\n\n## {m.group(2).strip()}\n", text)
    text = _PARA.sub("\n", text)
    text = _LIST_ITEM.sub("\n- ", text)
    text = _ANY_TAG.sub("", text)
    text = _HTML_TAG.sub("", text)
    text = text.replace(_ESC_OPEN, "[").replace(_ESC_CLOSE, "]")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def sections(text: str) -> list[tuple[str | None, str]]:
    """Split a cleaned note on its own headings.

    Sectioned rather than windowed, per the data model: a patch note already carries
    "Game Balance and System Adjustments" and "Bug Fixes" as headings, and respecting
    them keeps a chunk self-contained and its citation meaningful.
    """
    parts = re.split(r"\n## (.+)\n", "\n" + text)
    out: list[tuple[str | None, str]] = []
    if parts[0].strip():
        out.append((None, parts[0].strip()))
    for i in range(1, len(parts) - 1, 2):
        # Pocketpair bracket their own section names - `\[Game Balance and System
        # Adjustments]` - so the restored literal brackets come through into the heading
        # and read as markup on a card.
        out.append((parts[i].strip().strip("[]●▼ ").strip(), parts[i + 1].strip()))
    return out


def is_patch_note(item: dict) -> bool:
    if item.get("feedname") != FIRST_PARTY:
        return False
    if "patchnotes" in (item.get("tags") or []):
        return True
    return bool(VERSION_IN_TITLE.search(item.get("title", ""))
                and CHANGELOG_BODY.search(item.get("contents", "")))


def build(version: str) -> dict:
    items = json.loads((RAW / "steam_news.json").read_text(encoding="utf-8"))
    first_party = [i for i in items if i.get("feedname") == FIRST_PARTY]
    tagged = [i for i in first_party if "patchnotes" in (i.get("tags") or [])]
    notes = [i for i in items if is_patch_note(i)]

    entries, chunks = [], []
    for item in sorted(notes, key=lambda i: -int(i.get("date", 0))):
        body = clean(item.get("contents", ""))
        if not body:
            continue
        m = VERSION.search(item.get("title", ""))
        game_version = m.group(1) if m else None
        stamp = time.strftime("%Y-%m-%d", time.gmtime(int(item["date"])))
        entries.append({
            "gid": item["gid"],
            "title": item["title"],
            "version": game_version,
            "date": stamp,
            "url": item.get("url", ""),
            "tagged": "patchnotes" in (item.get("tags") or []),
        })
        for heading, text in sections(body):
            if len(text) < MIN_CHARS or BOILERPLATE.match(text):
                continue
            # `v` only when there IS a version. A post with none is titled by its date,
            # and "v2026-07-30" reads as a version number that does not exist.
            label = f"v{game_version}" if game_version else stamp
            chunks.append({
                "chunk_id": f"patch:{item['gid']}:{len(chunks)}",
                # A citation reads "Patch notes: v1.0.3 - Bug Fixes", which is a claim a
                # reader can date and go and check against the URL.
                "title": label + (f" - {heading}" if heading else ""),
                "section": "Patch notes",
                "text": text,
                "source_key": item["gid"],
                "source_table": "steam_news",
                "version": game_version,
                "date": stamp,
                "url": item.get("url", ""),
            })

    versions = [e["version"] for e in entries if e["version"]]
    return {
        "dataset_version": 1,
        "game_version": version,
        # NOT "pak". The first dataset here that comes from somewhere else, and the field
        # exists so a card and a reader can tell.
        "provenance": "steam_news",
        "source": f"Steam news API, app {APP_ID}, feed {FIRST_PARTY} only",
        "first_party_note": "steam_community_announcements is Pocketpair posting to "
                            "their own Steam page - the developers describing their own "
                            "changes. The 132 press articles syndicated into the same "
                            "feed (PC Gamer, PCGamesN, VG247) are third-party and are "
                            "excluded.",
        "network_note": "This is an INGEST-time fetch cached at data/raw/steam_news.json. "
                        "Every build but --refresh reads the cache, so ADR-0003's "
                        "local-first runtime is untouched.",
        "filter_note": f"Steam tags only {len(tagged)} of {len(first_party)} first-party "
                       f"posts 'patchnotes', and filtering on the tag alone drops 40 real "
                       f"patch notes INCLUDING the v1.0 Official Release Changelog. A "
                       f"post qualifies on the tag, or on a version in its title plus a "
                       f"changelog-shaped body - the second clause is what keeps "
                       f"'1.0 Official Launch Trailer is OUT!' from counting as a patch.",
        "dating_note": "The reason this is worth having beyond its own content: a patch "
                       "timeline is what makes a strategy claim checkable. A guide "
                       "written before 1.0 may describe a game that no longer exists.",
        "stats": {
            "news_items": len(items),
            "first_party": len(first_party),
            "tagged_patchnotes": len(tagged),
            "kept": len(entries),
            "kept_without_a_tag": sum(1 for e in entries if not e["tagged"]),
            "chunks": len(chunks),
            "characters": sum(len(c["text"]) for c in chunks),
            "newest": entries[0]["date"] if entries else None,
            "oldest": entries[-1]["date"] if entries else None,
            "versions": len(set(versions)),
        },
        "entries": entries,
        "chunks": chunks,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    ap.add_argument("--refresh", action="store_true",
                    help="fetch from the Steam news API and re-cache data/raw")
    args = ap.parse_args()

    cache = RAW / "steam_news.json"
    if args.refresh or not cache.exists():
        if not args.refresh:
            print(f"no {cache} - fetching once", file=sys.stderr)
        items = fetch()
        cache.write_text(json.dumps(items, indent=2, ensure_ascii=False),
                         encoding="utf-8")
        print(f"fetched {len(items)} news items -> {cache}")

    data = build(args.version)
    s = data["stats"]
    if not s["kept"]:
        sys.exit("ABORT: no patch notes matched. The feed's shape has changed and "
                 "nothing was written.")

    dest = REPO / "data" / args.version / "patch_notes.json"
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"patch notes -> {dest}")
    print(f"  news items       {s['news_items']}  ->  {s['first_party']} first-party")
    print(f"  kept             {s['kept']} notes across {s['versions']} versions"
          f"   ({s['oldest']} .. {s['newest']})")
    print(f"  of those         {s['tagged_patchnotes']} were tagged, "
          f"{s['kept_without_a_tag']} were NOT and would have been lost to the tag alone")
    print(f"  chunks           {s['chunks']}  ({s['characters'] // 1000}k characters)")


if __name__ == "__main__":
    main()
