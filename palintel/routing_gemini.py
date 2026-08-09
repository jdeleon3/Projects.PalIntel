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
from .routing_local import SYSTEM
from .tools import Decline, ToolCall

log = logging.getLogger("palintel.routing.gemini")

MODEL = "gemini-3.6-flash"
API = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 120

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
        """0.0 when the model's price is not known. Never a guess - see PRICES."""
        p = PRICES.get(self.model)
        if p is None:
            return 0.0
        return (self.input * p[0] + (self.output + self.thoughts) * p[1]) / 1e6

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


class GeminiRouter:
    """RouterBackend backed by the Gemini API with typed function declarations."""

    def __init__(self, lexicon: Lexicon, locatable: set[str] | None = None,
                 api_key: str | None = None, extra_tools: list[dict] | None = None,
                 model: str = MODEL):
        from .routing_anthropic import _tool_schema  # one registry, one definition

        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env")
        self._key = key
        self._model = model
        self.name = f"gemini:{model}"

        self._resources = sorted(locatable if locatable is not None
                                 else set(lexicon.resources()))
        tools = [_tool_schema(self._resources), *(extra_tools or [])]
        self._decls = _to_function_declarations(tools)
        self._entities = sorted(set(lexicon.canonical_names) | set(self._resources))
        self.tool_names = [t["name"] for t in tools]
        self.last_usage: GeminiUsage | None = None

    def _user_content(self, utterance: str, candidates: list[Candidate]) -> str:
        if candidates:
            hints = "\n".join(
                f"  {c.score:.2f}  {c.canonical}  ({c.kind}, matched {c.matched_text!r})"
                for c in candidates)
        else:
            hints = "  (none)"
        return (f"Utterance:\n  {utterance}\n\n"
                f"Candidate entities, best first:\n{hints}")

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user",
                          "parts": [{"text": self._user_content(utterance, candidates)}]}],
            "tools": [{"functionDeclarations": self._decls}],
            # AUTO, not ANY: declining is a real answer here, and forcing a call would
            # remove the behaviour A5 actually measures.
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {"temperature": 0},
        }
        req = urllib.request.Request(
            f"{API}/models/{self._model}:generateContent",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # The key rides in a header, so the URL is safe to log; the body is not.
            log.error("gemini %s on %s", e.code, self._model)
            return Decline(reason=f"gemini error {e.code}")
        except (urllib.error.URLError, TimeoutError):
            return Decline(reason="gemini unreachable")

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
        if call is None:
            said = " ".join(p.get("text", "") for p in parts).strip()
            log.info("gemini declined %s: %s", self.last_usage, said or "(no reason)")
            return Decline(reason=said or "no matching query type",
                           known_options=self._resources)

        args = {k: v for k, v in (call.get("args") or {}).items() if v is not None}
        log.info("gemini -> %s(%s) %s", call["name"], args, self.last_usage)
        return ToolCall(name=call["name"], args=args,
                        rationale=f"{self.name} chose {call['name']}")


def list_models(api_key: str | None = None) -> list[str]:
    """Model ids that support generateContent, from the API rather than from memory."""
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    req = urllib.request.Request(f"{API}/models?pageSize=200",
                                 headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return [m["name"].removeprefix("models/") for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]
