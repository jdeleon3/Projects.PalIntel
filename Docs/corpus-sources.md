# Community corpus sources — a register for review

*Surveyed 2026-08-12. **Nothing here has been ingested and nothing should be until you have
read it.** This is a register: what exists, what it is worth, what it would cost, and what
each site says about being read by a machine.*

## Why this register exists

The Q7 corpus is built entirely from the game's own text — 3,103 chunks, licence-clean,
[surveyed against all 532 pak tables](../tools/ingest/build_corpus.py). It has one honest
limit, recorded when it shipped:

> The game explains its mechanics and says nothing about playing well. This corpus answers
> *"how does the breeding farm work"* and cannot answer *"what is the best base layout"*.

**That second question is the whole of what this register is about.** Community consensus
on "bests", build strategies, breeding routes, boss approaches, base siting — the things no
amount of datamining produces, because they are the accumulated judgement of people who
played.

**Scope: the gap only.** Sources that mostly restate stats we already extract from the pak
are not assessed here; they carry licence risk and add no coverage. They are named in
[§Deliberately not assessed](#deliberately-not-assessed) so nobody re-surveys them.

---

## The rubric

Four axes, 1–5 each. **Authority is the unweighted mean, and it is deliberately separate
from value**, because the two come apart badly: a datamined database scores 5 on
provenance and is worth nothing to us, while a subreddit scores 2 and holds the only real
consensus in existence.

| Axis | 1 | 3 | 5 |
|---|---|---|---|
| **P — Provenance** | Asserted, no stated basis | Author's own play experience | Datamined, measured, or method published |
| **V — Verifiability** | Rankings with no reasoning | Reasoning given, unsourced | Per-claim evidence a reader can re-check |
| **C — Currency** | Undated or visibly stale | Dated, patch unclear | Patch-versioned and current (1.0.3) |
| **B — Consensus breadth** | One author | Small editorial team | Large multi-contributor or voted |

**Gap fit (G)** is separate and is the one that decides whether a source is worth the
trouble: how much of what it carries is content the pak cannot give us. 1 = a stat mirror,
5 = almost entirely judgement, strategy and consensus.

**Verdict** is my recommendation, not a decision:

- 🟢 **Candidate** — worth ingesting, subject to your licence call
- 🟡 **Review** — real value, a real problem; the problem is named
- 🔴 **Blocked** — the operator has said no in a machine-readable way
- ⚪ **Unassessed** — could not be fetched with this tooling

---

## The finding that matters most: licence and robots disagree

Two of the three highest-value sources carry a **Creative Commons licence that permits
reuse** *and* a **robots.txt that names ClaudeBot and forbids it.**

`palworld.wiki.gg` is CC BY-SA 4.0 — reuse permitted with attribution and share-alike —
and its robots.txt disallows `ClaudeBot`, `GPTBot`, `CCBot`, `PerplexityBot`,
`Google-Extended`, `Scrapy`, `wget` and a dozen others, with the content signal
`search=yes, ai-train=no, use=reference`. `palworldgame.wiki` carries the same signal and
the same ClaudeBot block. Game8 blocks `GPTBot` and `Google-Extended`.

**These are different claims and only you can decide which governs.** The licence is about
copyright; the robots policy is about access and the operator's stated wishes. They can
both be true and point opposite ways. Three defensible positions, none of which I am
taking for you:

1. **Robots governs.** If a site names the crawler and says no, it is a no — whatever the
   licence permits. This is the conservative reading and it removes 🔴 rows entirely.
2. **Licence governs for content, robots governs for crawling.** Content obtained without
   crawling — a database dump, a Special:Export, a manual copy — is reusable under CC
   BY-SA with attribution. Defensible for MediaWiki sites that publish dumps.
3. **Personal-use posture.** ADR-0014 already argued that extracting data from a game you
   own, for a local single-user tool, inverts the licensing calculus. Whether that argument
   stretches from a `.pak` you paid for to a website you did not is exactly where I would
   stop and ask.

**The register records the observation and does not resolve it.** Every 🔴 below is a
robots-based flag, not a licence judgement.

---

## Register — scoring

| # | Source | Type | P | V | C | B | **Auth** | **G** | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Palworld Companion** | Editorial guides + DB | 3 | 4 | 4 | 2 | **3.3** | **4** | 🟢 Candidate |
| 2 | **Game8** | Editorial guides + tier lists | 3 | 3 | 5 | 3 | **3.5** | **4** | 🟡 Review |
| 3 | **r/Palworld** | Forum / consensus | 2 | 2 | 4 | 5 | **3.3** | **5** | ⚪ Unassessed |
| 4 | **Steam Community discussions** | Forum / consensus | 2 | 2 | 4 | 5 | **3.3** | **5** | 🟡 Review |
| 5 | **palworld.wiki.gg** | CC wiki | 4 | 3 | 2 | 4 | **3.3** | **2** | 🔴 Blocked |
| 6 | **palworldgame.wiki** | Editorial guides | 3 | 3 | 4 | 2 | **3.0** | **4** | 🔴 Blocked |
| 7 | **PalHoller** | Computed tools + tier lists | 5 | 4 | 5 | 1 | **3.8** | **2** | 🟡 Review |
| 8 | **OP.GG Palworld** | DB + voted tier lists | 3 | 2 | 3 | 4 | **3.0** | **3** | 🟡 Review |
| 9 | **Mobalytics** | Editorial guides | 3 | 3 | ? | 3 | **3.0** | **4** | ⚪ Unassessed |
| 10 | **Palworld Fandom wiki** | CC wiki | 3 | 2 | 2 | 4 | **2.8** | **2** | ⚪ Unassessed |
| 11 | **PalSphere** | DB + tier lists | 2 | 1 | 4 | 2 | **2.3** | **3** | 🟡 Review |
| 12 | **palworld.gg** | DB + tools + tier list | 3 | 2 | 1 | 2 | **2.0** | **2** | 🟡 Review |
| 13 | **Breeding calculators** (grouped) | Derived tools | 4 | 3 | 3 | 1 | **2.8** | **1** | 🟡 Review |
| 14 | **Host/SEO blogs** (grouped) | Marketing content | 1 | 1 | 2 | 1 | **1.3** | **3** | 🔴 Avoid |
| 15 | **YouTube guide channels** | Video / transcripts | 2 | 2 | 4 | 3 | **2.8** | **5** | 🟡 Review |
| 16 | **Official patch notes** | First-party | 5 | 5 | 5 | — | **5.0** | **2** | 🟢 Candidate |

*Authority is the mean of P/V/C/B. `?` means the axis could not be observed and is excluded
from that row's mean. Official patch notes have no consensus-breadth axis — they are not
consensus, they are the thing consensus forms about.*

---

## Register — access, licence and feeds

| # | Source | URL | Licence as stated | Robots / AI stance | Sitemap | RSS | Fetchable now |
|---|---|---|---|---|---|---|---|
| 1 | Palworld Companion | `palworldcompanion.com` | "Community fan site", no reuse licence → all rights reserved | **Permissive** — only `/api/` and `/_next/` disallowed | ✅ `/sitemap.xml`, ~600 URLs (~40 guides) | — | ✅ |
| 2 | Game8 | `game8.co/games/Palworld` | © Pocketpair disclaimer; site prose ARR, ToS at `/terms` | Blocks **GPTBot**, **Google-Extended**, dotbot | ✅ `/sitemaps/sitemap.xml.gz` | — | ✅ |
| 3 | r/Palworld | `reddit.com/r/Palworld` | User content under Reddit's ToS; bulk reuse restricted | Reddit blocks unauthenticated crawling | — | Per-sub `.rss` exists | ❌ needs Reddit API |
| 4 | Steam Community | `steamcommunity.com/app/1623730/discussions` | User content under Steam Subscriber Agreement | Permits `/app/`; blocks only trade/email/actions | ❌ none | — | ✅ |
| 5 | palworld.wiki.gg | `palworld.wiki.gg` | **CC BY-SA 4.0** (stated in footer) | **Blocks ClaudeBot**, GPTBot, CCBot, Scrapy, wget; `ai-train=no` | ✅ `/sitemaps/sitemap-index-palworld_en.xml` | — | ✅ but see stance |
| 6 | palworldgame.wiki | `palworldgame.wiki` | © 2026 Palworld Wiki, ToS present, no reuse grant | **Blocks ClaudeBot**, GPTBot, CCBot, Bytespider; `ai-train=no` | ✅ `/sitemap.xml` | — | ✅ but see stance |
| 7 | PalHoller | `palholler.com` | © 2026 PalHoller; not affiliated with Pocketpair; no reuse grant | **Allow: /** for all agents | ✅ `/sitemap.xml` | — | ✅ |
| 8 | OP.GG | `op.gg/palworld` | ARR, no reuse grant | Permissive except query-string paths | ✅ `/sitemap.xml` | — | ✅ |
| 9 | Mobalytics | `mobalytics.gg/gamebase/guides/palworld-*` | not observed | not observed | not observed | — | ❌ HTTP 403 |
| 10 | Palworld Fandom | `palworld.fandom.com` | CC BY-SA (Fandom default) | Fandom-standard | Fandom-standard | — | ❌ HTTP 402 |
| 11 | PalSphere | `palsphere.app` | "Fan-made Palworld database", no licence | not observed | not observed | — | ✅ |
| 12 | palworld.gg | `palworld.gg` | Not affiliated with Pocketpair; no licence | not observed | not observed | — | ✅ |
| 13 | Breeding calculators | `palholler.com`, `palmods.gg`, `palworldbreedingcalc.com`, `thepalworldbreedingcalculator.com`, `wikily.gg/palworld`, `palworldguides.com` | none state a reuse licence | varies | varies | — | ✅ |
| 14 | Host/SEO blogs | `bisecthosting.com/blog`, `xgamingserver.com/blog`, `driffle.com/blog`, `sportskeeda.com` | ARR | varies | varies | Some have RSS | ✅ |
| 15 | YouTube channels | `youtube.com` | ARR; transcripts are derivative | YouTube ToS forbids scraping | — | Channel RSS exists | ⚠ ToS |
| 16 | Official patch notes | Steam news for app `1623730`; `pocketpair.jp` | First-party, ARR | Steam news is API-accessible | — | Steam news API | ⚠ `pocketpair.jp` returned empty |

*"Fetchable now" means with the tooling used for this survey. It is not a permission
statement — rows 5 and 6 are fetchable and their operators have said not to.*

---

## What each source would actually be worth

**1. Palworld Companion — the strongest fetchable candidate.** Structured guides with
headings, a "quick recommendations" table per section, and — unusually — **stated
reasoning before the rankings**. Its base-siting guide gives four criteria (flat terrain,
resource density, raid safety, water access) and then justifies each coordinate against
them: *"(192, 38) mountain peak — 5-6 ore nodes + 5-6 coal nodes in a single base radius.
Non-raidable due to the elevation."* That is exactly the layer sitting on top of what Q4
already computes, and it is the layer Q4 explicitly cannot supply. Dated `2026-07-17`,
1.0-tagged, ~40 guide URLs in the sitemap, robots permissive. It cites 12+ outlets but does
not trace individual claims, so `V=4` not 5.

**2. Game8 — the biggest and the most current, with an AI-crawler block.** 70–75% written
guides and tier lists, `Last updated: 2026-08-03`, tracking patch 1.0.3. Coverage is
unmatched: breeding strategy, combat/support/mount tier lists, boss walkthroughs, location
guides. But it blocks GPTBot and Google-Extended, which is not a ClaudeBot block and is
plainly the same intent. 🟡 because the value is high enough that the question is worth
putting to you rather than answering for you.

**3. r/Palworld — the only real consensus, and the hardest to get.** Everything above is
one team's editorial voice; a subreddit is thousands of players disagreeing in public,
which is the actual signal for "what do people think is best". Vote counts are a
consensus measure nothing else here has. Not fetchable with this tooling — it needs the
Reddit API, which means credentials and rate limits. **Also the worst signal-to-noise
ratio in the register**: most of the volume is screenshots and jokes, so ingestion would
need filtering by flair, score and comment depth before a single chunk was written.

**4. Steam Community discussions — the same consensus, actually crawlable.** `robots.txt`
permits `/app/`, which makes it the accessible half of what makes Reddit valuable. Older,
slower and less voted, but the boss-strategy threads that surfaced in this survey are
substantive. No sitemap, so discovery would be by thread listing.

**5. palworld.wiki.gg — the licence you would want, the stance you would not.** CC BY-SA
4.0, MediaWiki (so `api.php` and `Special:Export` exist and dumps are conventional), 2,969
articles, 152 active editors. Two problems. The robots block is explicit and names
ClaudeBot. And **currency is doubtful**: the main article showed patch 0.6.9 and a last
edit of August 2024, while the game is on 1.0.3 — per-article currency is unverified but
the front page being two years stale is not a good sign. Gap fit is low anyway: a wiki is
mostly the stat tables we already extract.

**6. palworldgame.wiki — good prose, unambiguous refusal.** Author-written strategy with
real editorial judgement (*"Group structures by function rather than aesthetics"*), dated
August 2026, version-referenced, sitemap present. And `ClaudeBot: Disallow: /` with
`ai-train=no, use=reference`. There is no reading of that which needs interpreting.

**7. PalHoller — authoritative and largely redundant.** Explicitly *"computed from the
game's own data files, which we re-import after each patch"* — the highest provenance in
the register, and robots allow-all. But that means it is **derived from the same pak we
already read**, so its tier lists duplicate a capability rather than adding one. The value
that is genuinely ours to gain is the *derivations we have not built*: breeding path
finding, workforce optimisation, mutation tracking. That is a case for building those
features, not for ingesting prose.

**8. OP.GG — voted tier lists.** Users build and share tier lists, which is a consensus
mechanism rather than an editorial opinion. Worth something specifically for "what do
people rank highest", if the aggregate is exposed rather than just the builder.

**11–13. PalSphere, palworld.gg, the breeding calculators.** Low value for this purpose.
They are databases and calculators: the derived numbers we either have or could compute,
wrapped in interfaces. PalSphere does not say where its "official rankings" come from,
which is `V=1`.

**14. Host and SEO blogs — actively negative.** BisectHosting, XGamingServer, Driffle,
Sportskeeda: server-hosting marketing and volume content. Assertions with no method, often
recycled from the sites above, frequently stale. Ingesting these would put laundered
second-hand opinion into a corpus whose entire promise is that every line carries a source.

**15. YouTube guide channels — the largest strategy corpus in existence, and the least
usable.** Transcripts are auto-generated, unstructured, unpunctuated and full of
sponsorship reads. ToS forbids scraping. High gap fit, very high cost.

**16. Official patch notes — not community, and belongs here anyway.** First-party, so
`P=V=C=5`. Two uses: they explain mechanics changes the help guide never updates, and they
**date the rest of the corpus** — a strategy guide written before 1.0 may be describing a
game that no longer exists, and a patch-note timeline is what makes that checkable. Steam
news for app `1623730` is API-accessible.

---

## Deliberately not assessed

Stat mirrors — sites whose content is the data we already extract from the pak. Named so
the next survey does not repeat this one:

| Source | Why not | Already covered by |
|---|---|---|
| `paldb.cc` | Datamined stat tables, v1.0.3, robots allow-all, sitemap present. Genuinely good, and it is our own datasets with a different front end | `pal_spawns.json`, `pal_drops.json`, `work.json`, `elements.json` |
| Pal/item/skill database pages on any site above | Same | the pak ingests |
| Interactive maps | Coordinates | `resource_nodes.json`, `pal_spawns.json`, the map crops |

One exception worth naming: **ranch production is still the project's only
community-sourced dataset** ([ADR-0014](adr/0014-game-files-as-source.md)'s amendment), and
STATUS lists finding an authoritative source for it as an open backlog item. If any survey
of these mirrors is worth doing, it is that one — and it is a *data* question, not a corpus
one.

---

## How this was assessed, and what that is worth

Every licence, robots and sitemap field above was **read from the site on 2026-08-12** and
is an observation. Every score is **my judgement** and is not.

Four limits, stated rather than buried:

- **Three sources could not be fetched** — Fandom (HTTP 402), Mobalytics (403), Reddit
  (blocked). Their rows are marked ⚪ and their scores are inferred from search results, so
  treat them as placeholders.
- **Robots and licences change.** This is a snapshot. Anything acted on should be
  re-checked at the time, and re-checked again at each Palworld patch, the same way the
  DAVE upstream issue is re-run.
- **Currency was read from front pages**, not sampled per article. `palworld.wiki.gg` may
  well have current articles behind a stale main page.
- **No page was assessed for chunk quality**, which is a depth question this breadth
  survey deliberately did not answer. Before ingesting anything, pull three real articles
  and run them through `build_corpus.py`'s cleaning to see what survives.

---

## What to decide, in order

1. **Does a robots.txt naming ClaudeBot settle it?** Answering this once removes or
   restores rows 2, 5 and 6 — the three highest-coverage sources in the register — and
   nothing else can be sequenced until it is answered.
2. **If you want to proceed, Palworld Companion is the one to try first.** Permissive
   robots, structured prose, stated reasoning, dated, sitemapped, ~40 guide URLs. Small
   enough to ingest by hand and measure honestly against the lexical baseline.
3. **Official patch notes are worth taking regardless.** First-party, API-accessible, and
   they are what lets any community chunk carry an *"as of patch X"* that a reader can act
   on.
4. **Every community chunk needs a provenance field the pak chunks do not.** The current
   corpus is one kind of thing and its citations say `Help guide: Sanity`. A mixed corpus
   needs the card to distinguish *"the game says"* from *"players generally say"*, because
   those are different claims and the whole value of Tier 3 is that a citation means
   something. That is a card and schema change, not just an ingest.
5. **And it needs a decision about what happens when they disagree.** The game's help text
   and community consensus will contradict each other — that is often *why* the consensus
   exists. Which one a card leads with is a design decision that does not exist today.
