"""PostgreSQL adapter for user-scope launch specs.

Replaces PostgresPresetRepository. User launch specs live in the
``volundr_launch_specs`` table (migrated from ``volundr_presets``).
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from volundr.domain.models import LaunchScope, LaunchSpec
from volundr.domain.ports import LaunchSpecRepository


class PostgresLaunchSpecRepository(LaunchSpecRepository):
    """PostgreSQL implementation of LaunchSpecRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(self, spec: LaunchSpec) -> LaunchSpec:
        await self._pool.execute(
            """
            INSERT INTO volundr_launch_specs
                (id, name, description, is_default, cli_tool, workload_type,
                 session_definition, model, system_prompt, resource_config,
                 mcp_servers, terminal_sidecar, skills, rules, env_vars,
                 env_secret_refs, source, integration_ids, repos, setup_scripts,
                 workspace_layout, workload_config, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, $23, $24)
            """,
            spec.id,
            spec.name,
            spec.description,
            spec.is_default,
            spec.cli_tool,
            spec.workload_type,
            spec.session_definition,
            spec.model,
            spec.system_prompt,
            json.dumps(spec.resource_config),
            json.dumps(spec.mcp_servers),
            json.dumps(spec.terminal_sidecar),
            json.dumps(spec.skills),
            json.dumps(spec.rules),
            json.dumps(spec.env_vars),
            json.dumps(spec.env_secret_refs),
            json.dumps(spec.source.model_dump()) if spec.source else None,
            json.dumps(spec.integration_ids),
            json.dumps(spec.repos),
            json.dumps(spec.setup_scripts),
            json.dumps(spec.workspace_layout),
            json.dumps(spec.workload_config),
            spec.created_at,
            spec.updated_at,
        )
        return spec

    async def get(self, spec_id: UUID) -> LaunchSpec | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM volundr_launch_specs WHERE id = $1", spec_id
        )
        return self._row_to_spec(row) if row is not None else None

    async def get_by_name(self, name: str) -> LaunchSpec | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM volundr_launch_specs WHERE name = $1", name
        )
        return self._row_to_spec(row) if row is not None else None

    async def list(
        self,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[LaunchSpec]:
        conditions: list[str] = []
        params: list = []
        idx = 1
        if cli_tool is not None:
            conditions.append(f"cli_tool = ${idx}")
            params.append(cli_tool)
            idx += 1
        if is_default is not None:
            conditions.append(f"is_default = ${idx}")
            params.append(is_default)
            idx += 1
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM volundr_launch_specs{where} ORDER BY updated_at DESC"
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_spec(row) for row in rows]

    async def update(self, spec: LaunchSpec) -> LaunchSpec:
        await self._pool.execute(
            """
            UPDATE volundr_launch_specs
            SET name = $2, description = $3, is_default = $4, cli_tool = $5,
                workload_type = $6, session_definition = $7, model = $8,
                system_prompt = $9, resource_config = $10, mcp_servers = $11,
                terminal_sidecar = $12, skills = $13, rules = $14, env_vars = $15,
                env_secret_refs = $16, source = $17, integration_ids = $18,
                repos = $19, setup_scripts = $20, workspace_layout = $21,
                workload_config = $22, updated_at = $23
            WHERE id = $1
            """,
            spec.id,
            spec.name,
            spec.description,
            spec.is_default,
            spec.cli_tool,
            spec.workload_type,
            spec.session_definition,
            spec.model,
            spec.system_prompt,
            json.dumps(spec.resource_config),
            json.dumps(spec.mcp_servers),
            json.dumps(spec.terminal_sidecar),
            json.dumps(spec.skills),
            json.dumps(spec.rules),
            json.dumps(spec.env_vars),
            json.dumps(spec.env_secret_refs),
            json.dumps(spec.source.model_dump()) if spec.source else None,
            json.dumps(spec.integration_ids),
            json.dumps(spec.repos),
            json.dumps(spec.setup_scripts),
            json.dumps(spec.workspace_layout),
            json.dumps(spec.workload_config),
            spec.updated_at,
        )
        return spec

    async def delete(self, spec_id: UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM volundr_launch_specs WHERE id = $1", spec_id
        )
        return result == "DELETE 1"

    async def clear_default(self, cli_tool: str) -> None:
        await self._pool.execute(
            """
            UPDATE volundr_launch_specs
            SET is_default = FALSE
            WHERE cli_tool = $1 AND is_default = TRUE
            """,
            cli_tool,
        )

    @staticmethod
    def _json(value: object, fallback: object) -> object:
        if isinstance(value, str):
            return json.loads(value)
        return value if value is not None else fallback

    @classmethod
    def _row_to_spec(cls, row: asyncpg.Record) -> LaunchSpec:
        return LaunchSpec(
            id=row["id"],
            scope=LaunchScope.USER,
            name=row["name"],
            description=row["description"],
            is_default=row["is_default"],
            cli_tool=row["cli_tool"],
            workload_type=row["workload_type"],
            session_definition=row["session_definition"],
            model=row["model"],
            system_prompt=row["system_prompt"],
            resource_config=cls._json(row["resource_config"], {}),
            mcp_servers=cls._json(row["mcp_servers"], []),
            terminal_sidecar=cls._json(row["terminal_sidecar"], {}),
            skills=cls._json(row["skills"], []),
            rules=cls._json(row["rules"], []),
            env_vars=cls._json(row["env_vars"], {}),
            env_secret_refs=cls._json(row["env_secret_refs"], []),
            source=cls._json(row["source"], None),
            integration_ids=cls._json(row["integration_ids"], []),
            repos=cls._json(row["repos"], []),
            setup_scripts=cls._json(row["setup_scripts"], []),
            workspace_layout=cls._json(row["workspace_layout"], {}),
            workload_config=cls._json(row["workload_config"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
