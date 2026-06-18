"""browser-use LLM-agent fallback for locating supplement links.

Used only when no publisher adapter matched, or an adapter found nothing. The
agent (an LLM driving Playwright under the hood) navigates the unfamiliar page
and returns supplement file links as structured JSON, which we map back into
``SupplementCandidate`` objects and feed into the normal skim/classify path.

NOTE: browser-use's API surface changes across releases. This module targets the
``Agent`` + ``ChatOpenAI`` + ``output_model_schema`` shape and degrades to an
empty list (rather than crashing the pipeline) if the call fails.
"""

from __future__ import annotations

import os
import json


from pydantic import BaseModel
from dotenv import load_dotenv
from web_download.models import SupplementCandidate

_TASK = (
    "Go to {url}. Find the article's supplementary / supporting material section. "
    "Collect every downloadable supplementary FILE link (PDF/DOC/XLS/ZIP). "
    "For each, return its visible label text and absolute URL. Do not invent "
    "links; only report links that exist on the page."
)

load_dotenv()


class _AgentLink(BaseModel):
    label: str
    url: str


class _AgentLinks(BaseModel):
    links: list[_AgentLink]


def _parse_links(raw: str | None) -> list[SupplementCandidate]:
    if not raw:
        return []
    try:
        data = _AgentLinks.model_validate_json(raw)
    except Exception:
        try:
            data = _AgentLinks.model_validate(json.loads(raw))
        except Exception:
            return []
    out: list[SupplementCandidate] = []
    for link in data.links:
        if not link.url:
            continue
        ext = link.url.rsplit(".", 1)[-1].lower() if "." in link.url else ""
        out.append(
            SupplementCandidate(
                label=link.label or link.url.rsplit("/", 1)[-1],
                url=link.url,
                file_type=ext
                if ext in {"pdf", "doc", "docx", "xls", "xlsx", "zip"}
                else "pdf",
                source="agent",
            )
        )
    return out


async def find_supplements(
    url: str, *, agent_model: str, headless: bool
) -> list[SupplementCandidate]:
    try:
        from browser_use import Agent, BrowserSession, ChatOpenAI
    except Exception:
        return []

    llm = ChatOpenAI(
        model=agent_model,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("BASE_URL"),
    )
    agent = Agent(
        task=_TASK.format(url=url),
        llm=llm,
        browser_session=BrowserSession(headless=headless),
        output_model_schema=_AgentLinks,
    )
    history = await agent.run()
    return _parse_links(history.final_result())
