"""Explicit native-delivery outcomes that are safe for the broker to retry."""


class DeliveryNotAcceptedError(RuntimeError):
    """The provider was not given this input; retry cannot duplicate execution.

    Raise only before input crosses the provider boundary. A lost response,
    failed paste, or general transport exception is not proof of rejection.
    """


#: Reason code a refused delivery carries while a native control is really on screen.
NATIVE_CONTROL_PENDING_CODE = "native_control_pending"


class NativeControlPendingError(DeliveryNotAcceptedError):
    """A native control (permission menu, question) is live, so nothing was typed.

    Typing into a live Claude menu would answer it: a digit selects a row and Enter
    confirms the highlighted one (Allow). The message is therefore refused, never
    queued or retried; the sender learns which card blocks it and can answer or
    dismiss that card, then send again.
    """

    code = NATIVE_CONTROL_PENDING_CODE

    def __init__(
        self,
        message: str,
        *,
        control_request_id: str,
        control_kind: str,
        opened_at: str | None,
        menu_state: str,
    ) -> None:
        super().__init__(message)
        self.control_request_id = control_request_id
        self.control_kind = control_kind
        self.opened_at = opened_at
        self.menu_state = menu_state

    def details(self) -> dict[str, str | None]:
        """The wire fields a refusal carries (delivery ACK, Forge 409)."""
        return {
            "code": self.code,
            "control_request_id": self.control_request_id,
            "control_kind": self.control_kind,
            "control_opened_at": self.opened_at,
            "menu_state": self.menu_state,
        }


class QuestionCloseUnconfirmedError(DeliveryNotAcceptedError):
    """A message closed a live question, and Claude has not yet confirmed it closed.

    The message itself was not typed, so a retry cannot duplicate it, and the retry
    never presses the closing key again: it waits for the same native proof (LEX-2016).
    """

    code = "question_close_unconfirmed"
