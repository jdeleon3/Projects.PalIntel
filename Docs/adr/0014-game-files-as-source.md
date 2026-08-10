# ADR-0014 — Game files as the primary data source; community sites as validation

**Status:** Accepted
**Amends:** [03-data-ingestion.md](../03-data-ingestion.md) §3
**Evidence:** Phase 0.5 / 0.7 source survey

## Context

The ingestion spec originally assumed community interactive maps and wikis were the only
practical origin for structured game data, and flagged licensing as a live risk
(assumption A7) since scraping a fan site for redistribution is legally ambiguous.

The Phase 0.5 / 0.7 survey found that assumption to be wrong in a useful direction.

**1. The community sites are themselves downstream of PAK extraction.** The leading
interactive map states its marker coordinates are sourced from PAK extraction of the game's
own files and verified against in-game tile coordinates. Scraping them means taking a
lossy, unversioned copy of data we can obtain at the source.

**2. Mature MIT-licensed tooling exists for the extraction path:**

| Tool | Licence | Provides |
|---|---|---|
| `PalworldDataTools/PalworldDataExtractor` | MIT | Pal character parameters, **special breeding combinations**, localization, icons |
| `cheahjs/palworld-save-tools` | MIT | `.sav` ↔ JSON; characters, base camp locations, containers. PyPI, Python 3.9+, stdlib only |
| FModel + community Palworld mappings/AES keys | Community | General UE5.1 asset extraction — the path to level data and node placements |

**3. The licensing posture inverts.** Extracting data from a game you own, for a personal
tool, is a fundamentally better position than scraping and redistributing a fan site's
compiled dataset. It also removes our dependence on a third party's uptime, format
stability, and terms.

**4. Extraction is patch-refreshable on our schedule.** Community sites update when their
maintainers get to it; the `.pak` updates the moment the game does.

## Decision

**Game files are the primary source for all structured data.** Community sites are used for
**cross-validation and for data genuinely absent from extraction**, never as the origin
where extraction is possible.

| Dataset | Primary source | Cross-validation |
|---|---|---|
| Pal params, work suitability, base stats | `PalworldDataExtractor` (MIT) | paldb.cc, community wikis |
| Breeding combos + exceptions | `PalworldDataExtractor` (MIT) | Known-combo sample (≥ 100) |
| Resource node coordinates | FModel level-data extraction | Community map marker counts |
| Spawn zones | FModel level-data extraction | Community maps |
| Tech tree | FModel / data-table extraction | Community wikis |
| Element matrix | **Hand-entered**, unit tested | — |
| Base sites (Q4) | **Curated prose** — opinion, not extractable | — |
| Knowledge corpus (Q7) | Licensed community prose | — |

The last two rows are the genuine exceptions. Base-site quality is a judgment call that
does not exist in the game files, and Tier 3 prose is by definition community-written.
**Assumption A7's licensing risk therefore narrows to the Q7 corpus alone**, where it is
unavoidable — and where it can be managed by source selection rather than by hoping.

## Consequences

**Positive**
- Authoritative data, at the same source the community sites use
- Licensing risk removed from six of eight datasets; confined to the Q7 prose corpus
- No dependence on third-party uptime, schema stability, or terms changes
- Patch refresh runs on our schedule, gated only by the mapping file being updated
- **A3 becomes directly testable.** The extractor exposes "special breeding combinations"
  as a distinct dataset — precisely the exception table the rank model predicts. Its
  existence as a separate structure is corroborating evidence for the rank rule; whether
  `CombiRank` is present in character parameters converts A3 from statistical sampling into
  a definitive check.

**Negative**
- Higher upfront cost than scraping: FModel setup, mapping file, AES key, and learning the
  asset layout
- Node placements live in level data, which is less structured than the tidy data tables —
  this is the real work in Phase 0.5, and the reason A4 remains the v1 gate
- Community mapping files and AES keys lag new patches slightly, so extraction may be
  briefly blocked after an update. Mitigated by the previous version's data staying published.
- Requires a local game install as a build-time dependency of ingestion (not of the runtime)

**Neutral**
- Extraction tooling stays out of the runtime process, unchanged from
  [03-data-ingestion.md](../03-data-ingestion.md) §8. The bot loads published data and has
  no extraction capability.
- Nothing here is distributed. Extracted data supports a personal tool; it is not
  republished as a dataset.
