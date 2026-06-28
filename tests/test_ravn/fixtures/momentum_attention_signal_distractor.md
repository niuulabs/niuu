# Resident Inbox Signal: Unrelated shell preference reminder

- id: sig-attention-distractor
- source: proof:fixture
- kind: operator.directed_message
- classification: preference
- confidence: 0.70
- status: new
- target_objective_id: 
- observed_at: 2026-06-28T10:04:00Z
- created_at: 2026-06-28T10:04:00+00:00
- processed_at: 

## Summary

Unrelated shell preference reminder.

## Reason

Fixture distractor for NIU-1077 attention proof.

<resident-inbox-signal-json>
{
  "classification": "preference",
  "confidence": 0.7,
  "created_at": "2026-06-28T10:04:00+00:00",
  "evidence_refs": [
    "proof:distractor"
  ],
  "id": "sig-attention-distractor",
  "kind": "operator.directed_message",
  "observed_at": "2026-06-28T10:04:00Z",
  "payload": {
    "content": "# Preference Note\n\nWhen editing shell snippets later, prefer concise zsh examples. This does not address the Momentum current-state attention tension."
  },
  "processed_at": "",
  "raw_ref": "proof:signal:distractor",
  "reason": "Fixture distractor for NIU-1077 attention proof.",
  "source": "proof:fixture",
  "status": "new",
  "summary": "Unrelated shell preference reminder.",
  "target_objective_id": ""
}
</resident-inbox-signal-json>
