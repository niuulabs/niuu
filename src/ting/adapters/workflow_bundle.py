"""Bounded, non-extracting transport for portable workflow files and bundles."""

from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class WorkflowBundleContents:
    workflow: str
    files: dict[str, str]


class WorkflowBundleCodec:
    """Read archives in memory, never allowing archive names to become disk paths."""

    def __init__(
        self,
        *,
        max_upload_bytes: int,
        max_expanded_bytes: int,
        max_entries: int,
    ) -> None:
        self.max_upload_bytes = max_upload_bytes
        self.max_expanded_bytes = max_expanded_bytes
        self.max_entries = max_entries

    def read(self, content: bytes, *, filename: str) -> WorkflowBundleContents:
        if len(content) > self.max_upload_bytes:
            raise ValueError("Workflow upload exceeds the configured size limit")
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix in {".yaml", ".yml"}:
            return WorkflowBundleContents(self._decode(content), {})
        if suffix != ".zip":
            raise ValueError("Upload a workflow .yaml/.yml file or a .zip bundle")
        try:
            return self._read_zip(content)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise ValueError(f"Invalid workflow ZIP bundle: {exc}") from exc

    def _read_zip(self, content: bytes) -> WorkflowBundleContents:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > self.max_entries:
                raise ValueError("Workflow bundle exceeds the configured entry limit")
            if sum(entry.file_size for entry in entries) > self.max_expanded_bytes:
                raise ValueError("Workflow bundle exceeds the configured expanded size limit")
            seen: set[str] = set()
            files: dict[str, str] = {}
            for entry in entries:
                path = self._safe_path(entry.filename.rstrip("/"))
                if path in seen:
                    raise ValueError(f"Duplicate workflow bundle entry: {path}")
                seen.add(path)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError(f"Symlinks are not allowed in workflow bundles: {path}")
                if entry.flag_bits & 1:
                    raise ValueError("Encrypted workflow bundles are not supported")
                if entry.is_dir():
                    continue
                if PurePosixPath(path).suffix.lower() not in {".yaml", ".yml"}:
                    raise ValueError(f"Unexpected non-YAML workflow bundle entry: {path}")
                with archive.open(entry) as source:
                    payload = source.read(self.max_expanded_bytes + 1)
                if len(payload) > self.max_expanded_bytes or len(payload) != entry.file_size:
                    raise ValueError("Workflow bundle has an invalid expanded entry size")
                files[path] = self._decode(payload)
            candidates = [
                path
                for path in files
                if PurePosixPath(path).name in {"workflow.yaml", "workflow.yml"}
            ]
            if len(candidates) != 1:
                raise ValueError("Workflow bundle must contain exactly one workflow.yaml document")
            workflow_path = candidates[0]
            root = PurePosixPath(workflow_path).parent
            relative: dict[str, str] = {}
            for path, text in files.items():
                if path == workflow_path:
                    continue
                try:
                    name = str(PurePosixPath(path).relative_to(root))
                except ValueError as exc:
                    raise ValueError(
                        "All bundle files must be under the workflow directory"
                    ) from exc
                if not name.startswith(("personas/", "workflows/")):
                    raise ValueError(f"Unexpected workflow bundle entry: {path}")
                relative[name] = text
            return WorkflowBundleContents(files[workflow_path], relative)

    @staticmethod
    def write(workflow: str, personas: dict[str, str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            entries = {"workflow.yaml": workflow}
            for path, content in sorted(personas.items()):
                path = WorkflowBundleCodec._safe_path(path)
                if not path.startswith(("personas/", "workflows/")):
                    raise ValueError("Bundle entries must be under personas/ or workflows/")
                entries[path] = content
            for path, content in entries.items():
                # ZipInfo's fixed epoch avoids embedding wall-clock build times.
                info = zipfile.ZipInfo(path)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        return output.getvalue()

    @staticmethod
    def _safe_path(path: str) -> str:
        parts = path.split("/")
        if (
            not path
            or "\\" in path
            or "\x00" in path
            or ":" in path
            or any(part in {"", ".", ".."} for part in parts)
            or PurePosixPath(path).is_absolute()
        ):
            raise ValueError(f"Unsafe workflow bundle path: {path!r}")
        return path

    @staticmethod
    def _decode(content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Workflow and persona documents must be UTF-8") from exc
