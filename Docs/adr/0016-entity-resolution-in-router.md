# ADR-0016 — Entity resolution belongs in the router, not the corrector

**Status:** Accepted
**Amends:** [ADR-0007](0007-entity-lexicon-boundary.md)
**Evidence:** Phase 0.6, `data/stt_eval/quiet/` (40 prompts, 39 scored entities)

## Context

[ADR-0007](0007-entity-lexicon-boundary.md) placed three defences against entity
corruption: keyterm boosting, fuzzy repair, and enum constraints on the router. It also
put the **decline decision** in the fuzzy repair layer — matches below a confidence
threshold were not coerced, because answering confidently about the wrong entity is worse
than admitting the miss.

Measurement showed the principle was right and the placement was wrong.

**Threshold-and-decline scored 61.5%** on 39 entities (`medium.en`, quiet conditions),
far below the 95% target. The failure mode looked fatal: Whisper does not produce garbled
phonetics, it produces confident English. "Helzephyr" became *"health sphere"*, "Aegidron"
became *"the nurse? I grew down"*, "Cryolinx" became *"car links"*. Once an invented noun
is rendered as fluent English, edit distance and phonetic keys appear to have nothing left
to work with.

Ranking the full lexicon against each transcript instead of thresholding it told a
different story:

| | |
|---|---|
| correct entity ranked 1 | **79.5%** |
| top-3 | **89.7%** |
| top-10 | 94.9% |
| beyond top-10 | 2 of 39 |

Every example above — *health sphere*, *the nurse I grew down*, *car links*, *Lee's bunk* —
ranks the correct Pal **first**. They failed scoring because their similarity fell just
under the 0.78 threshold and the matcher refused to coerce.

**These were threshold rejections, not ranking failures.** The information was present the
whole time; the layer holding it simply lacked the standing to act on it.

## The layering error

The corrector sees a string. It does not know that a Pal is expected, that
*"against the first tower"* implies a combat matchup, or that *"how do I breed X"*
constrains X to a breedable species. It is asked to make a confidence judgement with the
least context of any component in the system.

The router has all of that, **and** it selects from a constrained enum — a forced choice
over 313 known names, where a threshold is not merely unhelpful but conceptually wrong.

## Decision

**Fuzzy repair stops declining. It emits a ranked candidate list, and the router decides.**

```
transcript
   → corrector: rank lexicon entities by phonetic + edit distance
                emit top-K candidates WITH scores  (no threshold, no rejection)
   → router:    choose using sentence context, constrained to the enum
                decline only when genuinely unsure
```

ADR-0007's core principle is **unchanged**: the system still never silently coerces a
low-confidence match, and still names the unrecognised token rather than guessing. The
decline decision simply moves to the layer that can make it well.

Concretely:
- The corrector's `MATCH_THRESHOLD` is removed from the production path. It stays in the
  evaluation tooling, where measuring the threshold-only baseline is still useful.
- The corrector supplies K candidates (K ≈ 5 given 89.7% top-3 and 94.9% top-10).
- Router tool schemas already constrain entity parameters to lexicon-generated enums, so
  no schema change is required.
- The router receives candidates as a hint, not a restriction — it may still decline.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Lower the threshold** | Trades misses for wrong entities. The precision metric added during this work immediately caught 9 spurious matches from a single bad alias (`"or"` for *ore*); loosening globally makes that worse, and a confident wrong entity is the failure ADR-0007 exists to prevent. |
| **Declare A5 failed, redesign entity handling** | The trigger this measurement was built to test. It would have fired on the 61.5% figure alone — and would have been wrong. The signal was never lost. |
| **Domain-adapted STT (fine-tune on Pal names)** | Substantial effort, and unnecessary if the router closes the gap. Remains the fallback if it does not. |
| **Keep declining in the corrector, add the router as a fourth layer** | Two components making the same judgement with different information. The corrector's decline would discard candidates the router could have resolved. |

## Consequences

**Positive**
- Uses information the architecture already had but was discarding
- Removes a tuned constant (0.78) from the production path in favour of a decision made
  with context
- Ceiling rises from 61.5% to a measured 89.7% top-3 availability
- Both layers now do what they are actually good at: the corrector ranks, the router
  decides

**Negative**
- Router accuracy becomes the binding constraint on entity extraction and is **not yet
  measured** — it needs a live model and belongs in Phase 1.
- Passing 5 candidates per entity adds prompt tokens and a little latency.
- The router now carries two jobs (intent classification *and* entity disambiguation),
  so its eval set must cover both.

**Neutral / open**
- **The 89.7% figure is a ceiling, not an achievement.** It is what a perfect chooser
  could reach given this ranking; a real model may mispick even at rank 1.
- The ranking experiment told the matcher an entity existed. The router must also decide
  *whether* one is present, which is untested.
- Frame words ("hey pal", "where can I find") were excluded when generating candidates —
  mild tuning to these templates. Production sees arbitrary phrasing.
- Two entities are unrecoverable at any layer: **Majex** (rank 69, heard as *"magics"*)
  and **Omascul** (rank 29, *"a Moscow"*). These are alias candidates or accepted misses.
