from __future__ import annotations

import pytest

from niuu.domain.outcome import OutcomeField
from personas.adapters.inline import InlinePersonaAdapter
from personas.domain.persona import (
    PersonaConfig,
    PersonaConsumes,
    PersonaFanIn,
    PersonaLLMConfig,
    PersonaProduces,
)
from personas.domain.persona_document import (
    PersonaDependency,
    PersonaDependencyResolutionError,
    PersonaDocumentError,
    PortablePersonaCollection,
    PortablePersonaDefinition,
    parse_portable_persona,
    portable_persona_from_config,
    portable_persona_yaml,
    resolve_persona_dependencies,
)


def _complete_persona(prompt: str = "First line.\nSecond line.\n") -> PersonaConfig:
    return PersonaConfig(
        name="reviewer",
        description="Reviews pull requests for correctness.",
        system_prompt_template=prompt,
        permission_mode="read-only",
        llm=PersonaLLMConfig(
            primary_alias="powerful",
            thinking_enabled=True,
            max_tokens=4096,
        ),
        iteration_budget=17,
        produces=PersonaProduces(
            event_type="review.completed",
            event_type_map={"pass": "review.passed", "fail": "review.failed"},
            schema={
                "verdict": OutcomeField(
                    type="enum",
                    description="Review verdict",
                    enum_values=["pass", "fail"],
                ),
                "summary": OutcomeField(
                    type="string",
                    description="Review summary",
                    required=False,
                ),
            },
        ),
        consumes=PersonaConsumes(
            event_types=["code.changed"],
            injects=["repo", "branch"],
            schema={
                "diff": OutcomeField(type="string", description="Patch to review"),
            },
        ),
        fan_in=PersonaFanIn(strategy="all_must_pass", contributes_to="review.verdict"),
        stop_on_outcome=True,
        delivery_workspace_actions=["inspect"],
    )


def test_portable_persona_round_trips_every_source_field_without_prompt_injection() -> None:
    config = _complete_persona()
    document = portable_persona_from_config(config)

    parsed = parse_portable_persona(portable_persona_yaml(document))

    assert parsed == document
    assert parsed.definition == config.to_dict()
    assert parsed.definition["system_prompt_template"] == "First line.\nSecond line.\n"
    assert parsed.revision == f"content-{parsed.digest.removeprefix('sha256:')[:16]}"
    assert parsed.digest == document.digest


def test_the_description_is_optional_and_leaves_a_persona_without_one_unchanged() -> None:
    without = PersonaConfig(name="plain", system_prompt_template="x")
    assert "description" not in without.to_dict()
    assert portable_persona_from_config(without).definition == {
        "name": "plain",
        "system_prompt_template": "x",
    }


def test_a_description_that_is_not_text_is_refused() -> None:
    with pytest.raises(PersonaDocumentError, match="description must be a string"):
        parse_portable_persona(
            {
                "schema_version": 1,
                "id": "p",
                "revision": "r1",
                "definition": {"name": "p", "description": 3},
            }
        )


def _guided(when: object) -> dict:
    return {
        "schema_version": 1,
        "id": "worker",
        "revision": "content-1234567890abcdef",
        "definition": {
            "name": "worker",
            "produces": {
                "event_type_map": {"pass": "work.done", "blocked": "work.blocked"},
                "when": when,
            },
        },
    }


def test_a_portable_persona_says_when_to_report_each_verdict() -> None:
    portable = parse_portable_persona(_guided({"pass": "The work is done and pushed."}))

    assert portable.definition["produces"]["when"] == {"pass": "The work is done and pushed."}


@pytest.mark.parametrize(
    ("when", "message"),
    [
        ({"approved": "Looks good."}, "names verdict 'approved'.*the verdicts are"),
        ({"pass": "  "}, r"produces.when.pass must say when to report it"),
        (["pass"], "produces.when must map each verdict"),
    ],
)
def test_guidance_for_a_verdict_the_persona_cannot_report_or_empty_guidance_is_refused(
    when: object, message: str
) -> None:
    with pytest.raises(PersonaDocumentError, match=message):
        parse_portable_persona(_guided(when))


def test_portable_persona_rejects_an_executor_field() -> None:
    document = {
        "schema_version": 1,
        "id": "worker",
        "revision": "content-1234567890abcdef",
        "definition": {"name": "worker", "executor": {"adapter": "some.module.Executor"}},
    }

    with pytest.raises(PersonaDocumentError, match="unsupported field.*executor"):
        parse_portable_persona(document)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"allowed_tools": ["file_read"]}, "persona worker: allowed_tools is not supported"),
        ({"forbidden_tools": ["shell"]}, "persona worker: forbidden_tools is not supported"),
        (
            {"allowed_tools": [], "forbidden_tools": []},
            "persona worker: allowed_tools and forbidden_tools are not supported",
        ),
    ],
)
def test_portable_persona_refuses_tool_restrictions_even_when_empty(
    fields: dict[str, list[str]], message: str
) -> None:
    document = {
        "schema_version": 1,
        "id": "worker",
        "revision": "content-1234567890abcdef",
        "definition": {"name": "worker", **fields},
    }

    with pytest.raises(PersonaDocumentError, match=message) as raised:
        parse_portable_persona(document)

    assert "per-harness tool restrictions are not available yet" in str(raised.value)
    assert "remove allowed_tools (and forbidden_tools) from the persona" in str(raised.value)


def test_a_portable_persona_built_directly_refuses_tool_restrictions_too() -> None:
    with pytest.raises(PersonaDocumentError, match="allowed_tools is not supported"):
        PortablePersonaDefinition(
            id="worker",
            revision="content-1234567890abcdef",
            definition={"name": "worker", "allowed_tools": ["file_read"]},
        )


def test_pinning_a_workflow_scoped_persona_with_a_tool_restriction_is_refused() -> None:
    """Ting pins a workflow's personas through this resolution; the refusal reaches it too."""
    scoped = {
        "schema_version": 1,
        "id": "worker",
        "revision": "content-1234567890abcdef",
        "definition": {"name": "worker", "forbidden_tools": ["shell"]},
    }
    pin = {"id": "worker", "revision": "content-1234567890abcdef", "digest": "sha256:" + "0" * 64}

    with pytest.raises(PersonaDocumentError, match="persona worker: forbidden_tools is not"):
        resolve_persona_dependencies({"worker": pin}, {"worker": scoped}, None)


def test_persona_dependency_rejects_path_traversal_identifier() -> None:
    with pytest.raises(PersonaDocumentError, match="Persona dependency id"):
        PersonaDependency(
            id="../../outside",
            revision="content-1234567890abcdef",
            digest="sha256:" + "0" * 64,
        )


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            """schema_version: 1
id: reviewer
id: duplicate
revision: initial
definition:
  name: reviewer
""",
            "duplicate key",
        ),
        (
            """schema_version: 1
id: reviewer
revision: initial
definition:
  name: reviewer
  llm:
    api_key: secret
""",
            "llm has unsupported field",
        ),
        (
            """schema_version: 1
id: reviewer
revision: initial
definition: &definition
  name: reviewer
  consumes: *definition
""",
            "unconstructable recursive node",
        ),
        (
            """schema_version: 1
id: reviewer
revision: initial
definition:
  name: reviewer
  produces:
    schema:
      verdict: null
""",
            r"produces\.schema\.verdict must be a mapping",
        ),
    ],
)
def test_portable_persona_rejects_unsafe_or_ambiguous_yaml(payload: str, message: str) -> None:
    with pytest.raises(PersonaDocumentError, match=message):
        parse_portable_persona(payload)


def test_scoped_definition_is_exact_and_never_falls_through_to_mutable_source() -> None:
    pinned = portable_persona_from_config(_complete_persona("Pinned prompt"))
    changed = portable_persona_from_config(
        _complete_persona("Changed prompt"),
        revision=pinned.revision,
    )

    class MutableSource:
        def load_portable(self, persona_id: str, revision: str):  # noqa: ANN201
            assert persona_id == "reviewer"
            assert revision == pinned.revision
            return changed

    resolved = resolve_persona_dependencies(
        {"review": pinned.dependency},
        {"review": pinned.to_dict()},
        MutableSource(),
    )
    assert resolved["review"] == pinned

    with pytest.raises(PersonaDependencyResolutionError, match="digest conflict"):
        resolve_persona_dependencies(
            {"review": pinned.dependency},
            {"review": changed.to_dict()},
            MutableSource(),
        )


def test_portable_collection_resolves_current_and_exact_revision() -> None:
    document = portable_persona_from_config(_complete_persona())
    source = PortablePersonaCollection([document])

    assert source.load_current_portable("reviewer") == document
    assert source.load_portable("reviewer", document.revision) == document


def test_inline_adapter_runs_exact_source_and_only_then_injects_outcome_prompt() -> None:
    document = portable_persona_from_config(_complete_persona("Pinned prompt"))
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    runtime = adapter.load("review")

    assert runtime is not None
    assert runtime.name == "review"
    assert runtime.system_prompt_template.startswith("Pinned prompt")
    assert "---outcome---" in runtime.system_prompt_template
    assert document.definition["system_prompt_template"] == "Pinned prompt"
