"""LLM plumbing for the browser-use agent.

browser-use asks OpenAI for a strict ``json_schema`` response and then parses it
with ``AgentOutput.model_validate_json``. Some OpenAI-compatible proxies do not
actually enforce strict mode, and the parse dies in two ways we have seen:

* a valid JSON object followed by extra characters -> ``Invalid JSON: trailing
  characters``;
* the object preceded by something — a `````json`` fence, a prose preamble, or
  nothing at all when the provider puts its whole answer in a side channel such
  as ``reasoning_content`` (or truncates on ``max_completion_tokens``, which
  reasoning tokens are billed against, leaving ``content`` empty);
* structurally valid JSON in the wrong shape — most damagingly a ``done`` action
  that names its prose ``message`` instead of ``text``, or omits it entirely.
  ``text`` is the one required field on ``DoneAction``, so the agent cannot say
  it has finished and keeps retrying work it already did.

Either way browser-use counts the parse as a step failure, and enough of those in
a row abort a run that had in fact completed its downloads.

``TolerantChatOpenAI`` keeps the strict request (a compliant provider is
unaffected) but salvages the first complete JSON object wherever it sits in the
response, and repairs a ``done`` action that is missing its ``text``.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ValidationError

from web_download.config import Settings

log = logging.getLogger("web_download")

# Keys a non-compliant provider has put the final prose under instead of
# DoneAction's ``text``, most preferred first.
_DONE_TEXT_ALIASES = ("message", "msg")
_DONE_TEXT_FALLBACK = "Task reported complete without a summary."


def _preview(text: str, limit: int = 200) -> str:
    """``text`` shortened for a log line."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


def _extract_json(text: str) -> Any:
    """Return the first complete JSON object in ``text``, ignoring what surrounds it.

    ``raw_decode`` alone only tolerates junk *after* the object; a provider that
    ignores strict ``json_schema`` also puts junk *before* it — a `````json``
    fence or a sentence of prose — and each ``{`` has to be tried in turn because
    the first one may open a brace inside that prose.

    Raises ``ValueError`` with a preview of the payload when there is no object
    to find, which is what an empty ``content`` looks like. The bare
    ``JSONDecodeError`` this replaces surfaced as an unattributable
    ``ModelProviderError: Expecting value: line 1 column 1 (char 0)``.
    """
    decoder = json.JSONDecoder()
    stripped = text.strip()
    if not stripped:
        raise ValueError(
            "provider returned an empty message; it may have spent the "
            "max_completion_tokens budget on reasoning tokens, or put the answer "
            "in a side channel such as reasoning_content"
        )

    start = 0
    while (opening := stripped.find("{", start)) != -1:
        try:
            obj, _end = decoder.raw_decode(stripped, opening)
        except json.JSONDecodeError:
            start = opening + 1
            continue
        return obj

    raise ValueError(f"no JSON object in provider response: {_preview(stripped)!r}")


def _repair_done(obj: Any) -> Any:
    """Give a ``done`` action the ``text`` field browser-use requires.

    ``text`` is the only required field on ``DoneAction``, so a provider that
    drops it turns a finished run into a step failure. The prose is usually still
    in the payload under another key, and ``AgentOutput.memory`` — required in
    every output mode — summarises what the agent just did, so one of the two is
    almost always a faithful substitute.

    Anything that is not a repairable ``done`` is returned untouched so the
    caller's ``ValidationError`` still surfaces.
    """
    if not isinstance(obj, dict):
        return obj
    actions = obj.get("action")
    if not isinstance(actions, list) or not actions:
        return obj
    done = actions[0].get("done") if isinstance(actions[0], dict) else None
    if not isinstance(done, dict) or done.get("text"):
        return obj

    text = ""
    for alias in _DONE_TEXT_ALIASES:
        # Popped whether or not it is used: DoneAction forbids extra keys, so
        # leaving the alias behind trades one validation error for another.
        value = done.pop(alias, None)
        if not text and isinstance(value, str) and value.strip():
            text = value
    if not text:
        memory = obj.get("memory")
        text = memory if isinstance(memory, str) and memory.strip() else _DONE_TEXT_FALLBACK

    done["text"] = text
    # INFO, unlike the trailing-junk salvage below: this rewrites the agent's
    # final answer and fires at most once a run, so it is worth seeing under -v.
    log.info("repaired a done action missing its text field: %r", text)
    return obj


@lru_cache(maxsize=8)
def _tolerant(model_cls: type[BaseModel]) -> type[BaseModel]:
    """``model_cls`` with a ``model_validate_json`` that ignores trailing junk
    and repairs a ``done`` action missing its ``text``.

    A subclass is used rather than a wrapper so the JSON schema browser-use puts
    on the wire is unchanged and the parsed value is still an instance of the
    original model. Cached because browser-use builds one output model per agent.
    """

    class _Tolerant(model_cls):  # type: ignore[misc,valid-type]
        @classmethod
        def model_validate_json(cls, json_data, **kwargs):  # type: ignore[override]
            try:
                return super().model_validate_json(json_data, **kwargs)
            except ValidationError:
                text = (
                    json_data.decode() if isinstance(json_data, bytes) else json_data
                )
                try:
                    obj = _extract_json(text)
                except ValueError as exc:
                    # WARNING, and carrying the payload: this is the step failure
                    # browser-use reports as a bare ModelProviderError, and the
                    # response itself is the only way to tell the causes apart.
                    log.warning("unparseable response from the provider: %s", exc)
                    raise
                log.debug("salvaged first JSON object from a non-compliant response")
                return super().model_validate(_repair_done(obj), **kwargs)

    _Tolerant.__name__ = model_cls.__name__
    _Tolerant.__qualname__ = model_cls.__qualname__
    return _Tolerant


def _chat_openai_cls():
    """Import lazily so the package imports without browser-use installed."""
    from browser_use.llm import ChatOpenAI

    class TolerantChatOpenAI(ChatOpenAI):  # type: ignore[misc,valid-type]
        """ChatOpenAI for providers that don't honor strict ``json_schema``."""

        async def ainvoke(self, messages, output_format=None, **kwargs):  # type: ignore[override]
            if output_format is not None:
                output_format = _tolerant(output_format)
            return await super().ainvoke(messages, output_format, **kwargs)

    return TolerantChatOpenAI


def build_llms(settings: Settings):
    """Return ``(primary, fallback | None)`` chat models for the agent.

    The primary talks to ``BASE_URL``/``OPENAI_API_KEY``. The fallback is built
    only when ``WD_FALLBACK_BASE_URL`` is set; browser-use switches to it for the
    rest of a run when the primary returns a retryable provider error (401, 402,
    429, 5xx) — which is what a proxy's malformed structured output surfaces as.
    """
    cls = _chat_openai_cls()
    primary = cls(
        model=settings.agent_model,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("BASE_URL"),
        max_completion_tokens=settings.agent_max_completion_tokens,
    )

    if not settings.fallback_base_url:
        return primary, None

    fallback = cls(
        model=settings.fallback_model or settings.agent_model,
        api_key=settings.fallback_api_key or os.getenv("OPENAI_API_KEY"),
        base_url=settings.fallback_base_url,
        max_completion_tokens=settings.agent_max_completion_tokens,
    )
    log.info("fallback LLM configured: %s @ %s", fallback.model, settings.fallback_base_url)
    return primary, fallback
