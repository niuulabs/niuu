"""The session's first message: delivered once, or the owner is told why not.

Forge gives every launch of a session the prompt it was created with, until the session has
run a turn (``volundr.domain.session_initial_prompt``): a launch can end before the harness
ever takes it. Skuld hands it to the harness as an ordinary user turn, under one of Forge's
durable delivery claims, so a broker that starts again knows what became of it:

* no claim, or a claim that was settled ``failed`` (the harness refused it before taking any
  input): nobody has had the prompt, so it is sent;
* ``delivered``: the first turn ran, so nothing is sent;
* ``pending``: it was handed over and nothing confirmed it. Sending it again could run the
  same instruction twice, so the owner is told to check and to send it themself.

A prompt that cannot be sent (the harness does not start, the session takes no messages, Forge
refuses the claim) is a *failed* turn with its reason, in the chat and in the durable log.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from niuu.cap_telemetry import record_degraded
from skuld.conversation_models import ConversationTurn
from skuld.delivery_claims import DeliveryClaimError, claim_message, read_delivery
from skuld.event_log import ForgeCredentialError, sanitize_log

logger = logging.getLogger("skuld.broker")

#: How the first message's turn is told apart from one a person typed.
INITIAL_PROMPT_SOURCE = "initial_prompt"

#: ``record_degraded`` path of a first message that did not reach the harness.
INITIAL_PROMPT_DEGRADED = "skuld.initial_prompt"


class InitialPromptError(RuntimeError):
    """The session's first message was not sent; the text tells the owner what to do."""


class InitialPromptMixin:
    """Own the delivery of ``session.initial_prompt``."""

    async def _auto_start_transport(self) -> None:
        """Start the transport in the background, and hand it the session's first message."""
        if self._settings.session.initial_prompt:
            await self._start_with_initial_prompt()
            return
        await self._warm_transport()

    async def _warm_transport(self) -> None:
        try:
            await self._ensure_transport_started()
            logger.info("Transport auto-started successfully")
        except Exception:
            logger.error("Transport auto-start failed", exc_info=True)

    async def _start_with_initial_prompt(self) -> None:
        """Deliver the first message unless its turn already ran; say so when it cannot be."""
        prompt = self._settings.session.initial_prompt
        try:
            status, request_id = await self._initial_prompt_status(prompt)
            if status == "delivered" or self._first_turn_ran(prompt):
                await self._warm_transport()
                return
            if status == "pending":
                await self._warm_transport()
                raise InitialPromptError(
                    "This message was handed to the agent in an earlier start and nothing "
                    "confirmed that the agent took it, so it was not sent again. Send it "
                    "again if the agent has not acted on it."
                )
            turn = await self._open_initial_prompt_turn(prompt, request_id)
            await self._start_for_initial_prompt()
            await self._claim_initial_prompt(prompt, request_id)
        except InitialPromptError as exc:
            await self._fail_initial_prompt(prompt, exc)
            return
        except Exception as exc:
            # A background task nobody awaits: without this the owner sees only silence.
            logger.exception("The session's first message could not be delivered")
            await self._fail_initial_prompt(
                prompt, InitialPromptError(f"This message was not sent: {exc}")
            )
            return
        await self._spawn_user_delivery(prompt, msg_id=turn.id, request_id=request_id, author=None)

    async def _start_for_initial_prompt(self) -> None:
        try:
            await self._ensure_transport_started()
        except Exception as exc:
            logger.error("Transport auto-start failed", exc_info=True)
            raise InitialPromptError(
                f"The agent did not start, so this message was not sent: {exc}. Fix that and "
                "start the session again to send it."
            ) from exc
        if not self._transport.capabilities.send_message:
            raise InitialPromptError(
                "This session is driven from the agent's own app and takes no messages from "
                "here, so this message was not sent. Send it in that app."
            )

    async def _report_unroutable_initial_prompt(self) -> None:
        """A room with no agent of its own has nobody to hand a first message to."""
        await self._fail_initial_prompt(
            self._settings.session.initial_prompt,
            InitialPromptError(
                "This room has no agent of its own, so this message was not sent to anyone. "
                "Resend the prompt once the room's agents have joined."
            ),
        )

    # -- what became of it before

    async def _initial_prompt_status(self, prompt: str) -> tuple[str, str]:
        """``(status, request_id)`` of the first message: ``unsent``, ``pending`` or ``delivered``.

        ``request_id`` is the claim to send it under. A failed claim is terminal, so each
        attempt after one takes the next id.
        """
        base = f"initial-prompt-{self._initial_prompt_digest(prompt)}"
        if not self.volundr_api_url:
            return self._local_initial_prompt_status(prompt), base
        attempt = 0
        while True:
            attempt += 1
            request_id = base if attempt == 1 else f"{base}-{attempt}"
            status = await self._ask_forge(
                lambda request_id=request_id: self._read_initial_prompt_claim(request_id),
                "read what became of the session's first message",
            )
            if status is None:
                return "unsent", request_id
            if status != "failed":
                return status, request_id

    def _local_initial_prompt_status(self, prompt: str) -> str:
        """Without a Forge there are no claims: the local history's own turn decides."""
        turn = self._initial_prompt_turn(prompt)
        if turn is None:
            return "unsent"
        match turn.metadata.get("steering_state"):
            case "failed":
                return "unsent"
            case "pending":
                return "pending"
        return "delivered"

    def _first_turn_ran(self, prompt: str) -> bool:
        """The agent took the message, or has spoken since: the session's first turn is over,
        whatever its claim says.

        A room's other participants are not this session's agent, and neither is an error: a
        history rebuilt from the log shows every error as an assistant turn, the one that
        reported this message undelivered included.
        """
        turn = self._initial_prompt_turn(prompt)
        if turn is not None and turn.metadata.get("steering_state") == "active":
            return True
        return any(
            turn.role == "assistant"
            and not turn.participant_id
            and turn.metadata.get("status") != "error"
            for turn in self._conversation_turns
        )

    # -- the turn

    @staticmethod
    def _initial_prompt_digest(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def _initial_prompt_turn_id(self, prompt: str) -> str:
        digest = self._initial_prompt_digest(prompt)
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.session_id}:initial-prompt:{digest}"))

    def _initial_prompt_turn(self, prompt: str) -> ConversationTurn | None:
        """The turn an earlier start recorded for it (one written before turn ids were fixed
        is known by its content)."""
        turn_id = self._initial_prompt_turn_id(prompt)
        users = [turn for turn in self._conversation_turns if turn.role == "user"]
        return next(
            (turn for turn in users if turn.id == turn_id),
            next((turn for turn in users if turn.content == prompt), None),
        )

    async def _open_initial_prompt_turn(
        self, prompt: str, request_id: str | None
    ) -> ConversationTurn:
        """The first message's turn, shown as waiting for the agent.

        Recorded before the harness starts: no harness echoes its own input, and without the
        turn the chat would be empty for as long as the harness takes to come up.
        """
        turn = self._initial_prompt_turn(prompt)
        if turn is None:
            await self._record_user_turn(
                prompt,
                msg_id=self._initial_prompt_turn_id(prompt),
                request_id=request_id,
                source=INITIAL_PROMPT_SOURCE,
            )
            return self._initial_prompt_turn(prompt)
        turn.metadata["steering_state"] = "pending"
        self._save_conversation_history()
        await self._emit_broker_frame(
            {
                "type": "user_confirmed",
                "id": turn.id,
                "content": prompt,
                "steering_state": "pending",
                "created_at": turn.created_at,
            }
        )
        return turn

    async def _fail_initial_prompt(self, prompt: str, error: InitialPromptError) -> None:
        turn = self._initial_prompt_turn(prompt)
        if turn is None:
            turn = await self._open_initial_prompt_turn(prompt, None)
        elif turn.metadata.get("steering_state") == "failed":
            return  # an earlier start already told the owner, and it is still true
        record_degraded(INITIAL_PROMPT_DEGRADED, f"session {self.session_id}: {error}")
        await self._fail_user_delivery(turn.id, None, error)

    # -- Forge's claims

    async def _read_initial_prompt_claim(self, request_id: str) -> str | None:
        return await read_delivery(
            await self._get_session_http_client(),
            session_id=self.session_id,
            request_id=request_id,
            timeout=self._settings.delivery.attempt_timeout_seconds,
        )

    async def _claim_initial_prompt(self, prompt: str, request_id: str) -> None:
        """Take the claim that makes this broker the one that sends the prompt."""
        if not self.volundr_api_url:
            return
        token = str(uuid.uuid4())

        async def claim() -> dict:
            return await claim_message(
                await self._get_session_http_client(),
                session_id=self.session_id,
                request_id=request_id,
                content=prompt,
                # The same token on every retry: a claim whose answer was lost is still ours.
                token=token,
                timeout=self._settings.delivery.attempt_timeout_seconds,
                attempts=self._settings.delivery.max_attempts,
            )

        claimed = await self._ask_forge(claim, "claim the session's first message")
        if not claimed["claimed"] or claimed["status"] != "pending":
            raise InitialPromptError(
                "Another broker of this session is sending this message (its delivery is "
                f"{claimed['status']}), so it was not sent from here."
            )
        self._message_claim_tokens[request_id] = token

    async def _ask_forge(self, call: Callable[[], Awaitable[Any]], doing: str) -> Any:
        """Ask Forge, waiting out an outage; an answer no retry can change is an error.

        The prompt waits with it, exactly as the durable log does: without the answer it could
        be sent twice, and the session would otherwise be lost to a moment's outage at boot.
        """
        delay = self.EVENT_LOG_RESUME_INITIAL_DELAY
        while True:
            try:
                return await call()
            except (httpx.HTTPError, ForgeCredentialError, DeliveryClaimError, ValueError) as exc:
                if not self._forge_may_still_answer(exc):
                    raise InitialPromptError(
                        f"Forge could not {doing} ({exc}), so this message was not sent. Fix "
                        "that and start the session again to send it."
                    ) from exc
                logger.error(
                    "Could not %s yet; trying again in %.0fs: %s",
                    doing,
                    delay,
                    sanitize_log(str(exc)),
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.EVENT_LOG_RESUME_MAX_DELAY)

    def _forge_may_still_answer(self, exc: BaseException) -> bool:
        cause = exc.__cause__ if isinstance(exc, DeliveryClaimError) else exc
        if isinstance(cause, ForgeCredentialError):
            return cause.transient
        if isinstance(cause, httpx.HTTPStatusError):
            return self._is_transient_status(cause.response.status_code)
        return isinstance(cause, httpx.HTTPError)
