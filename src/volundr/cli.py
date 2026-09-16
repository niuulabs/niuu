"""Small, scriptable Forge REST client for people and coordinator skills.

Authentication stays in the environment. JSON request files preserve the full
REST contract so new harness options do not require a second CLI implementation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import typer

app = typer.Typer(no_args_is_help=True, help="Forge sessions and persistent Git projects.")
projects = typer.Typer(no_args_is_help=True)
sessions = typer.Typer(no_args_is_help=True)
app.add_typer(projects, name="projects")
app.add_typer(sessions, name="sessions")
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass
class Client:
    url: str
    token_env: str
    instance: str | None
    timeout: float

    def request(self, method: str, path: str, body=None, params=None):
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise typer.BadParameter("Use a Forge-relative API path such as /sessions")
        query = dict(params or {})
        if self.instance:
            query["instance_id"] = self.instance
            if method == "POST" and path in {"/sessions", "/projects"}:
                body = {**(body or {}), "instance_id": self.instance}
        token = os.environ.get(self.token_env, "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                response = client.request(
                    method,
                    f"{self.url}/api/v1/forge{path}",
                    headers=headers,
                    params=query,
                    json=body,
                )
        except httpx.TransportError as exc:
            typer.echo(
                f"Forge connection failed ({type(exc).__name__}); retry with the same request ID.",
                err=True,
            )
            raise typer.Exit(2) from exc
        if response.is_error or response.is_redirect:
            # Do not print HTML proxy error pages or request headers containing credentials.
            try:
                detail = response.json().get("detail", response.reason_phrase)
            except (ValueError, AttributeError):
                detail = response.reason_phrase
            typer.echo(f"Forge HTTP {response.status_code}: {detail}", err=True)
            raise typer.Exit(2)
        unavailable = response.headers.get("X-Forge-Unavailable-Instances")
        if unavailable:
            typer.echo(f"Partial mesh response; unavailable hosts: {unavailable}", err=True)
        return response.json() if response.content else {"status": response.status_code}


def output(value):
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


def read_body(filename: str) -> dict:
    try:
        text = (
            typer.get_text_stream("stdin").read() if filename == "-" else Path(filename).read_text()
        )
        value = json.loads(text)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "Request must be a readable JSON file, or '-' for JSON on stdin"
        ) from exc
    if not isinstance(value, dict):
        raise typer.BadParameter("Request JSON must be an object")
    return value


@app.callback()
def configure(
    ctx: typer.Context,
    url: Annotated[str, typer.Option(envvar="FORGE_URL")] = "http://localhost:8080",
    token_env: str = "FORGE_TOKEN",
    instance: Annotated[str | None, typer.Option(envvar="FORGE_INSTANCE_ID")] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
):
    """Choose an API endpoint and optional mesh host. Output is always JSON."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise typer.BadParameter("Use an HTTP(S) Forge URL without embedded credentials")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise typer.BadParameter("Use the Forge server origin without an API path")
    if timeout <= 0:
        raise typer.BadParameter("Timeout must be positive")
    ctx.obj = Client(url.rstrip("/"), token_env, instance, timeout)


@app.command("request")
def request(ctx: typer.Context, method: str, path: str, body: str | None = None):
    """Call any Forge REST operation; --body accepts a JSON file or stdin (-)."""
    output(ctx.obj.request(method.upper(), path, read_body(body) if body else None))


@projects.command("list")
def list_projects(ctx: typer.Context):
    output(ctx.obj.request("GET", "/projects"))


@projects.command("register")
def register(ctx: typer.Context, body: str):
    """Register a meta-repository using its stable project ID and host-local checkout."""
    output(ctx.obj.request("POST", "/projects", read_body(body)))


@projects.command("context")
def context(ctx: typer.Context, project_id: UUID):
    output(ctx.obj.request("GET", f"/projects/{project_id}/context"))


@projects.command("receipts")
def receipts(ctx: typer.Context, project_id: UUID, after: int = 0, limit: int = 100):
    """Read durable handoffs; persist next_cursor only after processing the page."""
    output(
        ctx.obj.request(
            "GET", f"/projects/{project_id}/receipts", params={"after": after, "limit": limit}
        )
    )


@projects.command("handoff")
def handoff(ctx: typer.Context, body: str):
    """Record a handoff JSON document with a stable id, sender, recipient, and evidence."""
    data = read_body(body)
    if not data.get("id") or not data.get("project_id"):
        raise typer.BadParameter("Handoffs require stable id and project_id UUIDs")
    project_id = UUID(data["project_id"])
    output(ctx.obj.request("POST", f"/projects/{project_id}/receipts", data))


@projects.command("ack")
def ack(ctx: typer.Context, project_id: UUID, receipt_id: UUID):
    """Acknowledge a receipt after inspecting it; this does not accept the work."""
    output(ctx.obj.request("POST", f"/projects/{project_id}/receipts/{receipt_id}/ack"))


@projects.command("export")
def export(ctx: typer.Context, project_id: UUID, after: int = 0, limit: int = 100):
    """Atomically export a page of receipts into Git files, without staging or committing."""
    output(
        ctx.obj.request(
            "POST", f"/projects/{project_id}/export", params={"after": after, "limit": limit}
        )
    )


@sessions.command("list")
def list_sessions(
    ctx: typer.Context, project: UUID | None = None, role: str | None = None, archived: bool = False
):
    params = {"include_archived": str(archived).lower()}
    if project:
        params["project_id"] = str(project)
    if role:
        params["role"] = role
    output(ctx.obj.request("GET", "/sessions", params=params))


@sessions.command("create")
def create(ctx: typer.Context, body: str):
    """Launch from SessionCreate JSON. Project launches must include a stable dispatch_id."""
    data = read_body(body)
    if data.get("coordination") and not data.get("dispatch_id"):
        raise typer.BadParameter("Save a dispatch_id UUID in the launch JSON before sending it")
    output(ctx.obj.request("POST", "/sessions", data))


@sessions.command("get")
def get(ctx: typer.Context, session_id: UUID):
    output(ctx.obj.request("GET", f"/sessions/{session_id}"))


@sessions.command("message")
def message(ctx: typer.Context, session_id: UUID, body: str):
    """Send JSON {content, request_id}. A delivery receipt is not task completion."""
    data = read_body(body)
    if not data.get("request_id"):
        raise typer.BadParameter("Save a stable request_id in the message JSON before sending it")
    output(ctx.obj.request("POST", f"/sessions/{session_id}/messages", data))


@sessions.command("stop")
def stop(ctx: typer.Context, session_id: UUID):
    output(ctx.obj.request("POST", f"/sessions/{session_id}/stop"))


@sessions.command("start")
def start(ctx: typer.Context, session_id: UUID):
    output(ctx.obj.request("POST", f"/sessions/{session_id}/start"))


@app.command("new-id")
def new_id():
    """Generate a UUID to save in a project, launch request, or handoff document."""
    typer.echo(str(uuid4()))


def main():
    app()


if __name__ == "__main__":
    main()
