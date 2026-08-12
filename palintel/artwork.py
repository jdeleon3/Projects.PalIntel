"""Artwork for answer cards — applied after the card, never inside it.

This module is the join between a typed result and the two published asset sets: world
map tiles and Pal icons. It exists so `cards.py` stays a pure text template (no Pillow,
no filesystem, exactly testable) and `mapcard.py` stays a pure renderer that knows
nothing about query classes.

Everything here is best-effort by construction. Assets missing, Pillow missing, a Pal
with no icon, a coordinate on no published map - each returns the card unchanged. A card
is an answer without a picture; it is never an answer *because* of one.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .cards import Card
from .execution import ResourceResult, SpawnResult
from .mapcard import MapAssets, render

log = logging.getLogger("palintel.artwork")


class Artwork:
    def __init__(self, assets: MapAssets, *, maps: bool = True, icons: bool = True):
        self.assets = assets
        self.maps = maps
        self.icons = icons

    @classmethod
    def load(cls, root: Path, *, maps: bool = False, icons: bool = False):
        """Return None unless something is switched on and the assets are actually there.

        Silent rather than loud: the assets are extracted from the player's own game
        install and are legitimately absent on a fresh checkout. Refusing to start over a
        missing picture would trade a working bot for a decorative one.
        """
        if not (maps or icons):
            return None
        assets = MapAssets.load(root)
        if assets is None:
            log.warning("card artwork is enabled but no assets at %s - "
                        "run tools/ingest/build_assets.py; answering text-only", root)
            return None
        return cls(assets, maps=maps, icons=icons)

    def _draw_map(self, card: Card, points, near) -> None:
        if not self.maps or not points:
            return
        try:
            drawn = render(self.assets, points, near=near)
        except Exception:
            # A broken tile or an unreadable manifest must not cost the player the
            # answer that is already sitting in the card.
            log.exception("map render failed; sending the card without it")
            return

        if drawn is None:
            # Nothing to anchor on - the top answer sits on no published map.
            log.info("no map for %r (result 1 is outside every region)", card.title)
            return

        card.image = drawn.image
        if drawn.omitted:
            # The map shows some of the answers, so the card has to say which. Silence
            # here is the failure the region rules exist to prevent: a crop that looks
            # complete and is not. Reported as the card's own numbering, because that is
            # what the reader is looking at.
            missing = ", ".join(f"#{i}" for i in drawn.omitted)
            card.lines.append(
                f"_Map shows {drawn.region} only - {missing} "
                f"{'is' if len(drawn.omitted) == 1 else 'are'} on another map._")

    def illustrate_resource(self, card: Card, result: ResourceResult) -> Callable[[], None]:
        """Plan the artwork for a resource card; the returned callable draws it.

        Nothing is rendered here. The caller posts the text card first and calls this
        afterwards, so the milliseconds land after the answer rather than in front of it.

        Map only, no thumbnail. The material's inventory icon was tried and dropped: it
        shows what the item looks like in your pack, and what a player actually needs is
        the rock in the world, which the game ships no 2D art for. A picture that answers
        a question nobody asked still costs the reader a glance.
        """
        points = [(n.map_x, n.map_y, result.resource) for n in result.nodes]
        return lambda: self._draw_map(card, points, result.near)

    def illustrate_sites(self, card: Card, points: list[tuple[float, float, str]],
                         near: tuple[float, float] | None = None
                         ) -> Callable[[], None]:
        """Plan a map crop for any list of already-labelled coordinates.

        The one illustrator that takes points rather than a typed result. Base siting
        produces coordinates that belong to no single resource - a site exists because
        several are in range - so there is no result shape to read them out of, and
        reshaping one to fit would be worse than passing what the map actually needs.
        """
        return lambda: self._draw_map(card, points, near)

    def illustrate_spawn(self, card: Card, result: SpawnResult) -> Callable[[], None]:
        points = [(a.map_x, a.map_y, result.pal) for a in result.areas]

        def draw() -> None:
            # The icon goes on even when there is nothing to map - a tower boss with no
            # overworld spawn still benefits from "this is the one I mean", which a name
            # alone cannot settle for a player who has heard their own STT mangle it.
            if self.icons:
                card.thumbnail = self.assets.icon(result.pal)
            self._draw_map(card, points, result.near)

        return draw
