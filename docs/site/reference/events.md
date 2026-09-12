# Events and live streams

Niuu uses events to report changes and streams to deliver live activity. Event
transport, collaboration, and agent decisions are separate layers.

## Follow the owner of the event

Session lifecycle belongs to Forge and its runtime gateway. Workflow progression
belongs to Ting. Knowledge changes belong to Mímir. Ravn owns its decisions and
case continuation. Sleipnir supplies transport abstractions for configured event
backbones; choosing a transport does not define the meaning of an event.

## Three communication paths

Collaboration rooms maintain shared conversational state and replay. Mesh supports
direct communication within a flock. A2A supports interaction with independent
agent systems. Do not interpret a room message as an A2A task unless the application
explicitly translated it into one.

## Debug missing activity

Find the producer and consumer, then verify the event is emitted, routed to the
correct scope, and accepted by the consumer. Check the session/run/case ID and
trace context. A connected transport does not prove that the consumer is subscribed
to the intended subject or that a resident chose to act on the observation.

Reconnecting a browser stream and replaying history should be distinguished from
starting work again. Use [observability](../operations/observability.md) to follow
an operation across the participating services.
