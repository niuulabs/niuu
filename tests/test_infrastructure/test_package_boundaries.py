"""Architecture regression tests for package import direction."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _external_package_imports(package: str) -> set[tuple[str, str]]:
    package_root = SRC_ROOT / package
    imports: set[tuple[str, str]] = set()

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = path.relative_to(package_root).as_posix()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.partition(".")[0]}
            else:
                continue

            for root in roots:
                if root != package:
                    imports.add((relative_path, root))

    return imports


def test_ting_and_volundr_do_not_import_each_other() -> None:
    ting_violations = {
        path for path, imported in _external_package_imports("ting") if imported == "volundr"
    }
    volundr_violations = {
        path for path, imported in _external_package_imports("volundr") if imported == "ting"
    }

    assert ting_violations == set()
    assert volundr_violations == set()


def test_niuu_does_not_import_feature_packages() -> None:
    actual = {
        path
        for path, imported in _external_package_imports("niuu")
        if imported in {"ting", "volundr"}
    }

    assert actual == set()


def test_domain_layers_do_not_read_process_environment() -> None:
    violations: set[str] = set()

    for domain_root in SRC_ROOT.glob("*/domain"):
        for path in domain_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "os.environ" in source or "os.getenv" in source:
                violations.add(path.relative_to(SRC_ROOT).as_posix())

    assert violations == set()


def test_shared_service_domains_do_not_depend_on_volundr() -> None:
    """Only standalone composition roots may wire legacy Volundr adapters/settings."""
    violations: set[str] = set()

    for package in ("identity", "credentials", "tracker", "audit", "features"):
        for path, imported in _external_package_imports(package):
            if imported != "volundr":
                continue
            if path == "app.py":
                continue
            violations.add(f"{package}/{path}")

    assert violations == set()


def test_volundr_shared_service_modules_are_compatibility_exports() -> None:
    expected_imports = {
        "domain/services/credential.py": "credentials",
        "domain/services/feature.py": "features",
        "domain/services/identity.py": "identity",
        "domain/services/mount_strategies.py": "credentials",
        "domain/services/tenant.py": "identity",
        "domain/services/tracker.py": "tracker",
        "domain/services/tracker_factory.py": "tracker",
    }
    actual = _external_package_imports("volundr")

    assert all((path, package) in actual for path, package in expected_imports.items())


def test_volundr_contract_names_alias_canonical_shared_types() -> None:
    from credentials.models import MCPServerConfig, MountType, SecretInfo, SecretMountSpec
    from credentials.ports import (
        MCPServerProvider,
        SecretManager,
        SecretMountStrategy,
    )
    from identity.models import (
        Tenant,
        TenantMembership,
        TenantRole,
        TenantTier,
        User,
        UserStatus,
    )
    from identity.ports import TenantRepository, UserRepository
    from tracker.models import ProjectMapping, TrackerConnectionStatus, TrackerIssue
    from tracker.ports import IssueTrackerProvider, ProjectMappingRepository
    from volundr.domain import models as legacy_models
    from volundr.domain import ports as legacy_ports

    model_aliases = {
        "MCPServerConfig": MCPServerConfig,
        "MountType": MountType,
        "ProjectMapping": ProjectMapping,
        "SecretInfo": SecretInfo,
        "SecretMountSpec": SecretMountSpec,
        "Tenant": Tenant,
        "TenantMembership": TenantMembership,
        "TenantRole": TenantRole,
        "TenantTier": TenantTier,
        "TrackerConnectionStatus": TrackerConnectionStatus,
        "TrackerIssue": TrackerIssue,
        "User": User,
        "UserStatus": UserStatus,
    }
    port_aliases = {
        "IssueTrackerProvider": IssueTrackerProvider,
        "MCPServerProvider": MCPServerProvider,
        "ProjectMappingRepository": ProjectMappingRepository,
        "SecretManager": SecretManager,
        "SecretMountStrategy": SecretMountStrategy,
        "TenantRepository": TenantRepository,
        "UserRepository": UserRepository,
    }

    assert all(getattr(legacy_models, name) is value for name, value in model_aliases.items())
    assert all(getattr(legacy_ports, name) is value for name, value in port_aliases.items())


def test_scoped_api_and_adapters_have_no_behavioral_environment_reads() -> None:
    """Configuration for hardened API/adapter modules must be constructor-injected."""
    targets = (
        "skuld/broker.py",
        "volundr/adapters/inbound/rest.py",
        "volundr/adapters/outbound/local_process.py",
        "volundr/adapters/outbound/openshell_gateway.py",
        "ravn/adapters/mimir/http.py",
    )
    forbidden = ("os.environ.get", "os.getenv", "os.environ[")
    violations = {
        target
        for target in targets
        if any(pattern in (SRC_ROOT / target).read_text(encoding="utf-8") for pattern in forbidden)
    }
    assert violations == set()
