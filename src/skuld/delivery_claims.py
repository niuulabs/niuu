"""Client for Forge's durable duplicate-send claims; contains no native execution."""

import asyncio
import hashlib
from urllib.parse import quote

import httpx


class DeliveryClaimError(RuntimeError):
    def __init__(self, content: str, *, code: str = "message_claim_unavailable") -> None:
        super().__init__(content)
        self.code = code


def delivery_path(session_id: str, request_id: str) -> str:
    return (
        f"/api/v1/forge/sessions/{quote(session_id, safe='')}/message-deliveries/"
        f"{quote(request_id, safe='')}"
    )


async def claim_message(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    request_id: str,
    content: str,
    token: str,
    timeout: float,
    attempts: int,
) -> dict:
    """Retry an uncertain HTTP claim with its original token, never the native send."""
    payload = {
        "payload_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "claim_token": token,
    }
    for attempt in range(attempts):
        try:
            response = await client.post(
                delivery_path(session_id, request_id) + "/claim", json=payload, timeout=timeout
            )
            if response.status_code == 409:
                raise DeliveryClaimError(
                    "This request ID was already used for different message content.",
                    code="request_id_conflict",
                )
            response.raise_for_status()
            result = response.json()
            if (
                not isinstance(result, dict)
                or type(result.get("claimed")) is not bool
                or result.get("request_id") != request_id
                or result.get("status") not in {"pending", "delivered", "failed"}
            ):
                raise ValueError("Invalid delivery claim response")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            if attempt + 1 >= attempts:
                raise DeliveryClaimError(
                    "Forge could not confirm a durable message claim. Delivery is unconfirmed; "
                    "retry with the same request ID."
                ) from exc
            # Yield between retries without inventing an independent timing policy.
            await asyncio.sleep(0)
    raise DeliveryClaimError("No delivery claim attempt was configured")


async def settle_message(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    request_id: str,
    token: str,
    status: str,
    error: str | None,
    timeout: float,
) -> None:
    response = await client.post(
        delivery_path(session_id, request_id) + "/settle",
        json={"claim_token": token, "status": status, "error": error},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != status:
        raise ValueError("Forge did not confirm the delivery settlement")
