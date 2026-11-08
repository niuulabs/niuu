import type {
  ChatMessage,
  InputRequest,
  PermissionRequest,
  RoomParticipant,
  SessionPlan,
} from '@niuulabs/ui';

export interface SessionGateSummary {
  id: string;
  title: string;
  detail: string;
}

export type SessionAttentionItem =
  | { kind: 'gate'; id: string; title: string; detail: string }
  | { kind: 'permission'; id: string; title: string; detail: string }
  | { kind: 'input'; id: string; title: string; detail: string };

export function buildAttentionItems({
  gates,
  permissions,
  inputRequests,
}: {
  gates: readonly SessionGateSummary[];
  permissions: readonly PermissionRequest[];
  inputRequests: readonly InputRequest[];
}): SessionAttentionItem[] {
  return [
    ...gates.map((gate) => ({ kind: 'gate' as const, ...gate })),
    ...permissions.map((permission) => ({
      kind: 'permission' as const,
      id: permission.requestId,
      title: permission.toolName,
      detail: permission.command || permission.description,
    })),
    ...inputRequests.map((request) => ({
      kind: 'input' as const,
      id: request.requestId,
      title: request.questions[0]?.prompt ?? 'The agent has a question',
      detail:
        request.answerable === false ? 'Answer this in the session terminal.' : 'Answer in chat.',
    })),
  ];
}

export type ChecklistStatus = 'pending' | 'in_progress' | 'completed';

export interface ChecklistItem {
  label: string;
  status: ChecklistStatus;
}

function parseChecklist(input: Record<string, unknown> | undefined): ChecklistItem[] {
  const todos = input?.todos;
  if (!Array.isArray(todos)) return [];
  return todos.flatMap((todo): ChecklistItem[] => {
    if (typeof todo !== 'object' || todo === null) return [];
    const { content, status } = todo as { content?: unknown; status?: unknown };
    if (typeof content !== 'string' || !content.trim()) return [];
    return [
      {
        label: content,
        status: status === 'completed' || status === 'in_progress' ? status : 'pending',
      },
    ];
  });
}

/** The agent rewrites its whole list on each update, so only the newest call counts. */
export function latestChecklist(messages: readonly ChatMessage[]): ChecklistItem[] {
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const parts = messages[messageIndex]?.parts ?? [];
    for (let partIndex = parts.length - 1; partIndex >= 0; partIndex -= 1) {
      const part = parts[partIndex];
      if (part?.type === 'tool_use' && part.name === 'TodoWrite') return parseChecklist(part.input);
    }
  }
  return [];
}

function checklistStatus(status: string): ChecklistStatus {
  return status === 'completed' || status === 'in_progress' ? status : 'pending';
}

/**
 * The checklist the panel shows. A runtime that sends plan frames owns the list, even
 * when its plan is empty; the agent's TodoWrite calls are read only for runtimes that
 * never send one.
 */
export function resolveChecklist(
  plan: SessionPlan | null | undefined,
  messages: readonly ChatMessage[],
): ChecklistItem[] {
  if (!plan) return latestChecklist(messages);
  return plan.tasks.flatMap((task): ChecklistItem[] =>
    task.content.trim() ? [{ label: task.content, status: checklistStatus(task.status) }] : [],
  );
}

export function runningPeers(participants: ReadonlyMap<string, RoomParticipant>) {
  return Array.from(participants.values()).filter(
    (participant) => participant.participantType === 'ravn',
  );
}
