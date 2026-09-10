"""Route explicit deployment choices to configured local and remote targets."""

import importlib

from mimir.ports.deployment import DeploymentRequest, KnowledgeDeploymentPort


class KnowledgeDeploymentTargets(KnowledgeDeploymentPort):
    def __init__(self, *, targets: dict[str, dict]) -> None:
        self.targets = {}
        for name, config in targets.items():
            kwargs = dict(config)
            module, cls = kwargs.pop("adapter").rsplit(".", 1)
            self.targets[name] = getattr(importlib.import_module(module), cls)(**kwargs)

    async def list_deployments(self, *, tenant_id: str = "") -> dict:
        targets = []
        for name, adapter in self.targets.items():
            try:
                status = await adapter.list_deployments(tenant_id=tenant_id)
                status["releases"] = [{**r, "target": name} for r in status["releases"]]
                targets.append({"id": name, **status})
            except Exception as exc:
                targets.append(
                    {
                        "id": name,
                        "cluster": name,
                        "namespace": "",
                        "backends": [],
                        "releases": [],
                        "error": str(exc),
                    }
                )
        return {
            "cluster": "",
            "namespace": "",
            "backends": [],
            "targets": targets,
            "releases": [r for t in targets for r in t["releases"]],
        }

    async def deploy(self, request: DeploymentRequest) -> dict:
        if request.target not in self.targets:
            raise ValueError("Select a configured deployment target")
        return await self.targets[request.target].deploy(request)

    def mounted_ports(self) -> list[dict]:
        return [p for adapter in self.targets.values() for p in adapter.mounted_ports()]

    async def discover_mounts(self, tenant_id: str, authorization: str = "") -> list[dict]:
        return [
            {**mount, "name": f"{name}/{mount['name']}"}
            for name, adapter in self.targets.items()
            for mount in await adapter.discover_mounts(tenant_id, authorization)
        ]

    def _resolve(self, name: str):
        target, _, instance = name.partition("/")
        if target not in self.targets or not instance:
            raise ValueError("Select the deployment target and instance")
        return self.targets[target], instance

    async def inspect_deployment(self, name: str, *, tenant_id: str = "") -> dict:
        adapter, instance = self._resolve(name)
        return await adapter.inspect_deployment(instance, tenant_id=tenant_id)

    async def control(self, name: str, action: str, *, tenant_id: str = "") -> dict:
        adapter, instance = self._resolve(name)
        return await adapter.control(instance, action, tenant_id=tenant_id)

    async def close(self) -> None:
        for adapter in self.targets.values():
            await adapter.close()
