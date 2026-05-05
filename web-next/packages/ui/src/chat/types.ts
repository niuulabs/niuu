/** Shared chat types for @niuulabs/ui/chat */

export interface AttachmentMeta {
  name: string;
  type: 'image' | 'file';
  size: number;
  contentType: string;
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
  readonly type: 'text' | 'tool_use' | 'tool_result' | 'reasoning';
  readonly text?: string;
  readonly id?: string;
  readonly name?: string;
  readonly input?: Record<string, unknown>;
  readonly tool_use_id?: string;
  readonly content?: string;
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

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  createdAt: Date;
  status?: 'running' | 'done' | 'error';
  parts?: readonly ChatMessagePart[];
  attachments?: AttachmentMeta[];
  metadata?: {
    messageType?: string;
    usage?: Record<string, { inputTokens?: number; outputTokens?: number }>;
  };
  participant?: ParticipantMeta;
  visibility?: 'visible' | 'internal';
  threadId?: string;
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
  | 'approve'
  | 'pass'
  | 'retry'
  | 'escalate'
  | 'fail'
  | 'needs_changes'
  | 'needs_review';

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
}

export type PermissionBehavior = 'allow_once' | 'allow_always' | 'deny';

export interface FileEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  depth?: number;
  children?: FileEntry[];
}

export interface SessionCapabilities {
  interrupt?: boolean;
  set_model?: boolean;
  set_thinking_tokens?: boolean;
  rewind_files?: boolean;
}
