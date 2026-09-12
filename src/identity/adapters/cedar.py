"""Cedar implementation of the shared authorization port.

Policies and schema are immutable for the process lifetime. A policy update is
a deployment, never an in-request reload or a fallback to another authorizer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from importlib.resources import files
from pathlib import Path

import cedarpy

from identity.models import Resource
from identity.ports import AuthorizationEvaluationError, AuthorizationPort
from niuu.domain.models import Principal

logger = logging.getLogger(__name__)


class CedarAuthorizationAdapter(AuthorizationPort):
    """Evaluate schema-validated Cedar policies without a network dependency.

    Custom deployments must supply both policy and JSON schema paths. The schema
    declares the supported Niuu resource types and actions. Unknown operations
    deny, malformed data/errors raise, and no decision result is cached.
    """

    def __init__(self, *, policies_path: str = "", schema_path: str = "") -> None:
        if bool(policies_path) != bool(schema_path):
            raise ValueError("Configure both policies_path and schema_path, or neither")
        bundled = files("identity.policies")
        policy_source = Path(policies_path) if policies_path else bundled / "authorization.cedar"
        schema_source = Path(schema_path) if schema_path else bundled / "schema.json"
        policies = policy_source.read_text(encoding="utf-8")
        schema_text = schema_source.read_text(encoding="utf-8")
        self._schema = json.loads(schema_text)
        validation = cedarpy.validate_policies(policies, self._schema)
        if not validation.validation_passed:
            raise ValueError(f"Cedar policy validation failed: {validation.errors}")
        self._policies = cedarpy.PolicySet.from_str(policies)
        if not len(self._policies):
            raise ValueError("Cedar policy set is empty")
        self.policy_version = hashlib.sha256((schema_text + "\n" + policies).encode()).hexdigest()
        self._actions = self._schema["Niuu"]["actions"]

    async def is_allowed(self, principal: Principal, action: str, resource: Resource) -> bool:
        return bool(await self.filter_allowed(principal, action, [resource]))

    async def filter_allowed(
        self, principal: Principal, action: str, resources: list[Resource]
    ) -> list[Resource]:
        if not resources:
            return []
        if not principal.user_id or not principal.tenant_id:
            return []
        action_schema = self._actions.get(action)
        if action_schema is None:
            return []
        supported_types = action_schema["appliesTo"]["resourceTypes"]
        candidates = [r for r in resources if r.kind in supported_types and r.id]
        if not candidates:
            return []

        principal_uid = {"type": "Niuu::User", "id": principal.user_id}
        entities = [
            {
                "uid": principal_uid,
                "attrs": {
                    "user_id": principal.user_id,
                    "email": principal.email,
                    "tenant_id": principal.tenant_id,
                    "roles": principal.roles,
                },
                "parents": [],
            }
        ]
        requests = []
        # Key by type AND id. Conflicting snapshots must not share an allow.
        seen: dict[tuple[str, str], dict] = {}
        for resource in candidates:
            uid = {"type": f"Niuu::{resource.kind}", "id": resource.id}
            attrs = dict(resource.attr)
            for key in ("owner_id", "tenant_id"):
                if attrs.get(key) is None:
                    attrs[key] = ""
            key = (resource.kind, resource.id)
            if key in seen and seen[key] != attrs:
                raise AuthorizationEvaluationError("Conflicting authorization resource snapshots")
            if key not in seen:
                entities.append({"uid": uid, "attrs": attrs, "parents": []})
                seen[key] = attrs
            requests.append(
                {
                    "principal": principal_uid,
                    "action": {"type": "Niuu::Action", "id": action},
                    "resource": uid,
                    "context": {},
                }
            )
        try:
            parsed_entities = cedarpy.Entities.from_json_str(json.dumps(entities), self._schema)
            results = cedarpy.is_authorized_batch(
                requests, self._policies, parsed_entities, schema=self._schema
            )
        except (ValueError, TypeError) as exc:
            logger.error("Cedar evaluation failed policy_version=%s", self.policy_version)
            raise AuthorizationEvaluationError("Cedar request or entity validation failed") from exc

        if len(results) != len(candidates) or any(r.diagnostics.errors for r in results):
            logger.error("Cedar evaluation errors policy_version=%s", self.policy_version)
            raise AuthorizationEvaluationError("Cedar authorization evaluation failed")
        allowed = []
        for resource, result in zip(candidates, results, strict=True):
            logger.info(
                "Authorization decision %s",
                json.dumps(
                    {
                        "principal": principal.user_id,
                        "tenant": principal.tenant_id,
                        "action": action,
                        "kind": resource.kind,
                        "resource": resource.id,
                        "allowed": result.allowed,
                        "policy_version": self.policy_version,
                    }
                ),
            )
            if result.allowed:
                allowed.append(resource)
        return allowed
