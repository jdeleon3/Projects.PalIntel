"""Q7 Tier 3 lookup — grounding, the floor, and the two bugs that made it confident.

The class quotes the game's own prose and cites it. The whole risk is that a citation
makes a wrong answer look *more* trustworthy rather than less, so the tests that matter
are the ones about declining:

* a question the corpus cannot answer must decline, and
* the score must be bounded, because both bugs found while building this were the same
  bug - a partial match reading as a total one.
"""
from __future__ import annotations

import pytest

from palintel import cards, corpus
from palintel.corpus import Chunk, Corpus, CorpusError


@pytest.fixture
def small() -> Corpus:
    return Corpus([
        Chunk("help:sanity", "Sanity", "Help guide",
              "When Pals do work at a base, their Sanity drops. Feed them quality food "
              "or let them rest in a hot spring to recover.", ()),
        Chunk("item:pancake", "Pancake", "Item",
              "A dish made by mixing flour and milk together then baking. Restores a "
              "little sanity.", ()),
        Chunk("paldeck:chillet", "Chillet", "Paldeck",
              "A serpentine Pal that coils around its prey.", ("Chillet",)),
    ])


# ------------------------------------------------------------------ the score is bounded

def test_a_partial_match_cannot_score_as_a_total_one(small):
    """**The first bug, and the one that made an unanswerable question confident.**

    The score summed IDF with a title multiplier and divided by the query's total, so a
    single matched title word could exceed 1.0 and saturate. Measured, that put a
    Castaway's Journal entry at 1.00 for "how do I make a sandwich" - a question with no
    answer anywhere in 3,106 chunks.
    """
    result = small.search("sanity", floor=0.0, limit=5)
    assert all(p.score <= 1.0 for p in result.passages)
    partial = small.search("sanity hot spring elephant parade", floor=0.0, limit=5)
    assert partial.best_score < 1.0


def test_a_word_the_corpus_has_never_seen_counts_against_the_match(small):
    """**The second bug, which was the first one wearing a different hat.**

    Unknown query terms were filtered out before scoring, so "how do I make a sandwich"
    became the single word "make" and any chunk containing it covered 100% of the
    question. A term in no chunk is the strongest evidence there is that the question is
    out of corpus.
    """
    known = small.search("sanity", floor=0.0).best_score
    padded = small.search("sanity zzzxqq wibblefrotz", floor=0.0).best_score
    assert padded < known


def test_a_title_match_outranks_a_body_mention(small):
    """The Sanity help entry is about sanity; the pancake merely restores some."""
    top = small.search("sanity", floor=0.0).passages[0]
    assert top.chunk.chunk_id == "help:sanity"


def test_an_entity_boost_uses_the_lexicons_own_names(small):
    """Passing resolved entities rather than re-matching here keeps the boost on the same
    vocabulary the router produces - a boost on a name the query could never resolve to
    is scoring a coincidence."""
    plain = small.search("serpentine", floor=0.0).passages[0].score
    boosted = small.search("serpentine", entities=("Chillet",),
                           floor=0.0).passages[0].score
    assert boosted > plain


def test_a_second_passage_has_to_be_nearly_as_good(small):
    """"How does sanity work" found the Sanity entry at 1.00 and offered the Pancake
    description underneath it, because a pancake restores SAN. True, cited, and noise."""
    result = small.search("sanity", floor=0.0, limit=2)
    assert len(result.passages) == 1


# ------------------------------------------------------------------ grounding

def test_nothing_relevant_declines_rather_than_quoting_the_closest_thing(small):
    result = small.search("what is the capital of France")
    assert not result.grounded


def test_a_decline_still_reports_how_close_it_got(small):
    """The number the floor is tuned on, and the difference between "nearly" and
    "nothing"."""
    result = small.search("sanity elephant parade zzzxqq")
    assert not result.grounded
    assert 0.0 < result.best_score < corpus.RELEVANT


def test_a_missing_corpus_raises_rather_than_answering_from_nothing():
    with pytest.raises(CorpusError):
        corpus.load("no-such-version")


# ------------------------------------------------------------------ the card

def test_the_card_carries_a_citation(small):
    card = cards.corpus_card(small.search("sanity"))
    assert "Help guide: Sanity" in card.to_text()


def test_the_card_quotes_verbatim_and_says_so(small):
    """ADR-0011 describes grounded synthesis; this quotes instead, which is a smaller
    promise and a much easier one to keep. The card must not let a quote read as a
    summary or the reverse."""
    text = cards.corpus_card(small.search("sanity")).to_text()
    assert "When Pals do work at a base" in text
    assert "nothing here was rewritten" in text


def test_the_card_is_blue_because_it_is_reference(small):
    assert cards.corpus_card(
        small.search("sanity")).colour == cards.TIER_REFERENCE


def test_a_decline_card_says_not_in_my_sources(small):
    card = cards.corpus_card(small.search("what is the capital of France"))
    assert "Not in my sources" in card.title
    assert card.colour == cards.TIER_DECLINE


# ------------------------------------------------------------------ the real corpus

def test_the_built_corpus_publishes_no_markup_and_no_untitled_chunk():
    """The corpus is quoted verbatim onto a card, so a leaked `<itemName id=|Wool|/>` is
    published straight to the player - and this project has already shipped markup as a
    display name once, in build_tech.py."""
    try:
        loaded = corpus.load("1.0.2")
    except CorpusError:
        pytest.skip("corpus.json not built")
    assert len(loaded.chunks) > 1000
    for c in loaded.chunks:
        assert "<" not in c.text and "|" not in c.text, c.chunk_id
        # No underscore: every internal key in these tables has one
        # (`ACTION_SKILL_AirBlade`, `DESC_RECIPE_AncientSpa`) and no English display
        # name does. 490 chunks were titled with their key before the skill name table
        # was joined on the right column. All-caps is NOT the test - "HUD" is a real
        # help entry.
        assert c.title and "_" not in c.title, c.chunk_id


def test_patch_notes_carry_their_own_provenance_and_a_date():
    """**The first chunks here that did not come from the pak.**

    Both sources are first-party - the developers' text tables and the developers' Steam
    posts - but they arrive by different roads and one of them needed the network. A
    citation that could not tell them apart would be hiding something a reader should see.

    The date is not decoration either: a patch note is the one kind of chunk in this
    corpus whose meaning depends on when it was true.
    """
    try:
        loaded = corpus.load("1.0.2")
    except CorpusError:
        pytest.skip("corpus.json not built")
    patches = [c for c in loaded.chunks if c.section == "Patch notes"]
    if not patches:
        pytest.skip("patch_notes.json not built")
    assert all(c.provenance == "steam_news" for c in patches)
    assert all(c.date for c in patches)
    assert all(c.provenance == "pak" for c in loaded.chunks
               if c.section != "Patch notes")
    assert "(" in patches[0].citation      # the date reaches the citation


def test_a_patch_note_answers_something_the_help_guide_never_covered():
    """Mutation shipped in 1.0 and the in-game help guide has no entry for it. This is
    the whole argument for the dataset in one query."""
    try:
        corpus.load("1.0.2")
    except CorpusError:
        pytest.skip("corpus.json not built")
    result = corpus.lookup("what is mutation")
    if not result.grounded:
        pytest.skip("patch_notes.json not built")
    assert result.passages[0].chunk.section == "Patch notes"


def test_the_built_corpus_excludes_npc_dialogue():
    """In-character speech is a character's opinion delivered in their voice, and a
    retrieval index cannot tell that apart from a mechanics explanation. A card citing a
    merchant's banter as how the game works is the confidently-wrong answer this project
    refuses, dressed in a source attribution."""
    try:
        loaded = corpus.load("1.0.2")
    except CorpusError:
        pytest.skip("corpus.json not built")
    assert not [c for c in loaded.chunks if "Dialogue" in c.section]


def test_the_real_help_guide_answers_the_questions_it_exists_for():
    """Not a threshold test - a wiring one. If the corpus rebuild ever stops producing
    help entries, every one of these silently becomes "not in my sources"."""
    try:
        corpus.load("1.0.2")
    except CorpusError:
        pytest.skip("corpus.json not built")
    for question in ("how does sanity work", "what is item rot",
                     "how does the breeding farm work"):
        result = corpus.lookup(question)
        assert result.grounded, question
        assert result.passages[0].chunk.section == "Help guide", question


# ------------------------------------------------------------------ the fast-path branch

@pytest.fixture(scope="module")
def router():
    from palintel.knowledge import KnowledgeBase
    from palintel.pipeline import _corpus_probe
    from palintel.routing import StubRouter

    kb = KnowledgeBase.load("1.0.2")
    probe = _corpus_probe("1.0.2")
    if probe is None:
        pytest.skip("corpus.json not built")
    return StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes}),
                      cues="wide", corpus=probe)


def _route(router, text):
    from palintel.tools import ToolCall

    call = router.route(text, router._lexicon.rank(text), [])
    return call.name if isinstance(call, ToolCall) else None


def test_a_mechanic_question_is_claimed(router):
    assert _route(router, "how does sanity work") == "lookup_corpus"


def test_a_question_naming_a_pal_is_left_to_the_narrower_class(router):
    """"Tell me about Shroomer" is pal_info's. This branch is explanatory in shape and
    must not take a question just for sharing that shape."""
    assert _route(router, "tell me about Shroomer") == "get_pal_info"


def test_a_question_naming_a_resource_is_left_to_the_location_class(router):
    assert _route(router, "where is the nearest coal") == "find_resource_nodes"


def test_a_question_the_corpus_cannot_ground_is_deferred_not_claimed(router):
    """**The guard that keeps the fast path honest here.**

    The branch consults the corpus before claiming, so a question it cannot answer still
    reaches the model. Claiming and then printing "not in my sources" would spend the
    player's question on a decline the model never got to try - and the corpus is the
    game's own text, so plenty of reasonable Palworld questions are honestly outside it.
    """
    assert _route(router, "what is the best base layout") != "lookup_corpus"
    assert _route(router, "what is the capital of France") != "lookup_corpus"


def test_the_branch_is_off_without_a_corpus():
    from palintel.knowledge import KnowledgeBase
    from palintel.routing import StubRouter

    kb = KnowledgeBase.load("1.0.2")
    off = StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes}), cues="wide")
    assert _route(off, "how does sanity work") != "lookup_corpus"


def test_a_question_the_answer_would_be_a_shopping_list_for_goes_to_the_corpus(router):
    """The two Phase 4 branches composing, and the reason the tech branch was made to
    require a recommendation frame. "Can you explain technology points" is not a request
    for something to research - it is a request for an explanation, and the game has one."""
    from palintel.pipeline import _corpus_probe
    from palintel.routing import StubRouter

    both = StubRouter(router._lexicon, sorted(router._resources), cues="wide",
                      progression=True, corpus=_corpus_probe("1.0.2"))
    assert _route(both, "can you explain technology points") == "lookup_corpus"
    assert _route(both, "what should I research next") == "suggest_next_unlock"


def test_a_question_the_game_never_answers_is_declined_on_the_real_corpus():
    """The half that matters. The game explains its mechanics and says nothing about
    playing well, so these have no answer in here and must not get one."""
    try:
        corpus.load("1.0.2")
    except CorpusError:
        pytest.skip("corpus.json not built")
    for question in ("what's the best base layout",
                     "which pal has the highest attack stat",
                     "how do I make a sandwich",
                     "what is the capital of France"):
        assert not corpus.lookup(question).grounded, question
