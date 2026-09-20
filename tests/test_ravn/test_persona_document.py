from __future__ import annotations

from dataclasses import replace

import pytest

from niuu.domain.outcome import OutcomeField
from ravn.adapters.personas.inline import InlinePersonaAdapter
from ravn.adapters.personas.loader import (
    PersonaConfig,
    PersonaConsumes,
    PersonaExecutorConfig,
    PersonaFanIn,
    PersonaLLMConfig,
    PersonaProduces,
)
from ravn.domain.persona_document import (
    PersonaDependency,
    PersonaDependencyResolutionError,
    PersonaDocumentError,
    PersonaNonPortableError,
    PortablePersonaCollection,
    parse_portable_persona,
    portable_persona_from_config,
    portable_persona_yaml,
    resolve_persona_dependencies,
)


def _complete_persona(prompt: str = "First line.\nSecond line.\n") -> PersonaConfig:
    return PersonaConfig(
        name="reviewer",
        system_prompt_template=prompt,
        allowed_tools=["file", "git"],
        forbidden_tools=["terminal"],
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


def test_portable_persona_rejects_local_executor_binding() -> None:
    config = replace(
        _complete_persona(),
        executor=PersonaExecutorConfig(
            adapter="ravn.adapters.executors.cli.CliTransportExecutor",
            kwargs={"token": "must-not-export"},
        ),
    )

    with pytest.raises(PersonaNonPortableError, match="local executor binding"):
        portable_persona_from_config(config)


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
