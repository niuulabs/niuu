"""Explicit native-delivery outcomes that are safe for the broker to retry."""


class DeliveryNotAcceptedError(RuntimeError):
    """The provider was not given this input; retry cannot duplicate execution.

    Raise only before input crosses the provider boundary. A lost response,
    failed paste, or general transport exception is not proof of rejection.
    """
