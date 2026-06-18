"""Protocol classification of a skimmed supplement using OpenAI GPT.

Given the link label plus the text extracted from the first few pages, decide
whether the document is a study protocol / statistical analysis plan. Uses the
OpenAI structured-output API so the response parses directly into
:class:`ProtocolDecision`.
"""

from __future__ import annotations
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from web_download.models import PROTOCOL_DOC_TYPES, ProtocolDecision

# Cap classifier input; the first pages are plenty to identify a protocol.
load_dotenv()
_MAX_CHARS = 6_000

_SYSTEM = (
    "You classify supplementary files attached to medical-journal articles. "
    "Decide whether the document is a clinical STUDY PROTOCOL or STATISTICAL "
    "ANALYSIS PLAN (SAP), versus other supplementary content (eMethods, "
    "supplementary tables/figures, consent forms, etc.). "
    "A study protocol describes trial design, objectives, eligibility, "
    "interventions, endpoints, and procedures; an SAP describes the planned "
    "statistical analyses. Base your decision on the provided text; the link "
    "label is a hint, not proof. Set is_protocol=true only for 'study protocol' "
    "or 'statistical analysis plan'."
)


def _user_prompt(label: str, first_pages_text: str) -> str:
    text = first_pages_text[:_MAX_CHARS]
    return (
        f"Link label: {label!r}\n\n"
        f"First pages of the document:\n---\n{text}\n---\n\n"
        "Return your structured decision."
    )


async def classify(
    label: str,
    first_pages_text: str,
    *,
    model: str,
    client: AsyncOpenAI | None = None,
) -> ProtocolDecision:
    client = client or AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL")
    )
    completion = await client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _user_prompt(label, first_pages_text)},
        ],
        response_format=ProtocolDecision,
        temperature=0,
    )
    decision = completion.choices[0].message.parsed
    assert decision is not None, "structured parse returned no content"
    # Guard against a model that flips is_protocol/doc_type inconsistently.
    decision.is_protocol = decision.doc_type in PROTOCOL_DOC_TYPES
    return decision
