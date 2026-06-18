from types import SimpleNamespace

from web_download.agent_fallback import _parse_links
from web_download.classifier import classify
from web_download.models import ProtocolDecision
from web_download.pipeline import _article_id, _safe_name


class _FakeParse:
    """Stand-in for client.beta.chat.completions.parse returning a fixed parsed obj."""

    def __init__(self, decision: ProtocolDecision):
        self._decision = decision
        self.last_kwargs = None

    async def parse(self, **kwargs):
        self.last_kwargs = kwargs
        msg = SimpleNamespace(parsed=self._decision)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    def __init__(self, decision):
        parser = _FakeParse(decision)
        self.chat = SimpleNamespace(completions=parser)
        self._parser = parser


async def test_classify_returns_decision_for_protocol():
    decision = ProtocolDecision(
        is_protocol=True, doc_type="study protocol", confidence=0.93, reason="trial design"
    )
    client = _FakeClient(decision)
    out = await classify("Supplement 2. Trial Protocol", "Study Protocol ...", model="x", client=client)
    assert out.is_protocol is True
    assert out.doc_type == "study protocol"


async def test_classify_guard_overrides_inconsistent_flag():
    # Model says is_protocol True but doc_type is non-protocol -> guard flips it.
    decision = ProtocolDecision(
        is_protocol=True, doc_type="supplementary tables-figures", confidence=0.8, reason="tables"
    )
    out = await classify("Supplement 3. eTables", "eTable 1 ...", model="x", client=_FakeClient(decision))
    assert out.is_protocol is False


def test_parse_links_valid_and_garbage():
    raw = '{"links": [{"label": "Protocol", "url": "https://x.com/p.pdf"}]}'
    cands = _parse_links(raw)
    assert len(cands) == 1
    assert cands[0].url == "https://x.com/p.pdf"
    assert cands[0].source == "agent"
    assert _parse_links("not json") == []
    assert _parse_links(None) == []


def test_pipeline_naming_helpers():
    assert _article_id("https://jamanetwork.com/journals/jama/fullarticle/2466145") == "2466145"
    assert _safe_name("2466145", "study protocol", 2) == "2466145_02_study-protocol.pdf"
