"""Intent routing on Gemini. Third backend behind the same RouterBackend protocol.

Exists to price the middle of the market. Opus 5 holds the safety bar at $0.0175/query;
Qwen3 8B is free and breaks it (11% wrong entities). Whether anything in between holds
the bar is the open question, and it is not answerable from the accuracy number alone -
see the A5 tables in Docs/04-roadmap.md.

Uses **function calling**, not `responseSchema`, and the choice was forced. Gemini's
structured-output schema rejects an enum past roughly 2KB of values: the 318-name lexicon
fails with a bare `INVALID_ARGUMENT`, and it is a size limit rather than a count limit -
217 real names pass, 218 fail, while 300 short synthetic names pass. Function
declarations carry the identical enum (2,925 chars) without complaint.

That turns out to be the better comparison anyway: Gemini and Claude both receive the
same registry as typed tool schemas with the same enums, so this arm is like-for-like
with the hosted baselines, while the local backend's grammar is the outlier.

Raw REST over urllib rather than an SDK - no new dependency, and no pinning to a Python
surface that has churned. Model ids are not hardcoded anywhere: query /v1beta/models.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .knowledge import Candidate, Lexicon
# The function-calling wording, shared with the Claude backend - not routing_local's,
# which asks for a JSON object and names a "decline" tool that exists only in that
# backend's grammar. Gemini followed those instructions faithfully: it called a function
# named `decline` (an unregistered tool, so a hard error at the dispatcher) and put bare
# `{}` in the text half of a decline, which rendered verbatim on the player's card.
# routing.py's module docstring always said the output-format sentence is the one thing
# that differs per backend; this import was the exception that proved it.
from .routing import context_block
from .routing_anthropic import SYSTEM
from .routing_unified import unpack
from .tools import Decline, ToolCall

log = logging.getLogger("palintel.routing.gemini")

MODEL = "gemini-3.6-flash"
API = "https://generativelanguage.googleapis.com/v1beta"
# Evaluation timeout. A slow response is data there, so the harness waits for it.
TIMEOUT_S = 120
# Runtime timeout. Measured over the 232-utterance A5 set: the routed p95 is 7.9s, but
# the tail runs to 64s and two requests hit the old 120s ceiling - two minutes of a bot
# that looks hung, for a query budgeted at 2.5s end to end. Cutting at 8s costs 5 of 184
# routed calls (2.2%), every one of which had already blown the budget by 3x, and in
# exchange the worst case becomes bounded and falls to a backstop router that answers
# clear resource queries instantly. Not a latency saving - a promise about the worst case.
RUNTIME_TIMEOUT_S = 8.0
# Long enough to outlast a full 1000-prompt run; the cache is deleted explicitly when the
# router is done, so this is a backstop against a crashed run leaking storage, not the
# normal path. Storage bills per token-hour (~$0.016/hour for this schema).
CACHE_TTL_S = 7200
# Gemini will not create a cache below this; see the pricing/caching docs. Production Q1
# sits under it, so context caching is an evaluation-time saving rather than a runtime one.
CACHE_MIN_TOKENS = 2048
# Gemini 3 replaced `thinkingBudget` with `thinkingLevel`; a budget of 0 is now a 400
# rather than "thinking off". `minimal` is the only setting that reaches zero thought
# tokens - `low` still thinks, and measured barely below `high`.
THINKING_LEVELS = ("minimal", "low", "high")

# Per million tokens: (input, output).
#
# Source: https://ai.google.dev/gemini-api/docs/pricing (paid tier, standard).
# Output price is stated by Google as *including* thinking tokens, so thoughts are folded
# into output in GeminiUsage.usd rather than priced separately.
#
# Do not take these from the model. Asked its own pricing, Gemini answered
# $0.075 / $0.30 - understating input 20x and output 25x, which turned a $5.75 run into
# an apparent $0.25 one and briefly inverted the entire cost comparison against Haiku.
# A model answers from training data that predates its own release; the pricing page is
# the source of record.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.6-flash": (1.50, 7.50),
}
# Context-caching input rate, per million tokens. Not yet used: the backend sends the
# ~16.7k-token tool schema uncached on every request, which is ~93% of its spend.
CACHED_INPUT_PRICE = {"gemini-3.6-flash": 0.15}


@dataclass(frozen=True)
class GeminiUsage:
    input: int
    output: int
    model: str
    thoughts: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def usd(self) -> float:
        """0.0 when the model's price is not known. Never a guess - see PRICES.

        `input` is Gemini's promptTokenCount, which *includes* the cached prefix, so the
        cached portion is subtracted out and repriced rather than double-counted.
        """
        p = PRICES.get(self.model)
        if p is None:
            return 0.0
        uncached = max(0, self.input - self.cache_read)
        cached_rate = CACHED_INPUT_PRICE.get(self.model, p[0])
        return (uncached * p[0] + self.cache_read * cached_rate
                + (self.output + self.thoughts) * p[1]) / 1e6

    @property
    def priced(self) -> bool:
        return self.model in PRICES

    def __str__(self) -> str:
        tail = f"${self.usd:.5f}" if self.priced else "unpriced"
        return f"[in {self.input}, out {self.output}+{self.thoughts} thought, {tail}]"


def _to_gemini_schema(node: Any) -> Any:
    """Translate an Anthropic tool input_schema into Gemini's OpenAPI subset.

    Only the constructs the registry actually emits are handled. A union type such as
    ["integer", "null"] - which the resource tool uses for an optional player level -
    becomes a single type plus `nullable`, since Gemini has no union form.
    """
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k == "type":
            if isinstance(v, list):
                concrete = [t for t in v if t != "null"]
                out["type"] = concrete[0].upper()
                if len(concrete) != len(v):
                    out["nullable"] = True
            else:
                out["type"] = v.upper()
        elif k == "properties":
            out["properties"] = {pk: _to_gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out["items"] = _to_gemini_schema(v)
        elif k in ("enum", "required", "maxItems", "description"):
            out[k] = v
        # additionalProperties has no Gemini equivalent and is dropped.
    return out


def _to_function_declarations(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool schemas -> Gemini functionDeclarations, same registry."""
    decls = []
    for t in tools:
        params = _to_gemini_schema(t["input_schema"])
        # Gemini rejects an OBJECT parameter block with no properties.
        decls.append({"name": t["name"],
                      "description": " ".join(t["description"].split()),
                      **({"parameters": params} if params.get("properties") else {})})
    return decls


def _reason_from(parts: list[dict]) -> str:
    """The model's own words for why it declined - but only if they are words.

    The decline reason is rendered on the player's card, and this backend inherits a
    system prompt that asks for JSON. So the text half of a decline is sometimes a bare
    `{}` or a fenced JSON object, which reached the card verbatim. There is nothing to
    salvage from those, and an empty string makes the caller use its own wording.
    """
    said = " ".join(p.get("text", "") for p in parts).strip()
    if said.startswith(("{", "[", "```")):
        return ""
    return said


class GeminiRouter:
    """RouterBackend backed by the Gemini API with typed function declarations."""

    def __init__(self, lexicon: Lexicon, locatable: set[str] | None = None,
                 api_key: str | None = None, extra_tools: list[dict] | None = None,
                 model: str = MODEL, use_cache: bool = True,
                 thinking_level: str | None = None,
                 timeout_s: float = RUNTIME_TIMEOUT_S,
                 unified: bool = False,
                 classes: tuple[str, ...] | None = None,
                 items: list[str] | None = None):
        from .routing_anthropic import registry  # one registry, one definition
        from .routing_unified import PRODUCTION_CLASSES

        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env")
        self._key = key
        self._model = model
        self._thinking_level = thinking_level
        self._timeout_s = timeout_s
        self.name = f"gemini:{model}" + (f":think={thinking_level}"
                                         if thinking_level else "")

        self._resources = sorted(locatable if locatable is not None
                                 else set(lexicon.resources()))
        if unified:
            # One tool absorbs the extra classes too, rather than sitting beside them -
            # otherwise the run would carry a consolidated tool AND five per-class ones,
            # which is neither registry and measures nothing.
            tools = registry(self._resources, lexicon.pals(), items=items,
                             unified=True, classes=classes or PRODUCTION_CLASSES)
        else:
            tools = [*registry(self._resources, lexicon.pals()), *(extra_tools or [])]
        self._decls = _to_function_declarations(tools)
        self._entities = sorted(set(lexicon.canonical_names) | set(self._resources))
        self.tool_names = [t["name"] for t in tools]
        self.last_usage: GeminiUsage | None = None
        # Created lazily on the first route() so constructing a router costs nothing,
        # and so a caller that never routes never pays cache storage.
        self._use_cache = use_cache
        self._cache: str | None = None

    def _user_content(self, utterance: str, candidates: list[Candidate],
                      context: list | None = None) -> str:
        if candidates:
            hints = "\n".join(
                f"  {c.score:.2f}  {c.canonical}  ({c.kind}, matched {c.matched_text!r})"
                for c in candidates)
        else:
            hints = "  (none)"
        # The context block sits in the user turn, not the cached system prefix: it
        # changes on every request, and putting it in the cache would invalidate 16.4k
        # tokens of tool schemas on each follow-up.
        return (f"{context_block(context)}"
                f"Utterance:\n  {utterance}\n\n"
                f"Candidate entities, best first:\n{hints}")

    def _create_cache(self) -> str | None:
        """Cache the tool schemas and system prompt; return the cache resource name.

        The schemas are ~16.4k tokens and identical on every request - about 93% of this
        backend's spend when resent uncached. Cached input bills at a tenth of the
        standard rate, taking a request from ~$0.025 to ~$0.003.

        Returns None (and the caller falls back to sending schemas inline) rather than
        raising: caching is an optimisation, and a run that silently costs more is a far
        better outcome than a run that dies.
        """
        # Gemini refuses to cache below 2048 tokens. Production Q1 registers one tool and
        # comes to ~650, so caching is an eval-only win: the A5 harness registers all
        # seven query classes at ~16.4k. Checking first avoids a guaranteed-400 API call
        # and a misleading warning on every single startup.
        approx = (len(json.dumps(self._decls)) + len(SYSTEM)) // 4
        if approx < CACHE_MIN_TOKENS:
            log.debug("schema ~%d tok is below Gemini's %d-token cache minimum; "
                      "sending inline", approx, CACHE_MIN_TOKENS)
            return None

        body = {
            "model": f"models/{self._model}",
            "ttl": f"{CACHE_TTL_S}s",
            "displayName": "palintel-router",
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "tools": [{"functionDeclarations": self._decls}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        }
        req = urllib.request.Request(
            f"{API}/cachedContents", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key})
        try:
            # The instance timeout, not TIMEOUT_S: this runs lazily inside the first
            # route(), so a hang here is a hang on a real query.
            with urllib.request.urlopen(req, timeout=self._timeout_s) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("gemini cache creation failed (%s); sending schemas inline", e)
            return None
        name = data.get("name")
        log.info("gemini cache %s created, %s tok", name,
                 (data.get("usageMetadata") or {}).get("totalTokenCount"))
        return name

    def delete_cache(self) -> None:
        """Release the cache. Storage bills per token-hour, so do not leave it to TTL."""
        if not self._cache:
            return
        req = urllib.request.Request(f"{API}/{self._cache}", method="DELETE",
                                     headers={"x-goog-api-key": self._key})
        try:
            urllib.request.urlopen(req, timeout=30)
            log.info("gemini cache %s deleted", self._cache)
        except (urllib.error.URLError, TimeoutError):
            log.warning("could not delete gemini cache %s; it expires in %ss",
                        self._cache, CACHE_TTL_S)
        self._cache = None

    def route(self, utterance: str, candidates: list[Candidate],
              context: list | None = None) -> ToolCall | Decline:
        if self._use_cache and self._cache is None:
            self._cache = self._create_cache()
            self._use_cache = self._cache is not None

        # thinkingConfig rides in generationConfig, which is per-request and outside the
        # cache, so a cached run can still vary the level.
        gen: dict[str, Any] = {"temperature": 0}
        if self._thinking_level:
            gen["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        body: dict[str, Any] = {
            "contents": [{"role": "user",
                          "parts": [{"text": self._user_content(
                              utterance, candidates, context)}]}],
            "generationConfig": gen,
        }
        if self._cache:
            # Schemas and system prompt come from the cache; resending them here is
            # rejected as a duplicate definition.
            body["cachedContent"] = self._cache
        else:
            body["systemInstruction"] = {"parts": [{"text": SYSTEM}]}
            body["tools"] = [{"functionDeclarations": self._decls}]
            # AUTO, not ANY: declining is a real answer here, and forcing a call would
            # remove the behaviour A5 actually measures.
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        req = urllib.request.Request(
            f"{API}/models/{self._model}:generateContent",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # A 403/404 here usually means the cache expired or was deleted underneath a
            # long run. Drop it and retry once inline rather than failing every remaining
            # prompt - an expensive run beats a dead one.
            if e.code in (403, 404) and self._cache:
                log.warning("gemini cache %s unusable (%s); retrying inline",
                            self._cache, e.code)
                self._cache = None
                self._use_cache = False
                return self.route(utterance, candidates)
            # The key rides in a header, so the URL is safe to log; the body is not.
            log.error("gemini %s on %s", e.code, self._model)
            # 429 and 5xx are the provider asking to be tried again; a 4xx is our bug and
            # retrying it just spends the budget twice.
            return Decline(reason=f"gemini error {e.code}",
                           transient=e.code == 429 or e.code >= 500)
        except (urllib.error.URLError, TimeoutError):
            log.warning("gemini did not answer within %.0fs", self._timeout_s)
            return Decline(reason="gemini unreachable", transient=True)

        u = data.get("usageMetadata", {})
        self.last_usage = GeminiUsage(
            input=u.get("promptTokenCount", 0),
            output=u.get("candidatesTokenCount", 0),
            thoughts=u.get("thoughtsTokenCount", 0),
            # Implicit context caching, if it is applying at all. Recorded because the
            # tool schema is ~16.7k tokens resent on every request and is ~99% of this
            # backend's spend: whether it is being cached is the single biggest cost
            # question, and it was previously not measured.
            cache_read=u.get("cachedContentTokenCount", 0),
            model=self._model,
        )

        cands = data.get("candidates") or []
        if not cands:
            # Safety block or empty generation - an honest decline, not a crash.
            log.warning("gemini returned no candidate: %s", data.get("promptFeedback"))
            return Decline(reason="gemini returned no candidate")
        parts = (cands[0].get("content") or {}).get("parts") or []
        call = next((p["functionCall"] for p in parts if "functionCall" in p), None)
        # SYSTEM is shared with the local backend, where declining means emitting a JSON
        # object with tool "decline". This backend has no such tool - it declines by
        # returning text - but the model follows the instruction anyway and sometimes
        # calls a function by that name. Reaching the dispatcher, that is an unregistered
        # tool and a hard error; it is really a decline wearing the local backend's
        # clothes. See the SYSTEM note in routing_local.py.
        if call is not None and call.get("name") not in self.tool_names:
            log.info("gemini declined via a %r call %s", call.get("name"),
                     self.last_usage)
            call = None
        if call is None:
            said = _reason_from(parts)
            log.info("gemini declined %s: %s", self.last_usage, said or "(no reason)")
            return Decline(reason=said or "no matching query type",
                           known_options=self._resources)

        args = {k: v for k, v in (call.get("args") or {}).items() if v is not None}
        name, args = unpack(call["name"], args)
        log.info("gemini -> %s(%s) %s", name, args, self.last_usage)
        return ToolCall(name=name, args=args,
                        rationale=f"{self.name} chose {call['name']}")


def available(api_key: str | None = None) -> bool:
    """True when a Gemini credential is resolvable.

    Credential-only, deliberately: this runs during startup backend selection, and a
    network probe there would add latency to every launch and would report a transient
    outage as "no Gemini configured" - sending the pipeline silently down to the stub.
    A bad key surfaces on the first route as an honest decline instead.
    """
    return bool(api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def list_models(api_key: str | None = None) -> list[str]:
    """Model ids that support generateContent, from the API rather than from memory."""
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    req = urllib.request.Request(f"{API}/models?pageSize=200",
                                 headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return [m["name"].removeprefix("models/") for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]
