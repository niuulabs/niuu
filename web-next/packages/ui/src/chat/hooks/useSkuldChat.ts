import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAuthHeaders } from '@niuulabs/query';
import { extractOutcomeBlock } from '../components/OutcomeCard';
import type {
  AgentInternalEvent,
  AgentEventTarget,
  AttachmentMeta,
  ChatMessage,
  ChatMessagePart,
  ContentBlock,
  InputRequest,
  MeshEvent,
  MeshOutcomeEvent,
  PermissionBehavior,
  PermissionRequest,
  RoomParticipant,
  SessionCapabilities,
} from '../types';
import type { SlashCommand } from '../utils/slashCommands';
import type { FileAttachment } from './useFileAttachments';
import { useWebSocket } from './useWebSocket';
import { wsUrlToHttpBase } from '../transport';
import {
  fetchHistoryBatch,
  historySocketUrl,
  isHistoryRecovery,
  HistoryChangedError,
  type HistoryPage,
  HISTORY_PAGE_SIZE,
} from './historyPaging';
import { repairCanonicalText } from './canonicalTextRepair';
import { reconcileHistoryMessages } from './historyReconciliation';
import {
  foldPublicText,
  publicTextContent,
  upsertToolPart,
  validateTextReceipt,
} from './orderedPublicText';

const HISTORY_RETRY_DELAY_MS = 1000;
const MAX_HISTORY_RETRIES = 2;

type WireParticipant = {
  peer_id?: string;
  peerId?: string;
  persona?: string;
  display_name?: string;
  displayName?: string;
  color?: string;
  participant_type?: string;
  participantType?: string;
  gateway_url?: string;
  gatewayUrl?: string;
  gateway_latency_ms?: number;
  gatewayLatencyMs?: number;
  gateway_region?: string;
  gatewayRegion?: string;
  subscribes_to?: string[];
  emits?: string[];
  tools?: string[];
  status?: string;
  activityType?: string;
};

type CliStreamEvent = {
  type: string;
  subtype?: string;
  id?: string;
  item_id?: string;
  index?: number;
  phase?: string;
  turn_id?: string;
  projection_revision?: string;
  complete?: boolean;
  text_bytes?: number;
  text_sha256?: string;
  content?: string | Array<{ type: string; text?: string }>;
  result?: string;
  error?: string | { message?: string; code?: string };
  message?: {
    model?: string;
    usage?: { input_tokens?: number; output_tokens?: number };
    content?: Array<{
      type: string;
      text?: string;
      id?: string;
      phase?: string;
      turn_id?: string;
      thread_id?: string;
      index?: number;
      name?: string;
      input?: Record<string, unknown>;
      tool_use_id?: string;
      content?: unknown;
      is_error?: boolean;
      truncated?: boolean;
      preview?: string;
      is_image?: boolean;
      mime_type?: string;
      img_w?: number;
      img_h?: number;
      image_previews?: ChatMessagePart['image_previews'];
    }>;
  };
  content_block?: {
    type?: string;
    text?: string;
    id?: string;
    phase?: string;
    turn_id?: string;
    thread_id?: string;
    index?: number;
    name?: string;
    tool_use_id?: string;
    input?: Record<string, unknown>;
    content?: unknown;
    is_error?: boolean;
    is_image?: boolean;
    truncated?: boolean;
    preview?: string;
    mime_type?: string;
    img_w?: number;
    img_h?: number;
    image_previews?: ChatMessagePart['image_previews'];
  };
  delta?: {
    type?: string;
    text?: string;
    thinking?: string;
    partial_json?: string;
  };
  usage?: { output_tokens?: number };
  total_cost_usd?: number;
  num_turns?: number;
  is_error?: boolean;
  valid?: boolean;
  request_id?: string;
  code?: string;
  questions?: Array<{
    question?: string;
    options?: Array<{ label?: string } | unknown>;
  }>;
  tool?: string;
  input?: Record<string, unknown>;
  participant?: WireParticipant;
  participants?: WireParticipant[];
  participantId?: string;
  participant_id?: string;
  role?: string;
  created_at?: string;
  thread_id?: string;
  visibility?: 'visible' | 'internal';
  metadata?: Record<string, unknown>;
  eventType?: string;
  verdict?: string;
  summary?: string;
  reason?: string;
  recommendation?: string;
  urgency?: number;
  fromPersona?: string;
  preview?: string;
  notificationType?: string;
  persona?: string;
  activityType?: string;
  status?: string;
  frame?: {
    type?: string;
    data?: string | Record<string, unknown>;
    metadata?: Record<string, unknown>;
  };
  slash_commands?: SlashCommandWireItem[];
  skills?: SlashCommandWireItem[];
  commands?: SlashCommandWireItem[];
  turns?: ConversationTurn[];
  fields?: Record<string, unknown>;
};

type SlashCommandWireItem =
  | string
  | {
      name?: string;
      command?: string;
      description?: string;
      argument_hint?: string;
      kind?: string;
      type?: string;
      source?: string;
    };

type SlashCommandsPayload = {
  commands?: SlashCommandWireItem[];
  slash_commands?: SlashCommandWireItem[];
  skills?: SlashCommandWireItem[];
};

export interface ConversationTurn {
  id: string;
  role: string;
  content: string;
  parts?: Array<Record<string, unknown>>;
  created_at: string;
  metadata?: Record<string, unknown>;
  participant_id?: string;
  participant_meta?: Record<string, unknown>;
  thread_id?: string;
  visibility?: 'visible' | 'internal';
  in_progress?: boolean;
  history_preview?: boolean;
  history_metadata_preview?: boolean;
}

interface UseSkuldChatResult {
  messages: ChatMessage[];
  streamingContent?: string;
  streamingParts?: ChatMessagePart[];
  streamingModel?: string;
  connected: boolean;
  historyLoaded: boolean;
  historyError: string | null;
  retryHistory: () => void;
  hasOlderHistory: boolean;
  loadingOlderHistory: boolean;
  olderHistoryError: string | null;
  loadOlderHistory: () => Promise<void>;
  participants: ReadonlyMap<string, RoomParticipant>;
  meshEvents: MeshEvent[];
  agentEvents: ReadonlyMap<string, readonly AgentInternalEvent[]>;
  pendingPermissions: PermissionRequest[];
  pendingInputRequests: InputRequest[];
  availableCommands: SlashCommand[];
  capabilities: SessionCapabilities;
  sendMessage: (text: string, attachments: FileAttachment[]) => void;
  sendDirectedMessages: (
    participants: RoomParticipant[],
    text: string,
    attachments: FileAttachment[],
  ) => void;
  publishEvent: (target: AgentEventTarget, text: string) => void;
  sendResendPrompt: () => void;
  respondToPermission: (requestId: string, behavior: PermissionBehavior) => void;
  respondToInput: (requestId: string, values: string[]) => void;
  sendInterrupt: () => void;
  sendSetModel: (model: string) => void;
  sendSetThinkingTokens: (tokens: number) => void;
  sendRewindFiles: () => void;
  sendSetInternalVisibility: (visible: boolean) => void;
  clearMessages: () => void;
}

const SINGLE_PARTICIPANT_ID = 'skuld-primary';
const STORAGE_PREFIX = 'niuu.skuldChat.v2.';

type PersistedAgentEvent = Omit<AgentInternalEvent, 'timestamp'> & { timestamp?: string };
type PersistedChatState = {
  projectionRevision?: string;
  messages?: Array<Omit<ChatMessage, 'createdAt'> & { createdAt: string }>;
  meshEvents?: Array<
    Omit<MeshEvent, 'timestamp'> & {
      timestamp: string;
    }
  >;
  participants?: RoomParticipant[];
  agentEvents?: Record<string, PersistedAgentEvent[]>;
};

type InternalParticipantStream = {
  messageId: string;
  parts: ChatMessagePart[];
  currentToolId: string;
};

function commandName(value: string): string {
  return value.trim().replace(/^\/+/, '').trim();
}

function normalizeSlashCommandItem(
  item: SlashCommandWireItem,
  fallbackType: SlashCommand['type'],
): SlashCommand | null {
  if (typeof item === 'string') {
    const name = commandName(item);
    return name ? { name, type: fallbackType } : null;
  }

  const rawName = item.name ?? item.command ?? '';
  const name = commandName(rawName);
  if (!name) return null;
  const kind = (item.kind ?? item.type ?? item.source ?? '').toLowerCase();
  const type: SlashCommand['type'] =
    kind === 'skill' || item.source === 'skill' ? 'skill' : fallbackType;
  return {
    name,
    type,
    description: item.description,
    ...(item.argument_hint ? { argumentHint: item.argument_hint } : {}),
  };
}

function normalizeAvailableCommands(
  slashCommands: SlashCommandWireItem[] = [],
  skills: SlashCommandWireItem[] = [],
): SlashCommand[] {
  const deduped = new Map<string, SlashCommand>();
  for (const item of slashCommands) {
    const command = normalizeSlashCommandItem(item, 'command');
    if (command && !deduped.has(command.name)) deduped.set(command.name, command);
  }
  for (const item of skills) {
    const command = normalizeSlashCommandItem(item, 'skill');
    if (command && !deduped.has(command.name)) deduped.set(command.name, command);
  }
  return Array.from(deduped.values());
}

function parseAdvertisedSlashCommand(
  input: string,
  availableCommands: readonly SlashCommand[],
): { command: string; arguments: string } | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith('/')) return null;

  const match = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(trimmed);
  const command = commandName(match?.[1] ?? '');
  if (!command) return null;

  const isAdvertised = availableCommands.some((item) => commandName(item.name) === command);
  if (!isAdvertised) return null;

  return {
    command: `/${command}`,
    arguments: match?.[2] ?? '',
  };
}

export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function getStorageKey(url: string): string {
  return `${STORAGE_PREFIX}${url}`;
}

export function safeSessionStorageGet(url: string): PersistedChatState | null {
  try {
    const raw = sessionStorage.getItem(getStorageKey(url));
    if (!raw) return null;
    return JSON.parse(raw) as PersistedChatState;
  } catch {
    return null;
  }
}

export function safeSessionStorageSet(url: string, state: PersistedChatState): void {
  try {
    sessionStorage.setItem(getStorageKey(url), JSON.stringify(state));
  } catch {
    // sessionStorage may be unavailable or full
  }
}

export function makeSingleParticipant(): RoomParticipant {
  return {
    peerId: SINGLE_PARTICIPANT_ID,
    persona: 'Skuld',
    displayName: 'Skuld',
    color: 'brand',
    participantType: 'skuld',
    status: 'idle',
  };
}

export function parseParticipantMeta(
  raw: WireParticipant | Record<string, unknown> | undefined,
): RoomParticipant | undefined {
  if (!raw) return undefined;
  const peerId = getString(raw, 'peer_id', 'peerId');
  if (!peerId) return undefined;
  return {
    peerId,
    persona: getString(raw, 'persona') ?? '',
    displayName: getString(raw, 'display_name', 'displayName') ?? '',
    color: getString(raw, 'color') ?? '',
    participantType: getString(raw, 'participant_type', 'participantType') ?? 'ravn',
    subscribesTo: getStringArray(raw, 'subscribes_to'),
    emits: getStringArray(raw, 'emits'),
    tools: getStringArray(raw, 'tools'),
    gateway: getString(raw, 'gateway_url', 'gatewayUrl'),
    gatewayLatencyMs: getNumber(raw, 'gateway_latency_ms', 'gatewayLatencyMs'),
    gatewayRegion: getString(raw, 'gateway_region', 'gatewayRegion'),
    status: getString(raw, 'status', 'activityType'),
  };
}

export function getString(
  raw: WireParticipant | Record<string, unknown>,
  ...keys: string[]
): string | undefined {
  const values = raw as Record<string, unknown>;
  for (const key of keys) {
    const value = values[key];
    if (typeof value === 'string') return value;
  }
  return undefined;
}

export function getNumber(
  raw: WireParticipant | Record<string, unknown>,
  ...keys: string[]
): number | undefined {
  const values = raw as Record<string, unknown>;
  for (const key of keys) {
    const value = values[key];
    if (typeof value === 'number') return value;
  }
  return undefined;
}

export function getStringArray(
  raw: WireParticipant | Record<string, unknown>,
  key: string,
): string[] | undefined {
  const value = (raw as Record<string, unknown>)[key];
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === 'string')
    : undefined;
}

export function reviveMessages(messages: PersistedChatState['messages']): ChatMessage[] {
  return (messages ?? [])
    .filter((message) => message.status !== 'running')
    .map((message) => ({
      ...message,
      createdAt: new Date(message.createdAt),
    }));
}

export function reviveMeshEvents(events: PersistedChatState['meshEvents']): MeshEvent[] {
  return (events ?? []).map((event) => ({
    ...event,
    timestamp: new Date(event.timestamp),
  })) as MeshEvent[];
}

export function reviveAgentEvents(
  events: PersistedChatState['agentEvents'],
): Map<string, AgentInternalEvent[]> {
  const next = new Map<string, AgentInternalEvent[]>();
  for (const [peerId, peerEvents] of Object.entries(events ?? {})) {
    next.set(
      peerId,
      peerEvents.map((event) => ({
        ...event,
        timestamp: event.timestamp ? new Date(event.timestamp) : undefined,
      })),
    );
  }
  return next;
}

type RevivedPersistedState = {
  messages: ChatMessage[];
  participants: Map<string, RoomParticipant>;
  meshEvents: MeshEvent[];
  agentEvents: Map<string, AgentInternalEvent[]>;
};

function revivePersistedState(url: string | null): RevivedPersistedState {
  const cached = url ? safeSessionStorageGet(url) : null;
  return {
    messages: reviveMessages(cached?.messages).slice(-HISTORY_PAGE_SIZE),
    participants: new Map(
      (cached?.participants ?? []).map((participant) => [participant.peerId, participant]),
    ),
    meshEvents: reviveMeshEvents(cached?.meshEvents),
    agentEvents: reviveAgentEvents(cached?.agentEvents),
  };
}

export function serializeMessages(messages: ChatMessage[]): PersistedChatState['messages'] {
  return messages
    .filter((message) => message.status !== 'running')
    .map((message) => ({
      ...message,
      createdAt: message.createdAt.toISOString(),
    }));
}

export function serializeMeshEvents(meshEvents: MeshEvent[]): PersistedChatState['meshEvents'] {
  return meshEvents.map((event) => ({
    ...event,
    timestamp: event.timestamp.toISOString(),
  }));
}

export function serializeAgentEvents(
  agentEvents: Map<string, AgentInternalEvent[]>,
): PersistedChatState['agentEvents'] {
  return Object.fromEntries(
    Array.from(agentEvents.entries()).map(([peerId, events]) => [
      peerId,
      events.map((event) => ({
        ...event,
        timestamp: event.timestamp?.toISOString(),
      })),
    ]),
  );
}

export function transformTurns(turns: ConversationTurn[]): ChatMessage[] {
  return turns.map((turn) => {
    const metadata = turn.metadata as ChatMessage['metadata'] | undefined;
    const metadataStatus = (turn.metadata as Record<string, unknown> | undefined)?.status;
    return {
      id: turn.id,
      role: turn.role === 'user' ? 'user' : 'assistant',
      content: turn.content,
      createdAt: new Date(turn.created_at),
      status: metadataStatus === 'error' ? 'error' : turn.in_progress ? 'running' : 'done',
      parts: turn.parts as ChatMessagePart[] | undefined,
      metadata,
      historyPreview: turn.history_preview,
      historyMetadataPreview: turn.history_metadata_preview,
      participant: parseParticipantMeta(
        turn.participant_meta as Record<string, unknown> | undefined,
      ),
      threadId: turn.thread_id,
      visibility: turn.visibility,
    };
  });
}

export function participantsFromTurns(turns: ConversationTurn[]): Map<string, RoomParticipant> {
  const next = new Map<string, RoomParticipant>();
  for (const turn of turns) {
    const participant = parseParticipantMeta(
      turn.participant_meta as Record<string, unknown> | undefined,
    );
    if (participant?.peerId) {
      next.set(participant.peerId, participant);
    }
  }
  const hasSkuldObserver = Array.from(next.values()).some(
    (participant) => participant.participantType === 'skuld',
  );
  const hasRavnPeer = Array.from(next.values()).some(
    (participant) => participant.participantType === 'ravn',
  );
  if (!hasSkuldObserver && hasRavnPeer) {
    const observer = makeSingleParticipant();
    next.set(observer.peerId, observer);
  }
  return next;
}

function parseOutcomeFields(raw: string): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  for (const line of raw.split(/\r?\n/)) {
    const match = /^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$/.exec(line.trim());
    if (!match) continue;
    const key = match[1] ?? '';
    const value = match[2]?.trim() ?? '';
    if (!key) continue;
    fields[key] = value;
  }
  return fields;
}

function normalizeOutcomeSummaryText(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  return value.replace(/([,;:!?])(?=[A-Za-z0-9])/g, '$1 ');
}

export function meshEventsFromTurns(turns: ConversationTurn[]): MeshEvent[] {
  const events: MeshEvent[] = [];
  for (const turn of turns) {
    if (turn.role !== 'assistant') continue;
    const participant = parseParticipantMeta(
      turn.participant_meta as Record<string, unknown> | undefined,
    );
    if (!participant?.peerId) continue;
    const outcome = extractOutcomeBlock(turn.content ?? '');
    if (!outcome) continue;
    const fields = parseOutcomeFields(outcome.raw);
    events.push({
      type: 'outcome',
      id: `history-${turn.id}`,
      timestamp: new Date(turn.created_at),
      participantId: participant.peerId,
      participant: { color: participant.color },
      persona: participant.persona,
      eventType:
        (typeof fields.event_type === 'string' && fields.event_type) ||
        (typeof fields.eventType === 'string' && fields.eventType) ||
        'outcome',
      verdict:
        typeof fields.verdict === 'string'
          ? (fields.verdict as MeshOutcomeEvent['verdict'])
          : undefined,
      summary: normalizeOutcomeSummaryText(fields.summary),
      fields,
      valid: true,
    });
  }
  return events;
}

export function stringifyOutcomeValue(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function pushOutcomeField(lines: string[], key: string, value: unknown): void {
  const text = stringifyOutcomeValue(value);
  if (!text) return;
  if (text.includes('\n')) {
    lines.push(`${key}: |`);
    for (const line of text.split('\n')) {
      lines.push(`  ${line}`);
    }
    return;
  }
  lines.push(`${key}: ${text}`);
}

export function formatOutcomeContent(event: CliStreamEvent): string {
  const fields =
    event.fields && typeof event.fields === 'object'
      ? { ...event.fields }
      : ({} as Record<string, unknown>);

  const lines: string[] = [];
  pushOutcomeField(lines, 'verdict', event.verdict ?? fields.verdict);
  pushOutcomeField(
    lines,
    'summary',
    normalizeOutcomeSummaryText(event.summary) ?? normalizeOutcomeSummaryText(fields.summary),
  );

  for (const [key, value] of Object.entries(fields)) {
    if (key === 'verdict' || key === 'summary' || key === 'success') continue;
    pushOutcomeField(lines, key, value);
  }

  if (lines.length === 0 && event.eventType) {
    pushOutcomeField(lines, 'event_type', event.eventType);
  }

  const raw = lines.join('\n');
  return `\`\`\`outcome\n${raw}\n\`\`\``;
}

async function attachmentToWireContent(
  attachment: FileAttachment,
): Promise<{ block: ContentBlock; meta: AttachmentMeta } | null> {
  const blob = attachment.compressed ?? attachment.file;
  if (!blob.type.startsWith('image/')) return null;

  const buffer = await blob.arrayBuffer();
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const base64 = btoa(binary);

  return {
    block: {
      type: 'image',
      source: {
        type: 'base64',
        media_type: blob.type as ContentBlock['source']['media_type'],
        data: base64,
      },
    },
    meta: {
      name: attachment.name,
      type: 'image',
      size: blob.size,
      contentType: blob.type,
    },
  };
}

export function parseEvent(raw: string): CliStreamEvent | null {
  let jsonStr = raw.trim();
  if (jsonStr.startsWith('data:')) jsonStr = jsonStr.slice(5).trim();
  try {
    return JSON.parse(jsonStr) as CliStreamEvent;
  } catch {
    return null;
  }
}

/** Keep the previously loaded prefix and local live tail around a contiguous recent page. */
function mergeRecentMessages(
  current: ChatMessage[],
  recent: ChatMessage[],
  preferLive = false,
): ChatMessage[] {
  const recentIds = new Set(recent.map((message) => message.id));
  const first = current.findIndex((message) => recentIds.has(message.id));
  let last = first;
  for (let index = first; index < current.length; index++) {
    if (current[index] && recentIds.has(current[index]!.id)) last = index;
  }
  const byId = new Map(current.map((message) => [message.id, message]));
  const incoming = recent.map((message) =>
    preferLive ? (byId.get(message.id) ?? message) : message,
  );
  if (first < 0) return [...incoming, ...current.filter((message) => !recentIds.has(message.id))];
  return [
    ...current.slice(0, first),
    ...incoming,
    ...current.slice(last + 1).filter((message) => !recentIds.has(message.id)),
  ];
}

interface UseSkuldChatOptions {
  historyMode?: 'session' | 'none';
}

export function useSkuldChat(
  url: string | null,
  options: UseSkuldChatOptions = {},
): UseSkuldChatResult {
  const { historyMode = 'session' } = options;
  const initialPersistedState = useMemo(() => revivePersistedState(url), [url]);
  const [messages, setMessages] = useState<ChatMessage[]>(() => initialPersistedState.messages);
  const [participants, setParticipants] = useState<Map<string, RoomParticipant>>(
    () => initialPersistedState.participants,
  );
  const [meshEvents, setMeshEvents] = useState<MeshEvent[]>(() => initialPersistedState.meshEvents);
  const [agentEvents, setAgentEvents] = useState<Map<string, AgentInternalEvent[]>>(
    () => initialPersistedState.agentEvents,
  );
  const [pendingPermissions, setPendingPermissions] = useState<PermissionRequest[]>([]);
  const [pendingInputRequests, setPendingInputRequests] = useState<InputRequest[]>([]);
  const [availableCommandsState, setAvailableCommandsState] = useState<{
    url: string | null;
    commands: SlashCommand[];
  }>({ url, commands: [] });
  const [capabilitiesState, setCapabilitiesState] = useState<{
    url: string | null;
    value: SessionCapabilities;
  }>({ url, value: {} });
  const capabilities = capabilitiesState.url === url ? capabilitiesState.value : {};
  const [connected, setConnected] = useState(false);
  const [connectionVersion, setConnectionVersion] = useState(0);
  const [historyLoadedForUrl, setHistoryLoadedForUrl] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState<string>('');
  const [streamingParts, setStreamingParts] = useState<ChatMessagePart[]>([]);
  const [streamingModel, setStreamingModel] = useState<string>('');

  const [historyRequestVersion, setHistoryRequestVersion] = useState(0);
  const [historyFailure, setHistoryFailure] = useState<{ url: string; message: string } | null>(
    null,
  );
  const historyAttempts = useRef({ url, count: 0 });
  const historyLoaded = historyLoadedForUrl === url;
  const [olderHistory, setOlderHistory] = useState<{
    url: string | null;
    page?: HistoryPage;
    loading: boolean;
    error: string | null;
  }>({ url, loading: false, error: null });
  const historyPageRef = useRef<{ url: string; page: HistoryPage } | null>(null);
  const historyReadRef = useRef<AbortController | null>(null);
  const historyWorkRef = useRef<Promise<void> | null>(null);
  const olderReadRef = useRef<AbortController | null>(null);
  const historyRefreshRef = useRef(false);
  const queuedRecoveryRef = useRef(false);
  const requestHistoryRecovery = useCallback(() => {
    if (historyMode !== 'session') return;
    if (historyReadRef.current) {
      queuedRecoveryRef.current = true;
      return;
    }
    olderReadRef.current?.abort();
    olderReadRef.current = null;
    historyRefreshRef.current = true;
    historyAttempts.current = { url, count: 0 };
    setHistoryRequestVersion((version) => version + 1);
  }, [historyMode, url]);
  const retryHistory = useCallback(() => {
    historyAttempts.current = { url, count: 0 };
    setHistoryFailure(null);
    historyRefreshRef.current = true;
    setHistoryRequestVersion((version) => version + 1);
  }, [url]);
  const participantsRef = useRef(participants);
  const messagesRef = useRef(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);
  const agentEventsRef = useRef(agentEvents);
  const hydratedCacheUrlRef = useRef(url);
  const internalStreamsRef = useRef<Map<string, InternalParticipantStream>>(new Map());
  const optimisticUserMessagesRef = useRef<Map<string, string>>(new Map());
  const toolJsonRef = useRef('');
  const toolIdRef = useRef('');
  const streamingMessageIdRef = useRef<string | null>(null);
  const streamingTextRef = useRef('');
  const streamingPartsRef = useRef<ChatMessagePart[]>([]);
  const streamingModelRef = useRef('');
  const activeNativeTurnRef = useRef<string | undefined>(undefined);
  const closedNativeTurnsRef = useRef(new Set<string>());
  const historyEvidenceRef = useRef(0);
  const snapshotEvidenceRef = useRef(0);
  const projectionRevisionRef = useRef<string | undefined>(undefined);
  const currentUrlRef = useRef(url);
  useEffect(() => {
    currentUrlRef.current = url;
    return () => {
      currentUrlRef.current = null;
    };
  }, [url]);
  const textRepairsRef = useRef(new Set<string>());
  const streamingInputTokensRef = useRef<number | undefined>(undefined);
  const streamingOutputTokensRef = useRef<number | undefined>(undefined);
  const historyRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const availableCommands = useMemo(
    () => (availableCommandsState.url === url ? availableCommandsState.commands : []),
    [availableCommandsState.commands, availableCommandsState.url, url],
  );
  const storeAvailableCommands = useCallback(
    (commands: SlashCommand[]) => {
      setAvailableCommandsState({ url, commands });
    },
    [url],
  );

  useEffect(() => {
    participantsRef.current = participants;
  }, [participants]);

  useEffect(() => {
    agentEventsRef.current = agentEvents;
  }, [agentEvents]);

  const ensureSingleParticipant = useCallback((): RoomParticipant => {
    const participant = makeSingleParticipant();
    setParticipants((prev) => {
      if (prev.size > 0 || prev.has(SINGLE_PARTICIPANT_ID)) return prev;
      return new Map(prev).set(participant.peerId, participant);
    });
    return participant;
  }, []);

  const getDefaultAssistantParticipant = useCallback((): RoomParticipant | undefined => {
    const existing = participantsRef.current.get(SINGLE_PARTICIPANT_ID);
    if (existing) return existing;
    if (participantsRef.current.size > 0) return undefined;
    return ensureSingleParticipant();
  }, [ensureSingleParticipant]);

  const resetStreaming = useCallback(() => {
    streamingMessageIdRef.current = null;
    streamingTextRef.current = '';
    streamingPartsRef.current = [];
    streamingModelRef.current = '';
    activeNativeTurnRef.current = undefined;
    setStreamingContent('');
    setStreamingParts([]);
    setStreamingModel('');
    toolJsonRef.current = '';
    toolIdRef.current = '';
    streamingInputTokensRef.current = undefined;
    streamingOutputTokensRef.current = undefined;
  }, []);

  const seedStreamingHistory = useCallback(
    (turns: ConversationTurn[], revision?: string) => {
      // Every accepted server snapshot replaces the cache projection, including repaired old turns.
      projectionRevisionRef.current = revision;
      // A truncated part sequence cannot seed a complete live turn. Keep its
      // explicit reader in history; subsequent socket items form the live tail.
      const active = [...turns].reverse().find((turn) => turn.in_progress && !turn.history_preview);
      for (const turn of turns) {
        if (turn.in_progress) continue;
        for (const part of turn.parts ?? []) {
          if (typeof part.turn_id === 'string') closedNativeTurnsRef.current.add(part.turn_id);
        }
      }
      while (closedNativeTurnsRef.current.size > 256) {
        closedNativeTurnsRef.current.delete(closedNativeTurnsRef.current.values().next().value!);
      }
      resetStreaming();
      if (!active) return;
      streamingMessageIdRef.current = active.id;
      streamingPartsRef.current = (active.parts ?? []) as unknown as ChatMessagePart[];
      streamingTextRef.current = active.content;
      activeNativeTurnRef.current = streamingPartsRef.current.find((part) => part.turn_id)?.turn_id;
      if (activeNativeTurnRef.current)
        closedNativeTurnsRef.current.delete(activeNativeTurnRef.current);
      setStreamingParts([...streamingPartsRef.current]);
      setStreamingContent(active.content);
    },
    [resetStreaming],
  );

  const clearHistoryRetryTimer = useCallback(() => {
    if (historyRetryTimerRef.current !== null) {
      clearTimeout(historyRetryTimerRef.current);
      historyRetryTimerRef.current = null;
    }
  }, []);

  const finalizeStreaming = useCallback(
    (status: ChatMessage['status'] = 'done', overrideContent?: string) => {
      const content = overrideContent || streamingTextRef.current;
      const parts =
        streamingPartsRef.current.length > 0 ? [...streamingPartsRef.current] : undefined;
      const model = streamingModelRef.current;
      const messageId = streamingMessageIdRef.current;
      if (!content && !parts?.length) {
        if (messageId) {
          setMessages((prev) => prev.filter((message) => message.id !== messageId));
        }
        resetStreaming();
        return;
      }
      const metadata = model
        ? {
            usage: {
              [model]: {
                inputTokens: streamingInputTokensRef.current,
                outputTokens: streamingOutputTokensRef.current,
              },
            },
          }
        : undefined;
      if (messageId) {
        setMessages((prev) =>
          prev.map((message) =>
            message.id === messageId
              ? {
                  ...message,
                  content,
                  status,
                  parts,
                  participant: message.participant ?? getDefaultAssistantParticipant(),
                  metadata,
                }
              : message,
          ),
        );
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: 'assistant',
            content,
            createdAt: new Date(),
            status,
            parts,
            participant: getDefaultAssistantParticipant(),
            metadata,
          },
        ]);
      }
      resetStreaming();
    },
    [getDefaultAssistantParticipant, resetStreaming],
  );

  useEffect(() => {
    if (!url) return;
    if (hydratedCacheUrlRef.current === url) return;
    hydratedCacheUrlRef.current = url;
    clearHistoryRetryTimer();
    const cached = safeSessionStorageGet(url);
    if (!cached) return;
    const cachedState = revivePersistedState(url);
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setMessages(cachedState.messages);
      setMeshEvents(cachedState.meshEvents);
      setParticipants(cachedState.participants);
      setAgentEvents(cachedState.agentEvents);
    });
    return () => {
      cancelled = true;
    };
  }, [clearHistoryRetryTimer, url]);

  useEffect(() => {
    if (!url || (historyLoaded && !historyRefreshRef.current)) return;
    if (historyMode === 'none') {
      queueMicrotask(() => {
        setHistoryLoadedForUrl(url);
      });
      return;
    }

    const httpBase = wsUrlToHttpBase(url);
    if (!httpBase) {
      queueMicrotask(() => {
        setHistoryLoadedForUrl(url);
      });
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    historyReadRef.current = controller;
    const previousPage =
      historyPageRef.current?.url === url ? historyPageRef.current.page : undefined;
    if (historyAttempts.current.url !== url) historyAttempts.current = { url, count: 0 };
    const evidenceAtStart = historyEvidenceRef.current;
    const snapshotAtStart = snapshotEvidenceRef.current;
    const idsAtStart = new Set(messagesRef.current.map((message) => message.id));

    historyWorkRef.current = fetchHistoryBatch(url, controller.signal)
      .then((data) => {
        if (cancelled) return;
        // Finish the post-attachment read before exposing the initial page.
        if (!historyLoaded && queuedRecoveryRef.current) return;
        clearHistoryRetryTimer();
        setHistoryFailure(null);
        historyRefreshRef.current = false;
        const sharesBoundary =
          previousPage &&
          previousPage.projection_revision === data.projection_revision &&
          messagesRef.current.some((turn) => data.turns.some((next) => next.id === turn.id));
        const boundary =
          sharesBoundary && previousPage.window_offset < data.window_offset ? previousPage : data;
        historyPageRef.current = { url, page: boundary };
        setOlderHistory({ url, page: boundary, loading: false, error: null });
        // A socket snapshot or live frame can win the initial GET. Never roll it back with this body.
        if (snapshotAtStart !== snapshotEvidenceRef.current) {
          setHistoryLoadedForUrl(url);
          return;
        }
        if (evidenceAtStart !== historyEvidenceRef.current) {
          // A live frame is only a suffix, not a replacement for its entire REST turn.
          const previous = messagesRef.current;
          const live = sharesBoundary
            ? previous
            : previous.filter(
                (message) => message.status === 'running' || !idsAtStart.has(message.id),
              );
          const { recent, current } = reconcileHistoryMessages(transformTurns(data.turns), live);
          const active = recent.find((message) => message.id === streamingMessageIdRef.current);
          if (active) {
            streamingPartsRef.current = [...(active.parts ?? [])];
            streamingTextRef.current = active.content;
            setStreamingParts([...streamingPartsRef.current]);
            setStreamingContent(active.content);
          }
          projectionRevisionRef.current = data.projection_revision;
          setMessages(mergeRecentMessages(current, recent));
          setHistoryLoadedForUrl(url);
          return;
        }
        seedStreamingHistory(data.turns ?? [], data.projection_revision);
        const nextMessages = data.turns?.length ? transformTurns(data.turns) : [];
        const historyParticipants = data.turns?.length ? participantsFromTurns(data.turns) : null;
        const historyMeshEvents = data.turns?.length ? meshEventsFromTurns(data.turns) : null;
        if (
          nextMessages.some((message) => message.role === 'assistant') &&
          !nextMessages.some((message) => message.participant) &&
          participantsRef.current.size === 0
        ) {
          const participant = ensureSingleParticipant();
          setMessages((prev) => {
            const incoming = nextMessages.map((message) =>
              message.role === 'assistant' ? { ...message, participant } : message,
            );
            return sharesBoundary ? mergeRecentMessages(prev, incoming) : incoming;
          });
        } else {
          setMessages((prev) => {
            if (!sharesBoundary) return nextMessages;
            return mergeRecentMessages(prev, nextMessages);
          });
        }
        if (historyParticipants && historyParticipants.size > 0) {
          setParticipants((prev) =>
            sharesBoundary ? new Map([...prev, ...historyParticipants]) : historyParticipants,
          );
        }
        if (historyMeshEvents && historyMeshEvents.length > 0) {
          setMeshEvents((prev) => {
            if (!sharesBoundary) return historyMeshEvents;
            const incoming = new Set(historyMeshEvents.map((event) => event.id));
            return [...prev.filter((event) => !incoming.has(event.id)), ...historyMeshEvents];
          });
        }
        setHistoryLoadedForUrl(url);
      })
      .catch((error: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setOlderHistory((previous) =>
          previous.url === url ? { ...previous, loading: false } : previous,
        );
        setHistoryFailure({
          url,
          message: error instanceof Error ? error.message : 'Could not reach the session history.',
        });
        clearHistoryRetryTimer();
        if (historyAttempts.current.count < MAX_HISTORY_RETRIES) {
          historyAttempts.current.count++;
          historyRetryTimerRef.current = setTimeout(() => {
            if (!cancelled) setHistoryRequestVersion((version) => version + 1);
          }, HISTORY_RETRY_DELAY_MS);
        }
      })
      .finally(() => {
        if (historyReadRef.current !== controller) return;
        historyReadRef.current = null;
        if (!cancelled && queuedRecoveryRef.current) {
          queuedRecoveryRef.current = false;
          historyRefreshRef.current = true;
          setHistoryRequestVersion((version) => version + 1);
        }
      });

    return () => {
      cancelled = true;
      controller.abort();
      if (historyReadRef.current === controller) historyReadRef.current = null;
      clearHistoryRetryTimer();
    };
  }, [
    clearHistoryRetryTimer,
    ensureSingleParticipant,
    historyLoaded,
    historyMode,
    historyRequestVersion,
    seedStreamingHistory,
    url,
  ]);

  const loadOlderHistory = useCallback(async () => {
    const held = historyPageRef.current;
    if (!url || held?.url !== url || held.page.window_offset <= 0 || olderReadRef.current) return;
    const controller = new AbortController();
    olderReadRef.current = controller;
    setOlderHistory({ url, page: held.page, loading: true, error: null });
    try {
      if (historyReadRef.current) await historyWorkRef.current;
      if (controller.signal.aborted || currentUrlRef.current !== url) return;
      const boundary = historyPageRef.current;
      if (boundary?.url !== url || boundary.page.window_offset <= 0) return;
      const page = await fetchHistoryBatch(url, controller.signal, boundary.page);
      if (controller.signal.aborted || currentUrlRef.current !== url) return;
      historyPageRef.current = { url, page };
      const older = transformTurns(page.turns);
      setMessages((prev) => {
        const ids = new Set(prev.map((message) => message.id));
        return [...older.filter((message) => !ids.has(message.id)), ...prev];
      });
      setParticipants((prev) => new Map([...participantsFromTurns(page.turns), ...prev]));
      setMeshEvents((prev) => {
        const ids = new Set(prev.map((event) => event.id));
        return [...meshEventsFromTurns(page.turns).filter((event) => !ids.has(event.id)), ...prev];
      });
      setOlderHistory({ url, page, loading: false, error: null });
    } catch (error) {
      if (controller.signal.aborted || currentUrlRef.current !== url) return;
      setOlderHistory({
        url,
        page: held.page,
        loading: false,
        error: error instanceof Error ? error.message : 'Could not load earlier messages.',
      });
      if (error instanceof HistoryChangedError) requestHistoryRecovery();
    } finally {
      if (olderReadRef.current === controller) olderReadRef.current = null;
    }
  }, [url, requestHistoryRecovery]);

  useEffect(
    () => () => {
      olderReadRef.current?.abort();
      olderReadRef.current = null;
      queuedRecoveryRef.current = false;
    },
    [url],
  );

  useEffect(() => {
    if (!url) return;
    safeSessionStorageSet(url, {
      projectionRevision: projectionRevisionRef.current,
      messages: serializeMessages(messages.slice(-HISTORY_PAGE_SIZE)),
      meshEvents: serializeMeshEvents(meshEvents),
      participants: Array.from(participants.values()),
      agentEvents: serializeAgentEvents(agentEvents),
    });
  }, [agentEvents, meshEvents, messages, participants, url]);

  const handleMessage = useCallback(
    (raw: string) => {
      const events = raw
        .split('\n')
        .filter((line) => line.trim())
        .map(parseEvent)
        .filter((event): event is CliStreamEvent => event !== null);

      const syncStreamingMessage = () => {
        const messageId = streamingMessageIdRef.current;
        if (!messageId) return;
        const nextContent = streamingTextRef.current;
        const nextParts =
          streamingPartsRef.current.length > 0 ? [...streamingPartsRef.current] : undefined;
        const participant = getDefaultAssistantParticipant();
        const metadata = streamingModelRef.current
          ? {
              usage: {
                [streamingModelRef.current]: {
                  inputTokens: streamingInputTokensRef.current,
                  outputTokens: streamingOutputTokensRef.current,
                },
              },
            }
          : undefined;
        setMessages((prev) =>
          prev.map((message) =>
            message.id === messageId
              ? {
                  ...message,
                  content: nextContent,
                  parts: nextParts,
                  participant: message.participant ?? participant,
                  metadata,
                }
              : message,
          ),
        );
      };

      const matchesText = (part: ChatMessagePart, event: CliStreamEvent) =>
        part.type === 'text' &&
        part.id === event.item_id &&
        (!event.turn_id || !part.turn_id || event.turn_id === part.turn_id) &&
        (!event.thread_id || !part.thread_id || event.thread_id === part.thread_id);

      const applyTextRepair = (event: CliStreamEvent, turn: ConversationTurn) => {
        if (!event.item_id) return;
        const canonical = (turn.parts ?? []) as unknown as ChatMessagePart[];
        const anchorKey = (part: ChatMessagePart) => {
          const id = part.type === 'tool_result' ? part.tool_use_id : part.id;
          return id ? `${part.type}:${id}` : undefined;
        };
        const anchors = new Map<string, ChatMessagePart[]>();
        for (const part of canonical) {
          const key = anchorKey(part);
          if (key) anchors.set(key, [...(anchors.get(key) ?? []), part]);
        }
        const score = (parts: readonly ChatMessagePart[]) =>
          parts.reduce((count, part) => {
            const key = anchorKey(part);
            return (
              count +
              (key &&
              anchors
                .get(key)
                ?.some(
                  (other) =>
                    (!part.turn_id || !other.turn_id || part.turn_id === other.turn_id) &&
                    (!part.thread_id || !other.thread_id || part.thread_id === other.thread_id),
                )
                ? 1
                : 0)
            );
          }, 0);
        // Bind a server turn through actual shared anchors, never native turn ID alone: steering
        // may have produced more than one public fragment for the same native turn.
        const candidates = messagesRef.current
          .filter((message) => message.role === 'assistant')
          .map((message) => ({
            id: message.id,
            score: message.id === turn.id ? Number.MAX_SAFE_INTEGER : score(message.parts ?? []),
          }))
          .filter((candidate) => candidate.score > 0)
          .sort((a, b) => b.score - a.score);
        const owner = candidates[0];
        if (!owner || candidates[1]?.score === owner.score) return;
        const target = {
          item_id: event.item_id,
          turn_id: event.turn_id,
          thread_id: event.thread_id,
        };
        if (owner.id === streamingMessageIdRef.current) {
          const parts = repairCanonicalText(canonical, streamingPartsRef.current, target);
          if (parts) {
            streamingPartsRef.current = parts;
            streamingTextRef.current = publicTextContent(parts);
            setStreamingParts([...parts]);
            setStreamingContent(streamingTextRef.current);
          }
        }
        setMessages((prev) =>
          prev.map((message) => {
            if (message.id !== owner.id) return message;
            const parts = repairCanonicalText(canonical, message.parts ?? [], target);
            return parts ? { ...message, parts, content: publicTextContent(parts) } : message;
          }),
        );
      };

      const repairTextFromHistory = async (event: CliStreamEvent) => {
        const key = JSON.stringify([event.thread_id, event.turn_id, event.item_id]);
        if (!url || textRepairsRef.current.has(key)) return;
        const base = wsUrlToHttpBase(url);
        if (!base) return;
        textRepairsRef.current.add(key);
        try {
          const history = await fetchHistoryBatch(url, new AbortController().signal);
          if (currentUrlRef.current !== url) return;
          for (const turn of history.turns ?? []) {
            const block = turn.parts?.find((part) =>
              matchesText(part as unknown as ChatMessagePart, event),
            );
            if (block?.complete === true && typeof block.text === 'string') {
              applyTextRepair(event, turn);
              return;
            }
          }
        } catch {
          /* Keep the observed incomplete text; the next snapshot can repair it. */
        } finally {
          textRepairsRef.current.delete(key);
        }
      };

      const finalizeParticipantStream = (
        peerId: string,
        status: ChatMessage['status'] = 'done',
      ) => {
        const stream = internalStreamsRef.current.get(peerId);
        if (!stream) return;
        const finalParts = [...stream.parts];
        const finalContent = finalParts
          .filter((part) => part.type === 'reasoning')
          .map((part) => part.text ?? '')
          .join('\n');
        setMessages((prev) =>
          prev.map((message) =>
            message.id === stream.messageId
              ? {
                  ...message,
                  content: finalContent,
                  parts: finalParts,
                  status,
                }
              : message,
          ),
        );
        internalStreamsRef.current.delete(peerId);
      };

      for (const event of events) {
        if (isHistoryRecovery(event)) {
          requestHistoryRecovery();
          continue;
        }
        // Native socket-only consumers still own their snapshots. Paged sessions use REST.
        if (event.type === 'conversation_history' && historyMode === 'session') continue;
        if (
          [
            'assistant',
            'content_block_start',
            'content_block_delta',
            'result',
            'user',
            'user_confirmed',
            'conversation_history',
          ].includes(event.type)
        ) {
          historyEvidenceRef.current += 1;
        }
        if (
          event.turn_id &&
          closedNativeTurnsRef.current.has(event.turn_id) &&
          event.type !== 'conversation_history' &&
          event.type !== 'user'
        )
          continue;
        if (
          event.turn_id &&
          activeNativeTurnRef.current &&
          event.turn_id !== activeNativeTurnRef.current &&
          ['assistant', 'content_block_start', 'content_block_delta'].includes(event.type)
        ) {
          closedNativeTurnsRef.current.add(activeNativeTurnRef.current);
          finalizeStreaming();
        }
        if (event.type === 'content_block_start' && !streamingMessageIdRef.current) {
          const id = generateId();
          streamingMessageIdRef.current = id;
          activeNativeTurnRef.current = event.turn_id;
          setMessages((prev) => [
            ...prev,
            {
              id,
              role: 'assistant',
              content: '',
              createdAt: new Date(),
              status: 'running',
              participant: getDefaultAssistantParticipant(),
            },
          ]);
        }
        if (
          event.type === 'content_block_stop' &&
          event.complete &&
          event.item_id &&
          (event.text_bytes !== undefined || event.text_sha256)
        ) {
          const part = streamingPartsRef.current.find((part) => matchesText(part, event));
          if (!part) {
            void repairTextFromHistory(event);
            continue;
          }
          // A whole authoritative completion already supplied these bytes. Only optimized
          // stops (whose item remains open) need a receipt check or expensive history repair.
          if (part.complete === true) continue;
          const captured = part.text ?? '';
          void validateTextReceipt(captured, event).then((valid) => {
            if (currentUrlRef.current !== url) return;
            if (!valid) {
              void repairTextFromHistory(event);
              return;
            }
            // Validation used immutable captured bytes. Do not overwrite intervening progress.
            const current = streamingPartsRef.current.find((part) => matchesText(part, event));
            if (current && current.text !== captured) return;
            setMessages((prev) =>
              prev.map((message) => {
                if (
                  !message.parts?.some((part) => matchesText(part, event) && part.text === captured)
                )
                  return message;
                return { ...message, parts: foldPublicText(message.parts, event)! };
              }),
            );
            if (current) {
              streamingPartsRef.current = foldPublicText(streamingPartsRef.current, event)!;
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
            }
          });
          continue;
        }
        const orderedText = foldPublicText(streamingPartsRef.current, event);
        if (orderedText) {
          if (event.turn_id && closedNativeTurnsRef.current.has(event.turn_id)) continue;
          if (
            event.turn_id &&
            activeNativeTurnRef.current &&
            event.turn_id !== activeNativeTurnRef.current
          ) {
            finalizeStreaming();
          }
          activeNativeTurnRef.current = event.turn_id ?? activeNativeTurnRef.current;
          if (!streamingMessageIdRef.current) {
            const messageId = generateId();
            streamingMessageIdRef.current = messageId;
            setMessages((prev) => [
              ...prev,
              {
                id: messageId,
                role: 'assistant',
                content: '',
                createdAt: new Date(),
                status: 'running',
                participant: getDefaultAssistantParticipant(),
              },
            ]);
          }
          streamingPartsRef.current = foldPublicText(streamingPartsRef.current, event)!;
          streamingTextRef.current = publicTextContent(streamingPartsRef.current);
          setStreamingContent(streamingTextRef.current);
          setStreamingParts([...streamingPartsRef.current]);
          syncStreamingMessage();
          continue;
        }
        switch (event.type) {
          case 'user': {
            // Claude and Codex emit tool results in a user envelope. They are
            // tool activity belonging to the assistant, never a new human prompt.
            const results =
              event.message?.content?.filter(
                (block) => block.type === 'tool_result' && block.tool_use_id,
              ) ?? [];
            for (const block of results) {
              const part: ChatMessagePart = { ...block, type: 'tool_result' };
              const matchesStream = streamingPartsRef.current.some(
                (existing) =>
                  existing.id === block.tool_use_id || existing.tool_use_id === block.tool_use_id,
              );
              if (matchesStream) {
                streamingPartsRef.current = upsertToolPart(streamingPartsRef.current, part);
                setStreamingParts([...streamingPartsRef.current]);
                syncStreamingMessage();
              } else {
                setMessages((current) => {
                  const index = current.findLastIndex(
                    (message) =>
                      message.role === 'assistant' &&
                      message.parts?.some(
                        (existing) =>
                          existing.id === block.tool_use_id ||
                          existing.tool_use_id === block.tool_use_id,
                      ),
                  );
                  if (index < 0) return current;
                  return current.map((message, i) =>
                    i === index
                      ? { ...message, parts: upsertToolPart(message.parts ?? [], part) }
                      : message,
                  );
                });
              }
            }
            break;
          }
          case 'assistant': {
            const explicitParticipant = parseParticipantMeta(event.participant);
            if (explicitParticipant) {
              setParticipants((prev) => {
                const next = new Map(prev);
                next.delete(SINGLE_PARTICIPANT_ID);
                next.set(explicitParticipant.peerId, explicitParticipant);
                return next;
              });
            } else {
              ensureSingleParticipant();
            }
            const blocks = event.message?.content;
            if (
              event.turn_id &&
              blocks?.length &&
              blocks.every((block) => block.type === 'tool_use')
            ) {
              if (!streamingMessageIdRef.current) {
                const id = generateId();
                streamingMessageIdRef.current = id;
                setMessages((prev) => [
                  ...prev,
                  {
                    id,
                    role: 'assistant',
                    content: '',
                    createdAt: new Date(),
                    status: 'running',
                    participant: getDefaultAssistantParticipant(),
                  },
                ]);
              }
              activeNativeTurnRef.current = event.turn_id;
              for (const block of blocks) {
                streamingPartsRef.current = upsertToolPart(streamingPartsRef.current, {
                  type: 'tool_use',
                  turn_id: event.turn_id,
                  thread_id: event.thread_id,
                  id: block.id,
                  name: block.name,
                  input: block.input ?? {},
                });
              }
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
              break;
            }
            if (
              streamingMessageIdRef.current &&
              blocks?.length &&
              blocks.every(
                (block) =>
                  block.type === 'tool_use' &&
                  block.id &&
                  streamingPartsRef.current.some(
                    (part) => part.type === 'tool_use' && part.id === block.id,
                  ),
              )
            ) {
              // Native tool completion can enrich the original call after its input
              // block closed. Keep that refresh in the same message and tool card.
              const updates = new Map(blocks.map((block) => [block.id, block]));
              streamingPartsRef.current = streamingPartsRef.current.map((part) => {
                const update = part.type === 'tool_use' ? updates.get(part.id) : undefined;
                return update && part.type === 'tool_use'
                  ? { ...part, name: update.name ?? part.name, input: update.input ?? part.input }
                  : part;
              });
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
              break;
            }
            if (streamingMessageIdRef.current) finalizeStreaming();
            const initialContent =
              event.message?.content
                ?.filter((entry) => entry.type === 'text' && entry.text)
                .map((entry) => entry.text)
                .join('') ?? '';
            const messageId = generateId();
            const participant = explicitParticipant ?? getDefaultAssistantParticipant();
            streamingMessageIdRef.current = messageId;
            streamingTextRef.current = initialContent;
            streamingPartsRef.current = initialContent
              ? [{ type: 'text', text: initialContent }]
              : [];
            streamingModelRef.current = event.message?.model ?? '';
            setStreamingContent(initialContent);
            setStreamingParts(streamingPartsRef.current);
            setStreamingModel(streamingModelRef.current);
            streamingInputTokensRef.current = event.message?.usage?.input_tokens;
            streamingOutputTokensRef.current = event.message?.usage?.output_tokens;
            setMessages((prev) => [
              ...prev,
              {
                id: messageId,
                role: 'assistant',
                content: initialContent,
                createdAt: new Date(),
                status: 'running',
                participant,
                parts:
                  streamingPartsRef.current.length > 0 ? [...streamingPartsRef.current] : undefined,
              },
            ]);
            break;
          }
          case 'content_block_start': {
            const blockType = event.content_block?.type;
            if (blockType === 'thinking') {
              const parts = streamingPartsRef.current;
              const last = parts[parts.length - 1];
              if (!last || last.type !== 'reasoning') {
                streamingPartsRef.current = [...parts, { type: 'reasoning', text: '' }];
                setStreamingParts([...streamingPartsRef.current]);
                syncStreamingMessage();
              }
            } else if (blockType === 'text') {
              const itemId = event.content_block?.id;
              const phase = event.content_block?.phase;
              streamingPartsRef.current = [
                ...streamingPartsRef.current,
                {
                  type: 'text',
                  text: '',
                  ...(itemId ? { id: itemId } : {}),
                  ...(phase ? { phase } : {}),
                },
              ];
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
            } else if (blockType === 'tool_use') {
              toolIdRef.current = event.content_block?.id ?? '';
              toolJsonRef.current = '';
              streamingPartsRef.current = upsertToolPart(streamingPartsRef.current, {
                type: 'tool_use',
                turn_id: event.turn_id,
                thread_id: event.thread_id,
                id: event.content_block?.id ?? '',
                name: event.content_block?.name ?? '',
                input: event.content_block?.input ?? {},
              });
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
            } else if (blockType === 'tool_result') {
              const toolUseId = event.content_block?.tool_use_id ?? '';
              if (toolUseId) {
                streamingPartsRef.current = upsertToolPart(streamingPartsRef.current, {
                  ...event.content_block,
                  type: 'tool_result',
                  tool_use_id: toolUseId,
                });
                setStreamingParts([...streamingPartsRef.current]);
                syncStreamingMessage();
              }
            }
            break;
          }
          case 'content_block_delta': {
            if (event.delta?.type === 'input_json_delta' && event.delta.partial_json) {
              toolJsonRef.current += event.delta.partial_json;
              break;
            }

            if (event.delta?.type === 'thinking_delta' && event.delta.thinking) {
              const next = [...streamingPartsRef.current];
              const last = next[next.length - 1];
              if (last?.type === 'reasoning') {
                next[next.length - 1] = {
                  ...last,
                  text: `${last.text ?? ''}${event.delta?.thinking ?? ''}`,
                };
              } else {
                next.push({ type: 'reasoning', text: event.delta?.thinking ?? '' });
              }
              streamingPartsRef.current = next;
              setStreamingParts(next);
              syncStreamingMessage();
              break;
            }

            const textChunk =
              event.delta?.type === 'text_delta' && event.delta.text
                ? event.delta.text
                : (event.delta?.text ?? null);
            if (!textChunk) break;

            const next = [...streamingPartsRef.current];
            const last = next[next.length - 1];
            const separator =
              last?.type === 'text' && last.id && !last.text && streamingTextRef.current
                ? '\n\n'
                : '';
            streamingTextRef.current += separator + textChunk;
            setStreamingContent(streamingTextRef.current);
            if (last?.type === 'text') {
              next[next.length - 1] = { ...last, text: `${last.text ?? ''}${textChunk}` };
            } else {
              next.push({ type: 'text', text: textChunk });
            }
            streamingPartsRef.current = next;
            setStreamingParts(next);
            syncStreamingMessage();
            break;
          }
          case 'content_block_stop': {
            if (!toolIdRef.current || !toolJsonRef.current) break;
            try {
              const input = JSON.parse(toolJsonRef.current) as Record<string, unknown>;
              streamingPartsRef.current = streamingPartsRef.current.map((part) =>
                part.type === 'tool_use' && part.id === toolIdRef.current
                  ? { ...part, input }
                  : part,
              );
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
            } catch {
              // Keep the empty tool input.
            }
            toolIdRef.current = '';
            toolJsonRef.current = '';
            break;
          }
          case 'message_delta': {
            streamingOutputTokensRef.current = event.usage?.output_tokens;
            break;
          }
          case 'result': {
            if (event.turn_id && closedNativeTurnsRef.current.has(event.turn_id)) break;
            if (activeNativeTurnRef.current) {
              closedNativeTurnsRef.current.add(activeNativeTurnRef.current);
              if (closedNativeTurnsRef.current.size > 256) {
                closedNativeTurnsRef.current.delete(
                  closedNativeTurnsRef.current.values().next().value!,
                );
              }
            }
            finalizeStreaming(event.is_error ? 'error' : 'done', event.result ?? undefined);
            break;
          }
          case 'error': {
            const errorMessage =
              typeof event.error === 'string'
                ? event.error
                : (event.error?.message ??
                  (typeof event.content === 'string' ? event.content : 'Unknown error'));
            if (event.code && event.request_id) {
              optimisticUserMessagesRef.current.delete(event.request_id);
              setMessages((previous) => [
                ...previous.map((message) =>
                  message.id === event.request_id
                    ? { ...message, status: 'error' as const }
                    : message,
                ),
                {
                  id: generateId(),
                  role: 'system',
                  content: errorMessage,
                  createdAt: new Date(),
                  status: 'error',
                  metadata: { messageType: 'error' },
                },
              ]);
              break;
            }
            finalizeStreaming('error', errorMessage);
            break;
          }
          case 'system': {
            const message =
              typeof event.content === 'string'
                ? event.content
                : event.subtype === 'init'
                  ? `Session initialized · ${event.message?.model ?? 'unknown'}`
                  : 'System event';
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: 'system',
                content: message,
                createdAt: new Date(),
                status: 'done',
                metadata: { messageType: 'system' },
              },
            ]);
            break;
          }
          case 'capabilities': {
            const caps = event as unknown as Record<string, unknown>;
            setCapabilitiesState({
              url,
              value: {
                interrupt: caps.interrupt === true,
                set_model: caps.set_model === true,
                set_thinking_tokens: caps.set_thinking_tokens === true,
                rewind_files: caps.rewind_files === true,
                slash_commands: caps.slash_commands === true,
                terminal_output: caps.terminal_output === true,
                terminal_input: caps.terminal_input === true,
                terminal_keys: caps.terminal_keys === true,
                terminal_resize: caps.terminal_resize === true,
                terminal_panes: caps.terminal_panes === true,
                room_prompt_resend: caps.room_prompt_resend === true,
              },
            });
            break;
          }
          case 'available_commands': {
            storeAvailableCommands(
              normalizeAvailableCommands(
                [...(event.commands ?? []), ...(event.slash_commands ?? [])],
                event.skills ?? [],
              ),
            );
            break;
          }
          case 'slash_commands': {
            storeAvailableCommands(normalizeAvailableCommands(event.commands ?? [], []));
            break;
          }
          case 'conversation_history': {
            snapshotEvidenceRef.current += 1;
            seedStreamingHistory(event.turns ?? [], event.projection_revision);
            const nextMessages = event.turns?.length ? transformTurns(event.turns) : [];
            const historyParticipants = event.turns?.length
              ? participantsFromTurns(event.turns)
              : null;
            const historyMeshEvents = event.turns?.length ? meshEventsFromTurns(event.turns) : null;
            if (
              nextMessages.some((message) => message.role === 'assistant') &&
              !nextMessages.some((message) => message.participant) &&
              participantsRef.current.size === 0
            ) {
              const participant = ensureSingleParticipant();
              setMessages(
                nextMessages.map((message) =>
                  message.role === 'assistant' ? { ...message, participant } : message,
                ),
              );
            } else {
              setMessages(nextMessages);
            }
            if (historyParticipants && historyParticipants.size > 0) {
              setParticipants(historyParticipants);
            }
            if (historyMeshEvents && historyMeshEvents.length > 0) {
              setMeshEvents(historyMeshEvents);
            }
            if (url) {
              setHistoryLoadedForUrl(url);
            }
            break;
          }
          case 'user_confirmed': {
            const messageId =
              typeof event.id === 'string' && event.id
                ? event.id
                : typeof event.request_id === 'string' && event.request_id
                  ? event.request_id
                  : generateId();
            const content = typeof event.content === 'string' ? event.content : '';
            if (!content) break;
            const requestId =
              typeof event.request_id === 'string' && event.request_id ? event.request_id : null;
            const optimisticMessageId = requestId
              ? optimisticUserMessagesRef.current.get(requestId)
              : undefined;
            if (requestId && optimisticMessageId) {
              optimisticUserMessagesRef.current.delete(requestId);
            }
            setMessages((prev) => {
              if (optimisticMessageId) {
                return prev.map((message) =>
                  message.id === optimisticMessageId
                    ? {
                        ...message,
                        id: messageId,
                        createdAt: event.created_at
                          ? new Date(event.created_at)
                          : message.createdAt,
                        status: 'done',
                        visibility: event.visibility ?? message.visibility,
                        metadata:
                          event.metadata && typeof event.metadata === 'object'
                            ? ({
                                ...(message.metadata ?? {}),
                                ...(event.metadata as ChatMessage['metadata']),
                              } as ChatMessage['metadata'])
                            : message.metadata,
                      }
                    : message,
                );
              }
              if (prev.some((message) => message.id === messageId)) {
                return prev;
              }
              return [
                ...prev,
                {
                  id: messageId,
                  role: 'user',
                  content,
                  createdAt: event.created_at ? new Date(event.created_at) : new Date(),
                  status: 'done',
                  metadata:
                    event.metadata && typeof event.metadata === 'object'
                      ? (event.metadata as ChatMessage['metadata'])
                      : undefined,
                  visibility: event.visibility,
                },
              ];
            });
            break;
          }
          case 'control_request': {
            if (!event.request_id) break;
            const input = event.input && typeof event.input === 'object' ? event.input : {};
            const command = typeof input.command === 'string' ? input.command.trim() : '';
            const filePath =
              typeof input.file_path === 'string'
                ? input.file_path
                : typeof input.path === 'string'
                  ? input.path
                  : '';
            const detail = command || filePath;
            setPendingPermissions((prev) => {
              const nextPermission = {
                requestId: event.request_id!,
                toolName: event.tool ?? 'unknown',
                description: detail || `Allow ${event.tool ?? 'tool'} to run`,
                input,
                command: command || undefined,
              };
              return [
                ...prev.filter((permission) => permission.requestId !== nextPermission.requestId),
                nextPermission,
              ];
            });
            break;
          }
          case 'permission_resolved': {
            if (!event.request_id) break;
            setPendingPermissions((prev) =>
              prev.filter((permission) => permission.requestId !== event.request_id),
            );
            break;
          }
          case 'ask_user_question': {
            const questions = Array.isArray(event.questions) ? event.questions : [];
            if (!event.request_id) break;
            const normalizedQuestions = questions.flatMap((question) => {
              if (!question || typeof question !== 'object') return [];
              const prompt = typeof question.question === 'string' ? question.question : '';
              if (!prompt) return [];
              const options = Array.isArray(question.options) ? question.options : [];
              return [
                {
                  prompt,
                  choices: options
                    .map((option) =>
                      option &&
                      typeof option === 'object' &&
                      'label' in option &&
                      typeof option.label === 'string'
                        ? option.label
                        : '',
                    )
                    .filter(Boolean),
                },
              ];
            });
            if (normalizedQuestions.length === 0) break;
            const inputRequest: InputRequest = {
              requestId: event.request_id,
              questions: normalizedQuestions,
            };
            setPendingInputRequests((prev) => [
              ...prev.filter((request) => request.requestId !== inputRequest.requestId),
              inputRequest,
            ]);
            break;
          }
          case 'ask_user_resolved': {
            if (!event.request_id) break;
            setPendingInputRequests((prev) =>
              prev.filter((request) => request.requestId !== event.request_id),
            );
            break;
          }
          case 'participant_joined': {
            const participant = parseParticipantMeta(event.participant);
            if (!participant?.peerId) break;
            setParticipants((prev) => new Map(prev).set(participant.peerId, participant));
            break;
          }
          case 'participant_left': {
            const peerId = String(event.participantId ?? event.participant_id ?? '');
            if (!peerId) break;
            setParticipants((prev) => {
              const next = new Map(prev);
              next.delete(peerId);
              return next;
            });
            break;
          }
          case 'room_state': {
            const next = new Map<string, RoomParticipant>();
            for (const participant of event.participants ?? []) {
              const parsed = parseParticipantMeta(participant);
              if (parsed?.peerId) next.set(parsed.peerId, parsed);
            }
            setParticipants(next);
            break;
          }
          case 'room_message': {
            const senderId = String(event.participantId ?? event.participant_id ?? '');
            if (senderId) {
              finalizeParticipantStream(senderId);
            }
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: event.role === 'user' ? 'user' : 'assistant',
                content: typeof event.content === 'string' ? event.content : '',
                createdAt: event.created_at ? new Date(event.created_at) : new Date(),
                status: 'done',
                participant: parseParticipantMeta(event.participant),
                threadId: event.thread_id,
                visibility: event.visibility,
              },
            ]);
            break;
          }
          case 'room_activity': {
            const peerId = String(event.participantId ?? event.participant_id ?? '');
            const status = event.activityType ?? event.status ?? 'idle';
            if (!peerId) break;
            setParticipants((prev) => {
              const existing = prev.get(peerId);
              if (!existing) return prev;
              return new Map(prev).set(peerId, {
                ...existing,
                status,
              });
            });
            if (status === 'idle') {
              finalizeParticipantStream(peerId);
            }
            break;
          }
          case 'room_outcome': {
            if (event.fields && event.fields.success === false) {
              break;
            }
            const participant = parseParticipantMeta(event.participant);
            const participantId = String(event.participantId ?? event.participant_id ?? '');
            setMeshEvents((prev) => [
              ...prev,
              {
                type: 'outcome',
                id: generateId(),
                timestamp: new Date(),
                participantId,
                participant: { color: participant?.color },
                persona: event.persona ?? participant?.persona ?? '',
                eventType: event.eventType ?? '',
                verdict: event.verdict as MeshOutcomeEvent['verdict'],
                summary:
                  normalizeOutcomeSummaryText(event.summary) ??
                  normalizeOutcomeSummaryText(event.fields?.summary),
                fields: event.fields,
                valid: event.valid === false ? false : true,
              },
            ]);
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: 'assistant',
                content: formatOutcomeContent(event),
                createdAt: new Date(),
                status: 'done',
                participant,
                participantId,
                visibility: 'internal',
              } as ChatMessage & { participantId?: string },
            ]);
            break;
          }
          case 'room_mesh_message': {
            const participant = parseParticipantMeta(event.participant);
            setMeshEvents((prev) => [
              ...prev,
              {
                type: 'mesh_message',
                id: generateId(),
                timestamp: new Date(),
                participantId: String(event.participantId ?? event.participant_id ?? ''),
                participant: { color: participant?.color },
                fromPersona: event.fromPersona ?? participant?.persona ?? '',
                eventType: event.eventType ?? '',
                preview: event.preview,
              },
            ]);
            break;
          }
          case 'room_notification': {
            const participant = parseParticipantMeta(event.participant);
            setMeshEvents((prev) => [
              ...prev,
              {
                type: 'notification',
                id: generateId(),
                timestamp: new Date(),
                participantId: String(event.participantId ?? event.participant_id ?? ''),
                participant: { color: participant?.color },
                persona: event.persona ?? participant?.persona ?? '',
                notificationType: event.notificationType ?? '',
                summary: event.summary ?? '',
                reason: event.reason,
                recommendation: event.recommendation,
                urgency: event.urgency ?? 0.5,
              },
            ]);
            break;
          }
          case 'room_agent_event': {
            const peerId = String(event.participantId ?? event.participant_id ?? '');
            const frame = event.frame;
            if (!peerId || !frame?.type) break;
            const nextEvent: AgentInternalEvent = {
              id: generateId(),
              participantId: peerId,
              timestamp: new Date(),
              frameType: frame.type,
              data: typeof frame.data === 'undefined' ? '' : frame.data,
              metadata: frame.metadata,
            };
            setAgentEvents((prev) => {
              const next = new Map(prev);
              next.set(peerId, [...(next.get(peerId) ?? []), nextEvent]);
              return next;
            });
            const participant = participantsRef.current.get(peerId);
            const existingStream = internalStreamsRef.current.get(peerId);
            const initialStream = existingStream ?? {
              messageId: generateId(),
              parts: [],
              currentToolId: '',
            };
            if (!existingStream) {
              const messageId = generateId();
              const createdStream = { ...initialStream, messageId };
              internalStreamsRef.current.set(peerId, createdStream);
              setMessages((prev) => [
                ...prev,
                {
                  id: messageId,
                  role: 'assistant',
                  content: '',
                  createdAt: new Date(),
                  status: 'running',
                  participant,
                  participantId: peerId,
                  visibility: 'internal',
                  threadId: event.thread_id,
                } as ChatMessage & { participantId?: string },
              ]);
            }
            const readStream = () => internalStreamsRef.current.get(peerId) ?? initialStream;
            const updateStream = (
              updater: (current: InternalParticipantStream) => InternalParticipantStream,
            ) => {
              const nextStream = updater(readStream());
              internalStreamsRef.current.set(peerId, nextStream);
              return nextStream;
            };
            const appendStreamingTextPart = (type: 'reasoning' | 'text', text: string) => {
              const currentStream = readStream();
              const lastPart = currentStream.parts.at(-1);
              if (lastPart?.type === type) {
                const updatedLastPart = {
                  ...lastPart,
                  text: `${lastPart.text ?? ''}${text}`,
                };
                updateStream((stream) => ({
                  ...stream,
                  parts: [...currentStream.parts.slice(0, -1), updatedLastPart],
                }));
                return;
              }
              updateStream((stream) => ({
                ...stream,
                parts: [...currentStream.parts, { type, text }],
              }));
            };
            if (frame.type === 'thought') {
              const text =
                typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data ?? '');
              appendStreamingTextPart('reasoning', text);
            } else if (frame.type === 'tool_start') {
              const currentStream = readStream();
              const toolName =
                (frame.metadata?.tool_name as string) ||
                (typeof frame.data === 'string' ? frame.data : '');
              const toolId = `tool-${generateId()}`;
              const input =
                typeof frame.metadata?.input === 'object' && frame.metadata.input !== null
                  ? (frame.metadata.input as Record<string, unknown>)
                  : {};
              updateStream((stream) => ({
                ...stream,
                currentToolId: toolId,
                parts: [
                  ...currentStream.parts,
                  { type: 'tool_use', id: toolId, name: toolName, input },
                ],
              }));
            } else if (frame.type === 'tool_result') {
              const currentStream = readStream();
              const result =
                typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data ?? '');
              const toolUseId = currentStream.currentToolId || `tool-${generateId()}`;
              updateStream((stream) => ({
                ...stream,
                currentToolId: '',
                parts: [
                  ...currentStream.parts,
                  { type: 'tool_result', tool_use_id: toolUseId, content: result },
                ],
              }));
            } else if (frame.type === 'text' || frame.type === 'message') {
              const text =
                typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data ?? '');
              appendStreamingTextPart('text', text);
            }
            const stream = readStream();
            const streamContent = stream.parts
              .filter((part) => part.type === 'reasoning' || part.type === 'text')
              .map((part) => part.text ?? '')
              .join('');
            const currentParts = [...stream.parts];
            setMessages((prev) =>
              prev.map((message) =>
                message.id === stream!.messageId
                  ? {
                      ...message,
                      content: streamContent,
                      parts: currentParts,
                    }
                  : message,
              ),
            );
            break;
          }
          default:
            break;
        }
      }
    },
    [
      ensureSingleParticipant,
      finalizeStreaming,
      getDefaultAssistantParticipant,
      storeAvailableCommands,
      seedStreamingHistory,
      requestHistoryRecovery,
      historyMode,
      url,
    ],
  );

  const socketUrl = useMemo(
    () => historySocketUrl(url, historyMode === 'session'),
    [url, historyMode],
  );
  const { sendJson } = useWebSocket(socketUrl, {
    onOpen: () => {
      // A read after socket attachment closes the GET-before-connect gap, including reconnects.
      // Cancel an unfinished initial batch now; do not download it all twice.
      if (!historyLoaded && historyReadRef.current) {
        historyReadRef.current.abort();
        historyReadRef.current = null;
        queuedRecoveryRef.current = false;
      }
      requestHistoryRecovery();
      setConnected(true);
      setConnectionVersion((version) => version + 1);
      storeAvailableCommands([]);
    },
    onMessage: handleMessage,
    onClose: () => {
      setConnected(false);
      finalizeStreaming();
    },
    onError: () => setConnected(false),
  });

  useEffect(() => {
    if (!connected || !capabilities.slash_commands) return;
    if (availableCommands.length > 0) return;
    // A refreshing discovery opens Claude's tmux autocomplete. Normal page loads
    // must use the advertised/cached catalogue without touching the CLI input.
    sendJson({ type: 'discover_slash_commands', refresh: false });
    const controller = new AbortController();
    let cancelled = false;

    const timer = setTimeout(() => {
      const httpBase = url ? wsUrlToHttpBase(url) : null;
      if (!httpBase) return;
      const requestUrl = new URL(
        'api/slash-commands',
        httpBase.endsWith('/') ? httpBase : `${httpBase}/`,
      );
      requestUrl.searchParams.set('refresh', 'false');

      void fetch(requestUrl.toString(), {
        headers: getAuthHeaders(),
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) return null;
          return (await response.json()) as SlashCommandsPayload;
        })
        .then((payload) => {
          if (!payload || cancelled) return;
          const commands = normalizeAvailableCommands(
            payload.commands ?? payload.slash_commands ?? [],
            payload.skills ?? [],
          );
          if (commands.length > 0) storeAvailableCommands(commands);
        })
        .catch(() => {
          // Slash command discovery is a progressive enhancement; keep chat usable if it fails.
        });
    }, 1500);

    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [
    availableCommands.length,
    capabilities.slash_commands,
    connected,
    connectionVersion,
    sendJson,
    storeAvailableCommands,
    url,
  ]);

  const sendMessage = useCallback(
    (text: string, attachments: FileAttachment[]) => {
      const trimmed = text.trim();
      if (!trimmed && attachments.length === 0) return;

      const slashCommand =
        attachments.length === 0 ? parseAdvertisedSlashCommand(trimmed, availableCommands) : null;
      if (slashCommand) {
        const requestId = generateId();
        optimisticUserMessagesRef.current.set(requestId, requestId);
        setMessages((prev) => [
          ...prev,
          {
            id: requestId,
            role: 'user',
            content: trimmed,
            createdAt: new Date(),
            status: 'done',
          },
        ]);
        sendJson({
          type: 'slash_command',
          command: slashCommand.command,
          arguments: slashCommand.arguments,
          request_id: requestId,
        });
        return;
      }

      Promise.all(attachments.map(attachmentToWireContent)).then((converted) => {
        const requestId = generateId();
        const valid = converted.filter(
          (value): value is NonNullable<typeof value> => value !== null,
        );
        optimisticUserMessagesRef.current.set(requestId, requestId);
        setMessages((prev) => [
          ...prev,
          {
            id: requestId,
            role: 'user',
            content: trimmed,
            createdAt: new Date(),
            status: 'done',
            attachments: valid.map((item) => item.meta),
          },
        ]);

        if (valid.length > 0) {
          const blocks: Array<{ type: 'text'; text: string } | ContentBlock> = [];
          if (trimmed) blocks.push({ type: 'text', text: trimmed });
          blocks.push(...valid.map((item) => item.block));
          sendJson({ type: 'user', content: blocks, request_id: requestId });
          return;
        }

        sendJson({ type: 'user', content: trimmed, request_id: requestId });
      });
    },
    [availableCommands, sendJson],
  );

  const sendDirectedMessages = useCallback(
    (targetParticipants: RoomParticipant[], text: string, attachments: FileAttachment[]) => {
      const trimmed = text.trim();
      if (!trimmed || targetParticipants.length === 0) return;
      const optimisticMessageId = generateId();
      const requestIds = targetParticipants.map(() => generateId());
      for (const requestId of requestIds) {
        optimisticUserMessagesRef.current.set(requestId, optimisticMessageId);
      }
      setMessages((prev) => [
        ...prev,
        {
          id: optimisticMessageId,
          role: 'user',
          content: trimmed,
          createdAt: new Date(),
          status: 'done',
        },
      ]);
      void attachments;
      for (let index = 0; index < targetParticipants.length; index += 1) {
        const participant = targetParticipants[index]!;
        sendJson({
          type: 'directed_message',
          targetPeerId: participant.peerId,
          content: trimmed,
          request_id: requestIds[index],
        });
      }
    },
    [sendJson],
  );

  const publishEvent = useCallback(
    (target: AgentEventTarget, text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !target.eventType.trim()) return;
      const requestId = generateId();
      optimisticUserMessagesRef.current.set(requestId, requestId);
      setMessages((prev) => [
        ...prev,
        {
          id: requestId,
          role: 'user',
          content: trimmed,
          createdAt: new Date(),
          status: 'done',
        },
      ]);
      sendJson({
        type: 'publish_event',
        eventType: target.eventType,
        content: trimmed,
        request_id: requestId,
      });
    },
    [sendJson],
  );

  const sendResendPrompt = useCallback(() => {
    sendJson({ type: 'resend_initial_prompt' });
  }, [sendJson]);

  const respondToPermission = useCallback(
    (requestId: string, behavior: PermissionBehavior) => {
      const normalized =
        behavior === 'allow_always' ? 'allowForever' : behavior === 'allow_once' ? 'allow' : 'deny';
      sendJson({
        type: 'permission_response',
        request_id: requestId,
        behavior: normalized,
        updated_input: {},
        updated_permissions: [],
      });
      setPendingPermissions((prev) =>
        prev.filter((permission) => permission.requestId !== requestId),
      );
    },
    [sendJson],
  );

  const respondToInput = useCallback(
    (requestId: string, values: string[]) => {
      sendJson({
        type: 'ask_user_answer',
        request_id: requestId,
        answers: values.map((answer) => ({ answer })),
      });
      setPendingInputRequests((prev) => prev.filter((request) => request.requestId !== requestId));
    },
    [sendJson],
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    setMeshEvents([]);
    setAgentEvents(new Map());
    setPendingPermissions([]);
    setPendingInputRequests([]);
    internalStreamsRef.current.clear();
    resetStreaming();
  }, [resetStreaming]);

  const stableParticipants = useMemo(
    () => participants as ReadonlyMap<string, RoomParticipant>,
    [participants],
  );
  const stableAgentEvents = useMemo(
    () => agentEvents as ReadonlyMap<string, readonly AgentInternalEvent[]>,
    [agentEvents],
  );

  const sendSetInternalVisibility = useCallback(
    (visible: boolean) => sendJson({ type: 'set_internal_visibility', visible }),
    [sendJson],
  );

  return {
    messages,
    streamingContent: streamingContent || undefined,
    streamingParts: streamingParts.length > 0 ? streamingParts : undefined,
    streamingModel: streamingModel || undefined,
    connected,
    historyLoaded,
    historyError: historyFailure?.url === url ? historyFailure.message : null,
    retryHistory,
    hasOlderHistory: olderHistory.url === url && (olderHistory.page?.window_offset ?? 0) > 0,
    loadingOlderHistory: olderHistory.url === url && olderHistory.loading,
    olderHistoryError: olderHistory.url === url ? olderHistory.error : null,
    loadOlderHistory,
    participants: stableParticipants,
    meshEvents,
    agentEvents: stableAgentEvents,
    pendingPermissions,
    pendingInputRequests,
    availableCommands,
    capabilities,
    sendMessage,
    sendDirectedMessages,
    publishEvent,
    sendResendPrompt,
    respondToPermission,
    respondToInput,
    sendInterrupt: () => sendJson({ type: 'interrupt' }),
    sendSetModel: (model: string) => sendJson({ type: 'set_model', model }),
    sendSetThinkingTokens: (tokens: number) =>
      sendJson({ type: 'set_max_thinking_tokens', max_thinking_tokens: tokens }),
    sendRewindFiles: () => sendJson({ type: 'rewind_files' }),
    sendSetInternalVisibility,
    clearMessages,
  };
}
