"""Intent routing on a locally served model (Ollama).

Implements the same RouterBackend protocol as routing_anthropic.py. Exists to answer one
question the hosted measurements cannot: how much of the routing decision needs a frontier
model, and what does the rest cost when inference is free.

The architecture differs from the hosted path in one way that matters. A hosted router
ships the 313-name Pal enum as a tool schema on every request - 21,741 tokens, ~80% of the
billed run (see 01-architecture.md section 7 note 4). Locally the enum does not go in the
context at all: it becomes a *decoding constraint*. Ollama compiles the JSON schema below
into a GBNF grammar, so a name outside the lexicon is unrepresentable rather than merely
discouraged.

That guarantees entity *validity*. It does not guarantee the entity is *right* - the model
can still be constrained into the wrong valid name, which is exactly the failure A5
measures. Grammar removes hallucinated Pals; it does not remove misidentified ones.

Tool schemas are accepted in Anthropic form and rendered into the prompt, rather than
restated here, so both backends are demonstrably scoring the same registry.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .knowledge import Candidate, Lexicon
from .routing import ROUTING_POLICY
from .tools import Decline, ToolCall

log = logging.getLogger("palintel.routing.local")

MODEL = "qwen3:8b"
HOST = "http://127.0.0.1:11434"
# Generous: a cold model load is seconds, and a grammar-constrained decode on a 21k-name
# alternation is slower per token than free generation.
TIMEOUT_S = 180
# Two is the observed maximum (breeding pairs, comparisons). Capping it in the grammar
# stops a small model padding the array to fill the schema.
MAX_ENTITIES = 2

SYSTEM = f"""\
You route Palworld voice and text queries to typed tools. You are one stage in a \
pipeline: your only output is one JSON object. Pick a tool when you can identify both \
the intent and its parameters; otherwise pick "decline" and return no entities.

{ROUTING_POLICY}\
"""


@dataclass(frozen=True)
class LocalUsage:
    """Token counts for one local routing call.

    Deliberately duck-typed to match routing_anthropic.Usage rather than importing it:
    that type prices an unknown model at Opus rates, which would report a local run as
    the most expensive thing in the project. Local inference is free; the field exists so
    the same scorer can consume both backends.
    """
    input: int
    output: int
    model: str
    cache_read: int = 0
    cache_write: int = 0

    @property
    def usd(self) -> float:
        return 0.0

    def __str__(self) -> str:
        return f"[in {self.input}, out {self.output}, local]"


def _render_tools(tools: list[dict[str, Any]]) -> str:
    """Describe the tool registry as prose for the prompt.

    The hosted backend gets this as typed schemas; here the enums are stripped out (they
    are in the grammar) and only names, intent, and slot names survive. Keeping the source
    of truth in the schemas means the two backends cannot drift apart silently.
    """
    lines = []
    for t in tools:
        slots = ", ".join(t.get("input_schema", {}).get("properties", {}))
        lines.append(f"- {t['name']}({slots})\n    {' '.join(t['description'].split())}")
    lines.append('- decline()\n    Intent or entity unclear. Return no entities.')
    return "\n".join(lines)


def _output_schema(entities: list[str], tool_names: list[str]) -> dict[str, Any]:
    """The grammar. Entities are collected by value, not by slot name.

    score_router.py scores entities by value and treats tool choice as out of scope for
    A5, so a flat {tool, entities} object carries everything the measurement reads while
    keeping the grammar to a single alternation instead of one per tool arity.
    """
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [*tool_names, "decline"]},
            "entities": {
                "type": "array",
                "maxItems": MAX_ENTITIES,
                "items": {"type": "string", "enum": entities},
            },
        },
        "required": ["tool", "entities"],
        "additionalProperties": False,
    }


class LocalRouter:
    """RouterBackend backed by a locally served model with grammar-constrained output."""

    def __init__(self, lexicon: Lexicon, locatable: set[str] | None = None,
                 extra_tools: list[dict] | None = None, model: str = MODEL,
                 host: str = HOST, think: bool = False):
        from .routing_anthropic import _tool_schema  # same registry, one definition

        self._resources = sorted(locatable if locatable is not None
                                 else set(lexicon.resources()))
        self._model = model
        self._host = host.rstrip("/")
        self._think = think
        self.name = f"local:{model}"

        tools = [_tool_schema(self._resources), *(extra_tools or [])]
        self._tool_block = _render_tools(tools)
        # Every name the grammar may emit: the full lexicon plus locatable resources.
        self._entities = sorted(set(lexicon.canonical_names) | set(self._resources))
        self._schema = _output_schema(self._entities,
                                      [t["name"] for t in tools])
        self.last_usage: LocalUsage | None = None

    def _user_content(self, utterance: str, candidates: list[Candidate]) -> str:
        if candidates:
            hints = "\n".join(
                f"  {c.score:.2f}  {c.canonical}  ({c.kind}, matched {c.matched_text!r})"
                for c in candidates)
        else:
            hints = "  (none)"
        return (f"Tools:\n{self._tool_block}\n\n"
                f"Utterance:\n  {utterance}\n\n"
                f"Candidate entities, best first:\n{hints}")

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self._host}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())

    def warmup(self) -> float:
        """Load the model into VRAM. Returns seconds taken.

        Called before timing: the first request otherwise carries a multi-second weight
        load that is not part of per-query latency, and would land in the p95.
        """
        t = time.perf_counter()
        self._post("/api/chat", {
            "model": self._model, "stream": False, "think": self._think,
            "messages": [{"role": "user", "content": "ok"}],
            "options": {"num_predict": 1},
        })
        return time.perf_counter() - t

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
        try:
            data = self._post("/api/chat", {
                "model": self._model,
                "stream": False,
                "think": self._think,
                "format": self._schema,
                "system": SYSTEM,
                "messages": [{"role": "user",
                              "content": self._user_content(utterance, candidates)}],
                # Greedy: this is a classification with one right answer, and run-to-run
                # variance is already the thing being controlled for.
                "options": {"temperature": 0.0},
            })
        except urllib.error.URLError as e:
            log.error("local router unreachable at %s: %s", self._host, e)
            return Decline(reason=f"local router unreachable - is `ollama serve` running?")
        except TimeoutError:
            return Decline(reason="local router timed out")

        self.last_usage = LocalUsage(
            input=data.get("prompt_eval_count", 0),
            output=data.get("eval_count", 0),
            model=self._model,
        )

        raw = (data.get("message") or {}).get("content", "")
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            # The grammar should make this unreachable; if it fires, the schema was not
            # applied and the run is measuring something other than what it claims.
            log.error("local router returned non-JSON despite grammar: %r", raw[:200])
            return Decline(reason="local router returned unparseable output")

        tool = out.get("tool", "decline")
        found = [e for e in out.get("entities", []) if e in self._entities]
        if tool == "decline":
            log.info("local router declined %s", self.last_usage)
            return Decline(reason="intent or entity unclear",
                           known_options=self._resources)

        args = {f"entity_{i}": e for i, e in enumerate(found)}
        log.info("local router -> %s(%s) %s", tool, args, self.last_usage)
        return ToolCall(name=tool, args=args,
                        rationale=f"{self.name} chose {tool}")


def available(host: str = HOST) -> bool:
    """True when an Ollama server is reachable."""
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=2):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
