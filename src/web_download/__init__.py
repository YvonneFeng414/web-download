"""Pipeline to identify and download protocol PDFs from article web pages."""

from web_download.models import (
    PipelineResult,
    ProtocolDecision,
    SupplementCandidate,
    SupplementResult,
)

__all__ = [
    "PipelineResult",
    "ProtocolDecision",
    "SupplementCandidate",
    "SupplementResult",
]
