"""Tier 3 lookup — the game's own prose, retrieved and quoted, never rewritten.

[ADR-0011](../Docs/adr/0011-corpus-grounded-knowledge.md) requires that a Tier 3 answer be
grounded in a retrieved source, carry a citation, and decline when nothing clears the
relevance bar. This module does all three, and does one thing the ADR's design did not
ask for and one thing it did ask for and is deliberately not doing yet.

## What it does that the design did not ask for: it quotes

The design says "grounded synthesis with mandatory citation" - a model writes the answer
from retrieved chunks. This **quotes the chunk verbatim** instead. That is a smaller
feature and a much stronger guarantee: no model is in the path at all, so the ADR-0011
failure mode of a fluent summary that drifts from its source cannot occur. The corpus is
the game's own text, written to be read by players, so quoting it is not a degraded
synthesis - for *"how does the breeding farm work"* it is the better answer.

Synthesis becomes worth building when a question needs two chunks combined. Nothing has
shown that yet, and the project's own history says not to build the mechanism before the
need (see the refinement-follow-up entry in STATUS).

## What it is deliberately not doing yet: embeddings

The design is hybrid retrieval - vector similarity plus an entity boost. This is the
entity boost plus **lexical** scoring, no embeddings, no new dependency, no model.

That is not a shortcut around the measurement; it is the baseline the measurement needs.
This project keeps finding that the simple thing wins or ties - a keyword fast path
answers most Q1 traffic, `large-v3` was *less* accurate than `medium.en` - and shipping
embeddings without a lexical number to beat would make "the sophisticated thing is
better" an assumption rather than a result. The corpus is 3,106 chunks of mostly short,
noun-dense text with the entity names spelled the same way the lexicon spells them, which
is close to the best case for lexical matching and the worst case for assuming otherwise.

## The floor, and the measurement that says lexical is not enough

`RELEVANT` is the point where "not in my sources" fires. The roadmap asks for it to be
calibrated against 50 questions split in-corpus and out; it is set against 33
(`tools/eval/score_corpus.py`), which is smaller and, worse, written by the person who
built the class. Chosen by the rule every other threshold here was chosen by: **where
wrong answers start, not where coverage stops improving.**

| floor | answered right | declined right | WRONG | missed |
|---|---|---|---|---|
| 0.30 | 18 | 8 | 7 | 0 |
| 0.50 | 18 | 10 | 2 | 3 |
| 0.62 | 17 | 11 | 1 | 4 |
| **0.80** | **17** | **11** | **0** | **5** |

0.80 costs nothing against 0.62 on this set and removes the last wrong answer, which was
*"do my pals get tired"* quoting a Castaway's Journal entry instead of the Sanity help
page. A section preference - prefer the help guide over lore for an explanatory question -
would also have fixed it, and was not written, because tuning a rule on one observation is
how a special case gets mistaken for a principle.

**The informative half of that table is the `missed` column, and it is the case for
embeddings.** All five misses are in-corpus questions asked in the player's words rather
than the game's: the game has a *Death* entry and the player says "die", a *Pal Rank &
Essence Condensers* entry and the player says "raise". They score 0.34-0.70 - inside the
band the out-of-corpus questions occupy - so **no threshold separates a paraphrased
question from an unanswerable one on lexical matching.** That is not a floor to tune, it
is a ceiling on the method, and it is the number an embedding index has to beat.

Note also what the seventeen successes are worth: they were written using the help
guide's own vocabulary, so they measure the corpus and the plumbing rather than the
retrieval. Real play is the independent check, as it was for the aliases.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .knowledge import STOPWORDS, WAKE_WORDS

REPO = Path(__file__).resolve().parents[1]

# Words that appear in nearly every question and in a fair number of chunks, so they add
# score without adding meaning. Separate from `STOPWORDS`, which is a general linguistic
# filter shared with entity ranking: these are specific to asking a question about a game.
QUESTION_WORDS = frozenset("""
does do work works working mean means explain tell about game palworld thing things
""".split())

# Minimum score for a chunk to be quoted. Below it the honest answer is "not in my
# sources", which ADR-0011 makes mandatory rather than optional. Provisional - see the
# module docstring.
RELEVANT = 0.80

# How the two halves of the score are weighted. **Both are fractions of the question's
# own IDF mass**, so the score is bounded by construction and reads as "how much of what
# you asked does this chunk cover".
#
# The first version was not bounded: it summed IDF with a title multiplier and divided by
# the query total, so ONE matched title word could exceed 1.0 and saturate. Measured, that
# put a Castaway's Journal entry at 1.00 for *"how do I make a sandwich"* - a question
# with no answer in the corpus at all, answered confidently, which is the single failure
# this project refuses. Coverage now dominates, and a chunk matching one word of three
# cannot clear the floor however well-titled it is.
COVERAGE_WEIGHT = 0.7
TITLE_WEIGHT = 0.3

# What one matched entity adds, capped so a chunk cannot ride to the top on names alone -
# a Paldeck entry mentioning three Pals is not a better answer to a question about one of
# them than the help entry that explains the mechanic.
ENTITY_BOOST = 0.12
MAX_ENTITY_BOOST = 0.24

# How far below the best passage a second one may sit and still be worth showing. A
# second quote is a "you may also want", so it has to be nearly as good an answer as the
# first - *"how does sanity work"* found the Sanity help entry at 1.00 and offered the
# Pancake item description underneath it, because a pancake restores SAN. True, cited,
# and noise.
SECOND_PASSAGE_MARGIN = 0.15


class CorpusError(RuntimeError):
    pass


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    title: str
    section: str            # "Help guide" | "Paldeck" | "Item" | ...
    text: str
    entities: tuple[str, ...]

    @property
    def citation(self) -> str:
        """What the card prints as the source. The game's own words for where it lives."""
        return f"{self.section}: {self.title}"


@dataclass(frozen=True)
class Passage:
    chunk: Chunk
    score: float
    # Which query terms actually matched. Carried for diagnosis, and because a passage
    # that scored on one common word is worth looking at differently from one that
    # matched three.
    matched: tuple[str, ...]


@dataclass(frozen=True)
class LookupResult:
    query: str
    passages: list[Passage]
    # The best score seen, whether or not it cleared the floor. On a decline this is what
    # says "nearly" rather than "nothing", and it is the number the floor is tuned on.
    best_score: float
    floor: float
    chunks_searched: int

    @property
    def grounded(self) -> bool:
        return bool(self.passages)


def _terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words
            if w not in STOPWORDS and w not in WAKE_WORDS
            and w not in QUESTION_WORDS and len(w) > 2]


class Corpus:
    """The chunks, plus the inverted document frequencies scoring needs.

    Built once at load. 3,106 chunks is small enough that scoring walks all of them -
    the data model's own sizing note says exact search is sub-millisecond here and no
    index structure is warranted, and that holds for a lexical scan as much as for a
    vector one.
    """

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._tokens: list[tuple[frozenset[str], frozenset[str]]] = []
        df: Counter[str] = Counter()
        for c in chunks:
            title = frozenset(_terms(c.title))
            body = frozenset(_terms(c.text))
            self._tokens.append((title, body))
            df.update(title | body)
        n = max(len(chunks), 1)
        # Smoothed IDF, so a term in every chunk contributes ~0 rather than exactly 0 and
        # a term in none does not divide by zero.
        self._idf = {t: math.log((n + 1) / (d + 1)) + 1.0 for t, d in df.items()}
        # What a term appearing in NO chunk would score. Used for query words the corpus
        # has never seen, which must weigh against a match rather than vanish from it.
        self._unseen_idf = math.log(n + 1) + 1.0

    def search(self, query: str, entities: tuple[str, ...] = (),
               limit: int = 2, floor: float = RELEVANT) -> LookupResult:
        """The best passages for a question, or none when nothing clears the floor.

        `entities` are canonical names the router already resolved. Passing them rather
        than re-matching here keeps the boost using the same vocabulary the lexicon
        produces - a boost firing on a name the query could never resolve to would be
        scoring a coincidence.
        """
        asked = list(dict.fromkeys(_terms(query)))
        # **Words the corpus has never seen stay in the denominator.** Dropping them was
        # the second version of the same bug as the unbounded score: *"how do I make a
        # sandwich"* has no `sandwich` anywhere in 3,106 chunks, so filtering it left
        # `make` as the entire question and any chunk containing that word covered 100%
        # of it. A term absent from the corpus is the strongest evidence there is that
        # the question is out of corpus, so it is scored as maximally informative and
        # unmatched.
        total = sum(self._idf.get(t, self._unseen_idf) for t in asked)
        terms = [t for t in asked if t in self._idf]
        wanted = {e.lower() for e in entities}

        scored: list[Passage] = []
        best = 0.0
        for chunk, (title, body) in zip(self.chunks, self._tokens):
            if not total:
                break
            covered = titled = 0.0
            matched = []
            for t in terms:
                in_title, in_body = t in title, t in body
                if not (in_title or in_body):
                    continue
                covered += self._idf[t]
                if in_title:
                    titled += self._idf[t]
                matched.append(t)
            if not matched:
                continue
            # Two fractions of the question's own IDF mass, so the score cannot exceed 1
            # and reads as "how much of what you asked this chunk covers". A chunk whose
            # TITLE names the whole question scores 1.0; one that merely mentions every
            # word scores 0.7; one that catches a single word of three scores ~0.25 and
            # never reaches the floor.
            score = (COVERAGE_WEIGHT * covered + TITLE_WEIGHT * titled) / total
            if wanted:
                overlap = sum(1 for e in chunk.entities if e.lower() in wanted)
                score += min(overlap * ENTITY_BOOST, MAX_ENTITY_BOOST)
            score = min(score, 1.0)
            best = max(best, score)
            if score >= floor:
                scored.append(Passage(chunk=chunk, score=round(score, 3),
                                      matched=tuple(matched)))

        # Longest text breaks a tie, not shortest: two chunks covering the question
        # equally well are usually a one-line item description and the help entry that
        # explains the mechanic, and the reader wants the second.
        scored.sort(key=lambda p: (-p.score, -len(p.chunk.text), p.chunk.chunk_id))
        kept: list[Passage] = []
        if scored:
            bar = scored[0].score - SECOND_PASSAGE_MARGIN
            kept = [scored[0]] + [p for p in scored[1:limit] if p.score >= bar]
        return LookupResult(query=query, passages=kept, best_score=round(best, 3),
                            floor=floor, chunks_searched=len(self.chunks))


@lru_cache(maxsize=4)
def load(version: str = "1.0.2") -> Corpus:
    path = REPO / "data" / version / "corpus.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CorpusError(
            f"missing dataset: {path} - run tools/ingest/build_corpus.py") from e
    return Corpus([Chunk(chunk_id=c["chunk_id"], title=c["title"],
                         section=c["section"], text=c["text"],
                         entities=tuple(c.get("entities", ())))
                   for c in raw["chunks"]])


def lookup(query: str, entities: tuple[str, ...] = (), limit: int = 2,
           version: str = "1.0.2") -> LookupResult:
    return load(version).search(query, entities=entities, limit=limit)
