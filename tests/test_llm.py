"""Tolerant structured-output parsing for proxies that ignore strict json_schema."""

import json

import pytest
from pydantic import ValidationError

from web_download.config import Settings
from web_download.llm import _extract_json, _tolerant, build_llms

browser_use = pytest.importorskip("browser_use")


@pytest.fixture(scope="module")
def agent_output():
    """The real AgentOutput browser-use builds for a flash-mode agent."""
    from browser_use.agent.views import AgentOutput
    from browser_use.tools.service import Tools

    action_model = Tools().registry.create_action_model()
    return AgentOutput.type_with_custom_actions_flash_mode(action_model)


# Mirrors the proxy responses that killed the NEJM batch run: a valid object
# followed by a second one the strict parser refuses.
GOOD = '{"action":[{"click":{"index":134}}]}'
TRAILING = GOOD + '{"memory":"prose the provider tacked on"}'

# The other half of that run: a done action whose prose is under the wrong key,
# then one with no prose at all. DoneAction requires "text".
MEMORY = "Both PDFs are in the staging area."
DONE_ALIASED = (
    '{"memory":"' + MEMORY + '",'
    '"action":[{"done":{"success":true,"message":"Downloaded protocol and appendix."}}]}'
)
DONE_TEXTLESS = '{"memory":"' + MEMORY + '","action":[{"done":{"success":true}}]}'
DONE_GOOD = '{"action":[{"done":{"success":true,"text":"Downloaded protocol."}}]}'


def test_strict_parser_rejects_trailing_characters(agent_output):
    with pytest.raises(ValidationError, match="trailing characters"):
        agent_output.model_validate_json(TRAILING)


def test_tolerant_parser_salvages_the_first_object(agent_output):
    from browser_use.agent.views import AgentOutput

    parsed = _tolerant(agent_output).model_validate_json(TRAILING)
    assert isinstance(parsed, AgentOutput)
    assert parsed.action[0].model_dump(exclude_none=True) == {"click": {"index": 134}}


def test_tolerant_parser_leaves_compliant_responses_alone(agent_output):
    parsed = _tolerant(agent_output).model_validate_json(GOOD)
    assert parsed.action[0].model_dump(exclude_none=True) == {"click": {"index": 134}}


def test_tolerant_subclass_keeps_the_wire_schema_identical(agent_output):
    """The provider must still be asked for strict json_schema — only the parse
    on our side is relaxed."""
    from browser_use.llm.schema import SchemaOptimizer

    base = SchemaOptimizer.create_optimized_json_schema(agent_output)
    tolerant = SchemaOptimizer.create_optimized_json_schema(_tolerant(agent_output))
    assert json.dumps(base, sort_keys=True) == json.dumps(tolerant, sort_keys=True)


def test_tolerant_still_raises_on_genuinely_broken_json(agent_output):
    with pytest.raises((ValidationError, ValueError)):
        _tolerant(agent_output).model_validate_json("not json at all")


# The proxy that ignores strict json_schema also wraps the object in a fence or a
# sentence, or returns nothing at all. raw_decode alone reported every one of
# these as "Expecting value: line 1 column 1 (char 0)".
FENCED = "```json\n" + GOOD + "\n```"
PREAMBLE = "Sure, here is the next action:\n" + GOOD
BRACED_PROSE = "I will {now} click the link.\n" + GOOD


@pytest.mark.parametrize("payload", [FENCED, PREAMBLE, BRACED_PROSE])
def test_tolerant_parser_ignores_junk_before_the_object(agent_output, payload):
    parsed = _tolerant(agent_output).model_validate_json(payload)
    assert parsed.action[0].model_dump(exclude_none=True) == {"click": {"index": 134}}


@pytest.mark.parametrize("payload", ["", "   \n ", None])
def test_empty_content_names_the_token_budget(payload):
    """An empty completion is the truncated-by-max_completion_tokens case; the
    error has to say so rather than blaming the JSON."""
    with pytest.raises(ValueError, match="empty message"):
        _extract_json(payload or "")


def test_unparseable_content_is_quoted_in_the_error():
    with pytest.raises(ValueError, match="I cannot access that page"):
        _extract_json("I cannot access that page.")


def test_strict_parser_rejects_done_without_text(agent_output):
    with pytest.raises(ValidationError, match="text"):
        agent_output.model_validate_json(DONE_ALIASED)


def test_tolerant_parser_promotes_an_aliased_done_message(agent_output):
    parsed = _tolerant(agent_output).model_validate_json(DONE_ALIASED)
    done = parsed.action[0].model_dump(exclude_none=True)["done"]
    assert done["text"] == "Downloaded protocol and appendix."
    assert done["success"] is True
    assert "message" not in done


def test_tolerant_parser_falls_back_to_memory_when_done_has_no_prose(agent_output):
    parsed = _tolerant(agent_output).model_validate_json(DONE_TEXTLESS)
    done = parsed.action[0].model_dump(exclude_none=True)["done"]
    assert done["text"] == MEMORY
    assert done["success"] is True


def test_tolerant_parser_leaves_a_well_formed_done_alone(agent_output):
    parsed = _tolerant(agent_output).model_validate_json(DONE_GOOD)
    done = parsed.action[0].model_dump(exclude_none=True)["done"]
    assert done["text"] == "Downloaded protocol."


def test_repair_does_not_swallow_errors_in_other_actions(agent_output):
    """Only ``done`` is repaired; a broken click still fails the step."""
    with pytest.raises(ValidationError):
        _tolerant(agent_output).model_validate_json('{"action":[{"click":{}}]}')


def test_repaired_done_still_dispatches_as_done(agent_output):
    """Tools.act() routes on ``model_dump(exclude_unset=True)`` — a repaired
    action must still come out under the ``done`` key there, or the agent runs
    on past the end of its task."""
    action = _tolerant(agent_output).model_validate_json(DONE_TEXTLESS).action[0]
    dispatched = {k: v for k, v in action.model_dump(exclude_unset=True).items() if v}
    assert list(dispatched) == ["done"]
    assert dispatched["done"]["text"] == MEMORY


def test_build_llms_without_fallback():
    primary, fallback = build_llms(Settings())
    assert fallback is None
    assert primary.model == Settings().agent_model


def test_build_llms_with_fallback():
    settings = Settings(
        fallback_base_url="https://api.openai.com/v1",
        fallback_api_key="sk-fallback",
        fallback_model="gpt-5.4-mini",
    )
    _primary, fallback = build_llms(settings)
    assert fallback is not None
    assert fallback.model == "gpt-5.4-mini"
    assert fallback.base_url == "https://api.openai.com/v1"
