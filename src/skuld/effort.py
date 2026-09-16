"""Harness-independent effort controls, durable across broker restart/resume."""

import json
import os

from niuu.domain.reasoning import validate_effort


def effort_argument(content: str) -> str | None:
    """Only the exact command token is reserved; ordinary prose remains a prompt."""
    pieces = content.strip().split(maxsplit=1)
    if not pieces or pieces[0].lower() != "/effort":
        return None
    return pieces[1].strip() if len(pieces) > 1 else ""


class EffortControlMixin:
    def _effort_state_path(self):
        return self._conversation_history_path().with_name(f"effort_{self.session_id}.json")

    def _restored_effort(self) -> str:
        path = self._effort_state_path()
        if path.exists():
            saved = json.loads(path.read_text())
            if saved.get("model") == self.model and isinstance(saved.get("effort"), str):
                return saved["effort"]
        return self._settings.session.reasoning_effort

    def _restore_runtime_options(self) -> None:
        """Restore acknowledged choices unless the operator changed launch model.

        Legacy effort files lack launch_model and keep their existing behavior.
        The file contains no native permission/auth configuration.
        """
        path = self._effort_state_path()
        if not path.exists():
            return
        saved = json.loads(path.read_text())
        if saved.get("launch_model") != self._settings.session.model:
            return
        if isinstance(saved.get("model"), str) and saved["model"]:
            self.model = saved["model"]
            self._runtime_service_tier = saved.get("service_tier")

    def _save_effort(self, effort: str) -> None:
        path = self._effort_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(
                {
                    "launch_model": self._settings.session.model,
                    "model": self.model,
                    "effort": effort,
                    "service_tier": getattr(self, "_runtime_service_tier", None),
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    async def handle_runtime_options(
        self, options: dict | None = None, *, refresh: bool = False, request_id: str | None = None
    ) -> dict:
        if self._transport is None:
            raise RuntimeError("The session transport is not ready")
        if not self._transport.capabilities.runtime_options:
            raise ValueError("This harness does not expose runtime options")
        async with self._effort_lock:
            if options is not None:
                await self._transport.send_control("set_runtime_options", options=options)
            state = await self._transport.get_runtime_options(refresh=refresh)
            if options is not None:
                current = state["current"]
                self.model = current["model"]
                self._runtime_service_tier = current.get("service_tier")
                self._save_effort(current["effort"])
            frame = {"type": "runtime_options", "request_id": request_id, **state}
            await self._emit_broker_frame(frame)
            return frame

    async def handle_effort(self, argument: str = "", *, request_id: str | None = None) -> dict:
        """Apply a native control, then publish the acknowledged state to all clients."""
        if self._transport is None:
            raise RuntimeError("The session transport is not ready")
        async with self._effort_lock:
            state = await self._transport.get_effort()
            if argument:
                if not self._transport.capabilities.set_effort or not state.get("mutable"):
                    raise ValueError("This harness does not expose an effort control")
                value = validate_effort(argument, state.get("levels", []))
                await self._transport.send_control("set_effort", effort=value)
                state = await self._transport.get_effort()
                if state.get("current") != value:
                    raise RuntimeError("The harness did not confirm the requested effort")
                self._save_effort(value)
            frame = {"type": "effort_status", "request_id": request_id, **state}
            await self._emit_broker_frame(frame)
            return frame

    async def _deliver_effort_command(
        self, content: str, msg_id: str, request_id: str | None
    ) -> bool:
        argument = effort_argument(content)
        if argument is None:
            return False
        try:
            await self.handle_effort(argument, request_id=request_id)
        except Exception as exc:
            await self._fail_user_delivery(msg_id, request_id, exc)
            return True
        await self._activate_user_turn({"msg_id": msg_id, "request_id": request_id})
        await self._emit_delivery_ack(request_id, msg_id, "delivered")
        return True
