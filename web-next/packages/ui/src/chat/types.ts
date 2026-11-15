/** Shared chat types for @niuulabs/ui/chat */

export interface AttachmentMeta {
  name: string;
  type: 'image' | 'file';
  size: number;
  contentType: string;
  /** data-URI for an inline image preview (thumbnail), when available */
  previewUrl?: string;
}

export interface ContentBlock {
  type: 'image';
  source: {
    type: 'base64';
    media_type: 'image/jpeg' | 'image/png' | 'image/webp' | 'image/gif';
    data: string;
  };
}

export interface ChatMessagePart {
  readonly type:
    'text' | 'tool_use' | 'tool_result' | 'reasoning' | 'tool_separator' | 'compaction';
  readonly text?: string;
  readonly id?: string;
  /** Native public text identity; phase is preserved even when a provider adds a new value. */
  readonly phase?: string;
  readonly turn_id?: string;
  readonly thread_id?: string;
  readonly index?: number;
  readonly complete?: boolean;
  readonly id_source?: string;
  readonly name?: string;
  readonly input?: Record<string, unknown>;
  readonly tool_use_id?: string;
  readonly content?: unknown;
  readonly is_error?: boolean;
  readonly is_image?: boolean;
  readonly mime_type?: string;
  readonly img_w?: number;
  readonly img_h?: number;
  readonly image_previews?: Array<{
    index: number;
    mime_type?: string;
    img_w?: number;
    img_h?: number;
  }>;
  readonly truncated?: boolean;
  readonly preview?: string;
}

export interface ParticipantMeta {
  peerId: string;
  persona: string;
  displayName?: string;
  color?: string;
  status?: string;
  participantType?: string;
  subscribesTo?: string[];
  emits?: string[];
  tools?: string[];
  /** Gateway URI, e.g. "bifrost://anthropic/claude-sonnet" */
  gateway?: string;
  /** Gateway round-trip latency in milliseconds */
  gatewayLatencyMs?: number;
  /** Gateway region, e.g. "us-east-1" */
  gatewayRegion?: string;
}

export type RoomParticipant = ParticipantMeta;

/**
 * The peer id the chat gives the session's own agent when no room participant names it:
 * the assistant of a session without a mesh room. It is the session itself, never a worker.
 */
export const SESSION_AGENT_PEER_ID = 'skuld-primary';

export interface AgentEventTarget {
  participant: RoomParticipant;
  eventType: string;
}

/**
 * The person who sent a user message, as the broker recorded it from the verified
 * connection. Absent on messages recorded before authorship existed and on messages that
 * were not sent by a verified person.
 */
export interface MessageAuthor {
  /** The identity provider's id for the person. */
  userId: string;
  /** The name to show; empty when the proxy knew neither a display name nor an email. */
  displayName: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  createdAt: Date;
  status?: 'running' | 'done' | 'error';
  parts?: readonly ChatMessagePart[];
  attachments?: AttachmentMeta[];
  historyPreview?: boolean;
  historyMetadataPreview?: boolean;
  metadata?: {
    messageType?: string;
    usage?: Record<string, { inputTokens?: number; outputTokens?: number }>;
  };
  participant?: ParticipantMeta;
  visibility?: 'visible' | 'internal';
  threadId?: string;
  /** Where a user message sent over the session socket is in delivery (user messages only). */
  delivery?: UserMessageDelivery;
  /** Who sent a user message, when the broker recorded it. */
  author?: MessageAuthor;
}

/**
 * The broker's delivery state for a user message.
 *
 * `user_confirmed` with `steering_state: "pending"` means queued behind the running turn;
 * `user_active` / `user_consumed` mean the agent took it; `user_delivered`,
 * `user_delivery_pending` and `user_delivery_failed` report the native send.
 */
export type UserMessageDeliveryState = 'queued' | 'active' | 'delivered' | 'unconfirmed' | 'failed';

export interface UserMessageDelivery {
  state: UserMessageDeliveryState;
  /** The broker's own sentence when the state is `failed` or `unconfirmed`. */
  error?: string;
}

/** One task in the agent's plan (Claude TodoWrite, Codex `turn/plan/updated`), from the `plan` frame. */
export interface SessionPlanTask {
  content: string;
  status: 'pending' | 'in_progress' | 'completed' | string;
  /** Present-tense label for the task while it runs ("Running the tests"). */
  activeForm?: string;
}

export interface SessionPlan {
  tasks: SessionPlanTask[];
  counts: { total: number; pending?: number; in_progress?: number; completed?: number };
}

/** A subagent or teammate the session started, from the `agent_update` frame. */
export interface SessionAgent {
  id: string;
  kind: 'subagent' | 'teammate' | string;
  name: string;
  status: 'running' | 'done' | 'failed' | string;
  description?: string;
  model?: string;
  startedAt?: string;
  endedAt?: string;
}

/** Reasoning effort for the next turn, from the `effort_status` frame. */
export interface SessionEffort {
  current: string | null;
  levels: string[];
  mutable: boolean;
  appliesTo?: string;
}

export interface SessionRuntimeModel {
  id: string;
  model: string;
  name: string;
  description?: string;
  isDefault?: boolean;
  hidden?: boolean;
  effortLevels: string[];
  defaultEffort?: string;
}

/** The session's native model catalog and current choice, from the `runtime_options` frame. */
export interface SessionRuntimeOptions {
  current: { model: string; effort?: string | null };
  models: SessionRuntimeModel[];
  appliesTo?: string;
}

/** Token use reported by the runtime (`session_usage`); `window` is the model's context size. */
export interface SessionUsage {
  model?: string;
  /** Tokens in the context for the latest turn, when the runtime reports it. */
  contextTokens?: number;
  /** The model's context window, when the runtime reports it. */
  window?: number;
  totalInputTokens?: number;
  totalOutputTokens?: number;
}

export interface AgentInternalEvent {
  id: string;
  participantId?: string;
  timestamp?: Date;
  frameType: 'thought' | 'tool_start' | 'tool_result' | string;
  data: string | Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export type MeshVerdict =
  'approve' | 'pass' | 'retry' | 'escalate' | 'fail' | 'needs_changes' | 'needs_review';

interface MeshEventBase {
  id: string;
  timestamp: Date;
  participantId: string;
  participant: { color?: string };
}

export interface MeshOutcomeEvent extends MeshEventBase {
  type: 'outcome';
  persona: string;
  eventType: string;
  verdict?: MeshVerdict;
  summary?: string;
  fields?: Record<string, unknown>;
  valid?: boolean;
}

export interface MeshDelegationEvent extends MeshEventBase {
  type: 'mesh_message';
  fromPersona: string;
  eventType: string;
  preview?: string;
}

export interface MeshNotificationEvent extends MeshEventBase {
  type: 'notification';
  persona: string;
  notificationType: string;
  summary: string;
  reason?: string;
  recommendation?: string;
  urgency: number;
}

export type MeshEvent = MeshOutcomeEvent | MeshDelegationEvent | MeshNotificationEvent;
export type MeshEventType = MeshEvent['type'];

export interface PermissionRequest {
  requestId: string;
  toolName: string;
  description: string;
  input?: Record<string, unknown>;
  command?: string;
}

export type PermissionBehavior = 'allow_once' | 'allow_always' | 'deny';

export interface InputRequest {
  requestId: string;
  questions: Array<{
    prompt: string;
    choices: string[];
    /** Per-choice explanation, aligned with ``choices`` (LEX-1502). */
    descriptions?: Array<string | undefined>;
    /** The question accepts several choices (Muse and Claude can send these). */
    multiSelect?: boolean;
    /** The runtime's question id, echoed in the answer when present. */
    id?: string;
    header?: string;
  }>;
  /** False when no client can answer it (it must be answered in the session's terminal). */
  answerable?: boolean;
  /** Why the last answer was refused (the broker's message); shown on the card. */
  error?: string;
  /** When this client first received the question (ISO 8601); drives the "waiting" timer. */
  receivedAt?: string;
}

/** One structured answer, in the shape Skuld's ``ask_user_answer`` accepts. */
export interface InputAnswer {
  question: string;
  question_id?: string;
  header?: string;
  /** One label or typed text; a list for a multi-select question. */
  answer: string | string[];
  option_indexes: number[];
  /** The typed text, when the answer is not one of the offered choices. */
  free_text?: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  depth?: number;
  children?: FileEntry[];
}

export interface SessionCapabilities {
  interrupt?: boolean;
  /** Messages sent while a turn runs are steered into it (or queued) by the broker. */
  steer?: boolean;
  steering_mode?: 'none' | 'live' | 'native' | string;
  set_effort?: boolean;
  runtime_options?: boolean;
  set_model?: boolean;
  set_thinking_tokens?: boolean;
  rewind_files?: boolean;
  slash_commands?: boolean;
  terminal_output?: boolean;
  terminal_input?: boolean;
  terminal_keys?: boolean;
  terminal_resize?: boolean;
  terminal_panes?: boolean;
  room_prompt_resend?: boolean;
}
