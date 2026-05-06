# Persona Overhaul + Ambient AI Implementation Plan

**Started:** 2026-04-14
**Status:** Planning

## Overview

Three interconnected workstreams to make Ravn personas production-grade:

1. **Persona Overhaul** — Improve existing personas + add new specialists
2. **Escalation Events** — Human-help-needed as mesh events (ambient AI)
3. **Chat Visibility** — Mesh discussions visible in user's chat window

---

## Workstream 1: Persona Overhaul

### Goals
- Increase persona depth from 5-15 lines to 100-300 lines
- Add explicit anti-patterns (what NOT to do)
- Define voice: precise, collaborative, calls out issues without being abrasive
- Add escalation protocols
- Add concreteness requirements

### Voice/Tone Guidelines (All Personas)

```yaml
voice:
  style: precise, collaborative, direct
  principles:
    - State facts, not opinions disguised as facts
    - Name specifics: file paths, line numbers, exact values
    - Call out issues clearly but constructively ("This has a bug" not "This is broken")
    - Recommend, don't mandate — user has context you lack
    - When uncertain, say so explicitly
  anti_patterns:
    - Vague language ("might be slow" → "adds ~200ms per request")
    - Passive deflection ("there seems to be an issue" → "auth.py:47 returns None")
    - Over-confidence ("this will work" → "this should work, verify with X")
    - Filler words (delve, robust, comprehensive, crucial, leverage)
```

### Personas to Improve

| Persona | Current Lines | Target Lines | Key Additions |
|---------|---------------|--------------|---------------|
| `coding-agent` | 15 | 150 | Workflow steps, test-first, anti-patterns |
| `reviewer` | 20 | 200 | Severity rubric, LGTM criteria, blocking vs non-blocking |
| `security-auditor` | 18 | 200 | OWASP checklist, severity levels, false positive handling |
| `qa-agent` | 15 | 150 | Test strategies, coverage requirements, regression checks |
| `planning-agent` | 12 | 150 | Trade-off analysis, option presentation, risk assessment |
| `coordinator` | 15 | 100 | Delegation criteria, synthesis, when NOT to delegate |

### New Personas to Add

| Persona | Purpose | Inspired By |
|---------|---------|-------------|
| `investigator` | Systematic debugging with root cause analysis | gstack `/investigate` |
| `verifier` | Adversarial testing — tries to break things | claude-code-prompts verification-specialist |
| `architect` | Solution architecture with trade-off analysis | claude-code-prompts solution-architect |
| `health-auditor` | Code quality dashboard with metrics | gstack `/health` |
| `office-hours` | Product strategy, ideation, "is this worth building" | gstack `/office-hours` |

### Persona Structure Template

```yaml
name: persona-name
system_prompt_template: |
  ## Identity
  You are a [role]. Your job is to [primary responsibility].

  ## Core Principles
  1. [Principle with concrete example]
  2. [Principle with concrete example]
  3. [Principle with concrete example]

  ## Workflow
  When given a task:
  1. [Step 1 — what to do, what to check]
  2. [Step 2 — conditions, branches]
  3. [Step 3 — verification, output]

  ## Anti-Patterns (DO NOT)
  - [Bad behavior] → instead [good behavior]
  - [Bad behavior] → instead [good behavior]

  ## Escalation Protocol
  STOP and emit `ravn.mesh.help.needed` when:
  - You have attempted the same approach 3+ times without progress
  - You are uncertain about a security-sensitive change
  - The scope exceeds what you can verify
  - You need information that isn't in the codebase

  When escalating, emit:
  ```yaml
  event_type: ravn.mesh.help.needed
  payload:
    persona: [your-name]
    task_id: [current-task]
    reason: [blocked | uncertain | needs_context | scope_exceeded]
    summary: [one sentence explaining what you need]
    attempted: [what you already tried]
    recommendation: [suggested next step for human]
  ```

  ## Output Format
  [Specific format requirements for this persona]

  ## Quality Checklist (before completing)
  - [ ] [Check 1]
  - [ ] [Check 2]
  - [ ] [Check 3]

allowed_tools: [...]
forbidden_tools: [...]
permission_mode: [...]
llm:
  primary_alias: [balanced | powerful | fast]
  thinking_enabled: [true | false]
iteration_budget: [N]
produces:
  event_type: [...]
  schema: [...]
consumes:
  event_types: [...]
```

---

## Workstream 2: Escalation Events (Ambient AI)

### Goals
- When a persona needs human help, emit a mesh event
- User receives notification (not watching logs)
- Event contains enough context to act without reading full history

### New Event Type

```yaml
namespace: ravn
event_type: ravn.mesh.help.needed
```

Register in `src/sleipnir/domain/events.py`:
```python
# In EVENT_NAMESPACES documentation, add:
#   ravn.mesh.help.needed — Agent needs human input to proceed
```

### Event Payload Schema

```python
@dataclass
class HelpNeededPayload:
    persona: str              # Which persona is asking
    peer_id: str              # Which mesh node
    task_id: str | None       # Current task ID (if any)
    reason: str               # blocked | uncertain | needs_context | scope_exceeded
    summary: str              # One sentence: what do you need?
    attempted: list[str]      # What was already tried (max 3 items)
    recommendation: str       # Suggested action for human
    context: dict             # Optional: file paths, error messages, etc.
    urgency: float            # 0.0-1.0, affects notification priority
```

### Implementation Steps

1. **Add `help.needed` to outcome schema** in `PersonaProduces`
   - All personas can emit this event type
   - Schema defined once, shared across personas

2. **Add escalation instructions to all personas**
   - When to escalate (see template above)
   - How to format the escalation

3. **Create HelpNeededHandler in mesh subscriber**
   - Receives `ravn.mesh.help.needed` events
   - Routes to user notification system

4. **Notification delivery options** (pick one or more):
   - **Skuld WebSocket** → Push to browser via RoomBridge (room_message with type "help_needed")
   - **Sleipnir webhook** → HTTP POST to user's notification endpoint
   - **Desktop notification** → If running locally, trigger system notification
   - **Slack/Discord** → Webhook to chat channel (configurable)

### RoomBridge Integration

Extend `RoomBridge` to handle help_needed events:

```python
async def handle_help_needed(self, event: SleipnirEvent) -> None:
    """Translate help.needed event into a room notification."""
    payload = event.payload
    room_event = {
        "type": "room_notification",
        "notificationType": "help_needed",
        "participantId": payload.get("peer_id"),
        "persona": payload.get("persona"),
        "reason": payload.get("reason"),
        "summary": payload.get("summary"),
        "recommendation": payload.get("recommendation"),
        "urgency": event.urgency,
    }
    await self._channels.broadcast(room_event)
```

---

## Workstream 3: Chat Visibility (Mesh Discussions)

### Goals
- User sees mesh agent discussions in their chat window
- Not just "activity indicators" but actual content
- Threaded conversations between agents visible

### Current State

`RoomBridge` already exists with:
- `participant_joined` / `participant_left` events
- `room_message` — agent responses
- `room_activity` — thinking, tool_executing, idle

### Gaps to Fill

| Feature | Current | Target |
|---------|---------|--------|
| Agent-to-agent messages | Not visible | Visible in chat |
| Mesh event cascade | Logs only | Chat thread per cascade |
| Outcome events | Not shown | Summarized in chat |
| Help needed | Not shown | Prominent notification |

### Implementation Steps

1. **Add `mesh_message` event type to RoomBridge**
   - When an agent publishes to mesh, also emit to room
   - Shows as "Agent X → Agent Y: [summary]"

2. **Thread mesh cascades**
   - Group related events by `correlation_id`
   - UI shows as collapsible thread

3. **Outcome summaries**
   - When an agent completes with outcome, emit summary to room
   - Shows as "Agent X completed: [verdict] — [summary]"

4. **Web UI updates** (Skuld frontend)
   - Add `mesh_message` rendering
   - Add `room_notification` for help_needed
   - Add thread grouping by correlation_id

### Wire Protocol Extensions

```typescript
// New room event types
type RoomMeshMessage = {
  type: "room_mesh_message";
  id: string;
  fromPeerId: string;
  fromPersona: string;
  toPeerId: string | null;  // null = broadcast
  toPersona: string | null;
  eventType: string;        // code.changed, review.completed, etc.
  summary: string;
  correlationId: string;
  timestamp: string;
};

type RoomNotification = {
  type: "room_notification";
  notificationType: "help_needed" | "error" | "milestone";
  participantId: string;
  persona: string;
  summary: string;
  urgency: number;
  action?: {
    label: string;
    handler: string;  // e.g., "respond_to_agent"
  };
};

type RoomOutcome = {
  type: "room_outcome";
  participantId: string;
  persona: string;
  eventType: string;
  verdict?: string;
  summary: string;
  correlationId: string;
};
```

---

## Implementation Order

### Phase 1: Foundation (Personas + Events)
1. Define `HelpNeededPayload` schema
2. Register `ravn.mesh.help.needed` event type
3. Create persona template with escalation protocol
4. Improve `reviewer` persona (test case for new depth)
5. Improve `security-auditor` persona
6. Add `investigator` persona

### Phase 2: Event Routing
7. Add mesh event handler for `help.needed`
8. Extend RoomBridge with `handle_help_needed`
9. Wire help_needed to browser notification
10. Test end-to-end: persona escalates → user sees notification

### Phase 3: Chat Visibility
11. Add `room_mesh_message` event type
12. Wire mesh publish to RoomBridge
13. Add `room_outcome` event type
14. Update Skuld web UI to render mesh messages
15. Add thread grouping by correlation_id

### Phase 4: Remaining Personas
16. Improve remaining personas (coding-agent, qa-agent, planning-agent, coordinator)
17. Add remaining new personas (verifier, architect, health-auditor, office-hours)
18. Write tests for persona escalation flows
19. Document new persona authoring guidelines

---

## Files to Create/Modify

### New Files
- `src/ravn/domain/escalation.py` — HelpNeededPayload model
- `src/ravn/adapters/mesh/help_handler.py` — Handles help.needed events
- `src/skuld/room_events.py` — Extended room event types

### Modified Files
- `src/sleipnir/domain/events.py` — Register help.needed
- `src/ravn/adapters/personas/loader.py` — Improved persona definitions
- `src/skuld/room_bridge.py` — Handle mesh events + help_needed
- `src/skuld/broker.py` — Wire mesh events to RoomBridge
- `web/src/components/Room/` — UI for mesh messages + notifications

---

## Success Criteria

1. **Personas**: Each persona has 100+ lines with workflow, anti-patterns, escalation
2. **Escalation**: When a persona emits `help.needed`, user sees notification within 5s
3. **Chat visibility**: Mesh agent discussions appear in chat with threading
4. **Ambient AI**: User doesn't watch logs; relevant info comes to them
