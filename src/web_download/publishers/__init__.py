"""Publisher adapter registry."""

from __future__ import annotations

from web_download.publishers.base import PublisherAdapter
from web_download.publishers.bmj import BmjAdapter
from web_download.publishers.jama import JamaAdapter
from web_download.publishers.lancet import LancetAdapter
from web_download.publishers.nejm import NejmAdapter

ADAPTERS: tuple[PublisherAdapter, ...] = (
    JamaAdapter(),
    NejmAdapter(),
    LancetAdapter(),
    BmjAdapter(),
)


def get_adapter(url: str) -> PublisherAdapter | None:
    """Return the first adapter whose publisher owns ``url``, else ``None``."""
    for adapter in ADAPTERS:
        if adapter.matches(url):
            return adapter
    return None


__all__ = ["ADAPTERS", "PublisherAdapter", "get_adapter"]
