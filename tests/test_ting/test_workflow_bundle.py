"""Archive boundaries for workflow sharing."""

import io
import stat
import zipfile

import pytest

from ting.adapters.workflow_bundle import WorkflowBundleCodec


@pytest.fixture
def codec():
    return WorkflowBundleCodec(max_upload_bytes=4096, max_expanded_bytes=8192, max_entries=8)


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, value in entries:
            output.writestr(name, value)
    return buffer.getvalue()


def test_workflow_and_bundle_round_trip(codec):
    workflow = "schema_version: 1\nname: Résumé\n"
    personas = {"personas/reviewer.yaml": "definition:\n  name: reviewer\n"}
    parsed = codec.read(codec.write(workflow, personas), filename="review.zip")
    assert parsed.workflow == workflow
    assert parsed.files == personas
    assert codec.read(workflow.encode(), filename="review.yml").workflow == workflow


def test_bundle_can_have_one_root_directory(codec):
    parsed = codec.read(
        archive(
            [
                ("review/", ""),
                ("review/workflow.yaml", "name: review"),
                ("review/personas/coder.yaml", "name: coder"),
            ]
        ),
        filename="review.zip",
    )
    assert parsed.files == {"personas/coder.yaml": "name: coder"}


@pytest.mark.parametrize(
    "path",
    [
        "../workflow.yaml",
        "/workflow.yaml",
        "a/../workflow.yaml",
        "a\\workflow.yaml",
        "C:/workflow.yaml",
        "a//workflow.yaml",
    ],
)
def test_rejects_unsafe_paths(codec, path):
    with pytest.raises(ValueError, match="Unsafe"):
        codec.read(archive([(path, "name: invalid")]), filename="review.zip")


def test_rejects_symlink(codec):
    entry = zipfile.ZipInfo("workflow.yaml")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="Symlinks"):
        codec.read(archive([(entry, "outside.yaml")]), filename="review.zip")


@pytest.mark.parametrize(
    "entries, message",
    [
        ([("personas/coder.yaml", "x")], "exactly one"),
        ([("workflow.yaml", "x"), ("nested/workflow.yaml", "x")], "exactly one"),
        ([("workflow.yaml", "x"), ("script.py", "x")], "non-YAML"),
        ([("review/workflow.yaml", "x"), ("other/personas/coder.yaml", "x")], "under"),
        ([("workflow.yaml", "x"), ("unrelated.yaml", "x")], "Unexpected"),
    ],
)
def test_rejects_unexpected_bundle_structure(codec, entries, message):
    with pytest.raises(ValueError, match=message):
        codec.read(archive(entries), filename="review.zip")


def test_bounds_expansion_and_entry_count(codec):
    with pytest.raises(ValueError, match="expanded size"):
        codec.read(archive([("workflow.yaml", "x" * 9000)]), filename="review.zip")
    with pytest.raises(ValueError, match="entry limit"):
        codec.read(archive([(f"{i}.yaml", "") for i in range(9)]), filename="review.zip")
    with pytest.raises(ValueError, match="size limit"):
        codec.read(b"x" * 5000, filename="review.yaml")


def test_rejects_invalid_upload(codec):
    for payload, filename, message in [
        (b"\xff", "review.yaml", "UTF-8"),
        (b"plain text", "review.zip", "Invalid"),
        (b"{}", "review.json", "Upload"),
    ]:
        with pytest.raises(ValueError, match=message):
            codec.read(payload, filename=filename)
