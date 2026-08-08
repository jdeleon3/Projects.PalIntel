# Architecture Decision Records

Each ADR records one decision: the context that forced it, the alternatives considered, and
the consequences accepted. Several **reverse** the original `HighLevel.txt` sketch — read
the relevant ADR before proposing a change that reintroduces a discarded approach.

| # | Decision | Status |
|---|---|---|
| [0001](0001-drop-vector-search-premise.md) | Drop vector search and the RAG premise; local structured store | Accepted — *amended by 0011* |
| [0002](0002-llm-as-router.md) | LLM routes; deterministic code answers | Accepted — *amended by 0010* |
| [0003](0003-local-first-process.md) | Single long-lived local process, not serverless | Accepted |
| [0004](0004-wake-word-activation.md) | Wake-word activation over continuous transcription | Accepted |
| [0005](0005-save-file-player-state.md) | Read player state from the local save file | Accepted |
| [0006](0006-templated-cards.md) | Templated cards, not LLM-generated | Accepted |
| [0007](0007-entity-lexicon-boundary.md) | One entity lexicon serving three consumers | Accepted |
| [0008](0008-breeding-graph-derivation.md) | Derive the breeding graph from combination rank | **Provisional** — pending A3 |
| [0009](0009-v1-vertical-slice.md) | Q1 resource lookup as the v1 slice | Accepted |
| [0010](0010-three-tier-answer-model.md) | Three-tier answer model (fact / computed advice / open knowledge) | Accepted |
| [0011](0011-corpus-grounded-knowledge.md) | Corpus-grounded general knowledge; cite or decline | Accepted |
| [0012](0012-dual-input-channels.md) | Voice and text share one pipeline | Accepted |
| [0013](0013-conversation-memory.md) | Short per-user conversation memory | Accepted |

## Amendment chain

Two decisions were amended rather than reversed when scope expanded from four query classes
to seven. The distinction matters — the original reasoning still holds within its original
scope:

- **0001 → 0011.** Vector search returns for Q7 only, locally, because that corpus is
  genuinely unstructured. Structured classes remain deterministic.
- **0002 → 0010.** The fact/generative binary becomes three tiers. The invariant that
  coordinates, stats, and breeding pairs never originate from a model is untouched.
