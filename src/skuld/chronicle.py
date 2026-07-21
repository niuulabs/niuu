"""Chronicle, session outcome, archive, and Telegram behavior for Skuld."""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.domain.outcome import parse_outcome_block
from niuu.utils import import_class
from skuld.channels import TelegramChannel
from skuld.conversation_models import CHRONICLE_SUMMARY_PROMPT, SUMMARY_TIMEOUT_SECONDS
from skuld.event_log import FORGE_SESSIONS_PATH
from sleipnir.domain.catalog import ravn_session_ended
from volundr.log_aggregate import aggregate_workspace_logs

logger = logging.getLogger("skuld.broker")


class ChronicleMixin:
    """Broker behavior for outcomes, chronicles, archives, and Telegram."""

    async def _generate_summary(self) -> dict:
        """Ask the CLI to generate a session summary.

        Returns a dict with ``summary``, ``key_changes``, and
        ``unfinished_work`` keys.  Falls back to artifacts data
        when the CLI is unavailable or times out.
        """
        if not self._transport or not self._transport.is_alive:
            logger.info("CLI not alive, skipping AI summary generation")
            return {
                "summary": None,
                "key_changes": self._artifacts.files_changed,
                "unfinished_work": None,
            }

        try:
            await self._transport.send_message(CHRONICLE_SUMMARY_PROMPT)

            # Wait for the result event (set by _handle_cli_message)
            deadline = time.monotonic() + SUMMARY_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                last = self._transport.last_result
                if last is not None:
                    break
                await asyncio.sleep(0.25)

            last = self._transport.last_result
            if last is None:
                logger.warning("Summary generation timed out after %ds", SUMMARY_TIMEOUT_SECONDS)
                return {
                    "summary": None,
                    "key_changes": self._artifacts.files_changed,
                    "unfinished_work": None,
                }

            # Extract text from result
            result_text = last.get("result", "")
            if not result_text:
                # Try to extract from content blocks
                for block in last.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        result_text = block.get("text", "")
                        break

            # Strip markdown fencing if present
            result_text = result_text.strip()
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                lines = lines[1:]  # drop opening fence
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # drop closing fence
                result_text = "\n".join(lines).strip()

            parsed = json.loads(result_text)
            logger.info("AI summary generated successfully")
            return {
                "summary": parsed.get("summary"),
                "key_changes": parsed.get("key_changes", self._artifacts.files_changed),
                "unfinished_work": parsed.get("unfinished_work"),
            }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse summary response: %s", e)
        except Exception:
            logger.warning("Summary generation failed", exc_info=True)

        return {
            "summary": None,
            "key_changes": self._artifacts.files_changed,
            "unfinished_work": None,
        }

    def _fallback_chronicle_summary(self) -> dict[str, Any]:
        """Build a best-effort chronicle summary from already-captured artifacts."""
        summary: str | None = None
        unfinished_work: str | None = None

        if isinstance(self._artifacts.structured_outcome, dict):
            raw_summary = self._artifacts.structured_outcome.get("summary")
            if isinstance(raw_summary, str) and raw_summary.strip():
                summary = raw_summary.strip()

            raw_unfinished = self._artifacts.structured_outcome.get("unfinished_work")
            if isinstance(raw_unfinished, str) and raw_unfinished.strip():
                unfinished_work = raw_unfinished.strip()

        return {
            "summary": summary,
            "key_changes": list(self._artifacts.files_changed),
            "unfinished_work": unfinished_work,
        }

    def _build_transcript(self) -> str:
        """Concatenate all assistant turns to form the session transcript."""
        return "\n\n".join(
            turn.content
            for turn in self._conversation_turns
            if turn.role == "assistant" and turn.content
        )

    def _extract_and_store_outcome(self) -> None:
        """Extract outcome block from the session transcript and store in artifacts.

        No-ops silently when no outcome block is present or parsing fails.
        """
        transcript = self._build_transcript()
        if not transcript:
            return
        try:
            outcome = parse_outcome_block(transcript)
            if outcome is None:
                return
            self._artifacts.structured_outcome = outcome.fields
            self._artifacts.outcome_valid = outcome.valid
        except Exception:
            logger.warning("Failed to extract outcome block from transcript", exc_info=True)

    async def _emit_session_ended_event(self) -> None:
        """Emit ravn.session.ended via Sleipnir with structured outcome and saga/run context.

        Always emits the event — even when outcome extraction failed — so that
        downstream Ting pipeline executors receive session completion signals.
        """
        outcome_str = "SUCCESS" if self._artifacts.outcome_valid else "PARTIAL"
        source = f"ravn:{self.session_id}"
        persona = self._settings.session.name

        try:
            event = ravn_session_ended(
                session_id=self.session_id,
                persona=persona,
                outcome=outcome_str,
                token_count=self._artifacts.total_tokens,
                duration_s=self._artifacts.duration_seconds,
                source=source,
                correlation_id=self.session_id,
            )
            if self._artifacts.structured_outcome is not None:
                event.payload["structured_outcome"] = self._artifacts.structured_outcome
                event.payload["outcome_valid"] = self._artifacts.outcome_valid
                for key in (
                    "verdict",
                    "tests_passing",
                    "scope_adherence",
                    "pr_url",
                    "summary",
                    "files_changed",
                ):
                    if key in self._artifacts.structured_outcome:
                        event.payload[key] = self._artifacts.structured_outcome[key]
            if self._artifacts.files_changed:
                event.payload["files_changed"] = list(self._artifacts.files_changed)
            if self._artifacts.run_id:
                event.payload["run_id"] = self._artifacts.run_id
            if self._artifacts.saga_id:
                event.payload["saga_id"] = self._artifacts.saga_id
            await self._sleipnir_publisher.publish(event)
            logger.info(
                "Session ended event emitted: session=%s outcome=%s saga=%s run=%s",
                self.session_id,
                outcome_str,
                self._artifacts.saga_id,
                self._artifacts.run_id,
            )
        except Exception:
            logger.warning("Failed to emit session ended event", exc_info=True)

    async def _on_result_publish_mesh(self) -> None:
        """Called when a CLI result event arrives (turn finished).

        Extracts outcome from transcript and publishes ``code.changed``
        on the mesh so flock peers (reviewer) can react immediately.
        """
        self._extract_and_store_outcome()
        await self._publish_mesh_outcome()

    async def _git_diff_summary(self) -> str:
        """Return a truncated git diff from the workspace.

        Best-effort: returns an empty string on any failure so mesh
        publishing is never blocked by git issues.
        """
        max_bytes = self._settings.mesh.diff_max_bytes
        timeout = self._settings.mesh.diff_timeout_s

        # Try committed changes first (HEAD~1..HEAD) since the coder
        # session typically commits before finishing.  Fall back to
        # uncommitted working-tree changes (diff HEAD).
        for diff_args in (["git", "diff", "HEAD~1..HEAD"], ["git", "diff", "HEAD"]):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *diff_args,
                    cwd=self.workspace_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except Exception:
                return ""
            raw = stdout.decode(errors="replace")
            if raw.strip():
                if len(raw) > max_bytes:
                    return raw[:max_bytes] + "\n... (truncated)"
                return raw

        return ""

    async def _publish_mesh_outcome(self) -> None:
        """Publish a ``code.changed`` event on the mesh so flock peers react.

        Called after the session completes.  The reviewer ravn subscribes
        to ``code.changed`` and will trigger a review when it receives this.
        """
        if self._mesh_adapter is None:
            return

        from ravn.domain.events import RavnEvent, RavnEventType

        diff_summary = await self._git_diff_summary()

        outcome_payload: dict = {
            "event_type": "code.changed",
            "session_id": self.session_id,
            "persona": self._settings.mesh.persona,
            "summary": (
                f"Session completed"
                f" ({self._artifacts.turn_count} turns,"
                f" {len(self._artifacts.files_changed)} files)"
            ),
            "workspace_path": self.workspace_dir,
        }

        initial_prompt = self._settings.session.initial_prompt
        if initial_prompt:
            outcome_payload["task_description"] = initial_prompt

        if diff_summary:
            outcome_payload["diff_summary"] = diff_summary

        if self._artifacts.structured_outcome is not None:
            outcome_payload["outcome"] = self._artifacts.structured_outcome
        if self._artifacts.files_changed:
            outcome_payload["files_changed"] = list(self._artifacts.files_changed)

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=f"skuld:{self._mesh_adapter.peer_id}",
            payload=outcome_payload,
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id=self.session_id,
            session_id=self.session_id,
        )

        try:
            await self._mesh_adapter._mesh.publish(event, "code.changed")
            logger.info(
                "Mesh: published code.changed (peer=%s, files=%d)",
                self._mesh_adapter.peer_id,
                len(self._artifacts.files_changed),
            )
            # The shared collaboration mesh bridge subscribes to the same NNG bus and will
            # pick this up via loopback (subscriber dials own pub address
            # from cluster.yaml).  No separate broadcast needed.

        except Exception:
            logger.warning("Mesh: failed to publish code.changed", exc_info=True)

    async def _report_chronicle(self) -> None:
        """Report chronicle summary data to the Volundr API on shutdown.

        Mirrors ``_report_usage`` — fires once during shutdown, best-effort,
        never raises.

        Also extracts the outcome block from the session transcript and emits
        the ``ravn.session.ended`` Sleipnir event so Ting can track run completion.
        """
        self._extract_and_store_outcome()
        await self._emit_session_ended_event()
        await self._publish_mesh_outcome()

        # The chronicle SUMMARY (an LLM pass on stop) is opt-in — OFF by default
        # in our pipeline. The session-ended signals above (Ting run tracking,
        # mesh outcome) still fire; only the extra summarization is gated.
        if not self._settings.chronicle_on_stop_enabled:
            return

        if not self.volundr_api_url:
            return

        has_reportable_artifacts = (
            self._artifacts.turn_count > 0
            or bool(self._artifacts.files_changed)
            or self._artifacts.structured_outcome is not None
        )
        if not has_reportable_artifacts:
            logger.info("No chronicle artifacts recorded, skipping chronicle report")
            return

        logger.info(
            "Generating chronicle report (turns=%d, files=%d, duration=%ds)",
            self._artifacts.turn_count,
            len(self._artifacts.files_changed),
            self._artifacts.duration_seconds,
        )

        try:
            summary_data = (
                await self._generate_summary()
                if self._artifacts.turn_count > 0
                else self._fallback_chronicle_summary()
            )

            client = await self._get_http_client()
            url = f"{FORGE_SESSIONS_PATH}/{self.session_id}/chronicle"

            payload: dict = {
                "duration_seconds": self._artifacts.duration_seconds,
            }
            if summary_data.get("summary"):
                payload["summary"] = summary_data["summary"]
            if summary_data.get("key_changes"):
                payload["key_changes"] = summary_data["key_changes"]
            if summary_data.get("unfinished_work"):
                payload["unfinished_work"] = summary_data["unfinished_work"]

            response = await client.post(url, json=payload)
            if response.status_code < 300:
                logger.info("Chronicle report submitted successfully")
            else:
                logger.warning(
                    "Chronicle report failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )

            # Emit session_stop to event pipeline
            await self._emit_pipeline_event(
                "session_stop",
                {
                    "reason": "shutdown",
                    "total_tokens": 0,
                    "duration_seconds": self._artifacts.duration_seconds,
                    "turn_count": self._artifacts.turn_count,
                    "files_changed": len(self._artifacts.files_changed),
                },
            )
        except Exception:
            logger.warning("Failed to report chronicle", exc_info=True)

    async def _write_workspace_archive(self) -> None:
        """Write a workspace-backed archive snapshot for stopped-session reads."""
        try:
            transcript_payload = {
                "turns": [asdict(turn) for turn in self._conversation_turns],
                "is_active": False,
                "last_activity": "",
            }
            aggregated_logs = aggregate_workspace_logs(
                self.workspace_dir,
                lines=5000,
                level="DEBUG",
            )
            workspace_slug = self.workspace_dir.replace("/", "-")
            event_source_dir = Path.home() / ".claude" / "projects" / workspace_slug
            self._archive_store.write_archive(
                session_id=self.session_id,
                workspace_dir=self.workspace_dir,
                transcript_payload=transcript_payload,
                aggregated_logs=aggregated_logs,
                event_source_dir=event_source_dir,
            )
            logger.info("Workspace archive written for session %s", self.session_id)
        except Exception:
            logger.warning("Failed to write workspace archive", exc_info=True)

    async def _init_telegram_channel(self) -> None:
        """Initialize and register a Telegram channel if configured."""
        tg_config = self._settings.telegram
        if not tg_config.enabled:
            return

        bot_token, chat_id = await self._resolve_telegram_credentials()
        if not bot_token or not chat_id:
            logger.warning("Telegram enabled but bot_token or chat_id missing, skipping")
            return

        try:
            channel = TelegramChannel(
                bot_token=bot_token,
                chat_id=chat_id,
                notify_only=tg_config.notify_only,
                topic_mode=tg_config.topic_mode,
                message_thread_id=tg_config.message_thread_id,
                topic_name=self._build_telegram_topic_name(),
                on_message=self._dispatch_browser_message,
            )
            await channel.start()
            self._channels.add(channel)
            logger.info("Telegram channel initialized for chat %s", tg_config.chat_id)
        except RuntimeError:
            logger.warning("python-telegram-bot not installed, Telegram channel disabled")
        except Exception:
            logger.warning("Failed to initialize Telegram channel", exc_info=True)

    async def _resolve_telegram_credentials(self) -> tuple[str, str]:
        """Resolve Telegram credentials from direct config or the shared credential store."""
        tg_config = self._settings.telegram
        bot_token = tg_config.bot_token.strip()
        chat_id = tg_config.chat_id.strip()
        if bot_token and chat_id:
            return bot_token, chat_id

        credential_name = tg_config.credential_name.strip()
        if not credential_name:
            return bot_token, chat_id

        try:
            store_cls = import_class(tg_config.credential_store_adapter)
            store = store_cls(**dict(tg_config.credential_store_kwargs))
            values = await store.get_value(
                tg_config.credential_owner_type,
                tg_config.credential_owner_id,
                credential_name,
            )
        except Exception:
            logger.warning(
                "Failed to resolve Telegram credential %r from %s",
                credential_name,
                tg_config.credential_store_adapter,
                exc_info=True,
            )
            return bot_token, chat_id

        if not values:
            logger.warning(
                "Telegram credential %r not found for %s/%s",
                credential_name,
                tg_config.credential_owner_type,
                tg_config.credential_owner_id,
            )
            return bot_token, chat_id

        return (
            bot_token or str(values.get("bot_token") or values.get("token") or "").strip(),
            chat_id or str(values.get("chat_id") or "").strip(),
        )

    def _build_telegram_topic_name(self) -> str:
        """Build a readable Telegram topic name for the active session."""
        session_name = (self._settings.session.name or "").strip()
        session_id = (self._settings.session.id or "").strip()

        if not session_name or session_name == "unknown":
            session_name = "Volundr session"

        pieces = [session_name]
        if session_id and session_id not in session_name:
            pieces.append(session_id[:8])

        topic_name = " · ".join(piece for piece in pieces if piece).strip()
        topic_name = " ".join(topic_name.split())
        return topic_name[:128] or "Volundr session"
