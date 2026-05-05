import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAccessToken } from '@niuulabs/query';
import type {
  AgentInternalEvent,
  AttachmentMeta,
  ChatMessage,
  ChatMessagePart,
  ContentBlock,
  MeshEvent,
  MeshOutcomeEvent,
  PermissionBehavior,
  PermissionRequest,
  RoomParticipant,
  SessionCapabilities,
} from '@niuulabs/ui';
import type { FileAttachment } from '@niuulabs/ui';
import { useWebSocket } from './useWebSocket';
import { wsUrlToHttpBase } from '../liveSessionTransport';

const HISTORY_RETRY_DELAY_MS = 1000;

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
  content?: string | Array<{ type: string; text?: string }>;
  result?: string;
  error?: string | { message?: string };
  message?: {
    model?: string;
    usage?: { input_tokens?: number; output_tokens?: number };
    content?: Array<{ type: string; text?: string }>;
  };
  content_block?: { type?: string; text?: string; id?: string; name?: string };
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
  turns?: ConversationTurn[];
  fields?: Record<string, unknown>;
};

interface ConversationTurn {
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
}

interface UseSkuldChatResult {
  messages: ChatMessage[];
  streamingContent?: string;
  streamingParts?: ChatMessagePart[];
  streamingModel?: string;
  connected: boolean;
  historyLoaded: boolean;
  participants: ReadonlyMap<string, RoomParticipant>;
  meshEvents: MeshEvent[];
  agentEvents: ReadonlyMap<string, readonly AgentInternalEvent[]>;
  pendingPermissions: PermissionRequest[];
  capabilities: SessionCapabilities;
  sendMessage: (text: string, attachments: FileAttachment[]) => void;
  sendDirectedMessages: (
    participants: RoomParticipant[],
    text: string,
    attachments: FileAttachment[],
  ) => void;
  respondToPermission: (requestId: string, behavior: PermissionBehavior) => void;
  sendInterrupt: () => void;
  sendSetModel: (model: string) => void;
  sendSetThinkingTokens: (tokens: number) => void;
  sendRewindFiles: () => void;
  clearMessages: () => void;
}

const SINGLE_PARTICIPANT_ID = 'skuld-primary';
const STORAGE_PREFIX = 'niuu.skuldChat.';

type PersistedAgentEvent = Omit<AgentInternalEvent, 'timestamp'> & { timestamp?: string };
type PersistedChatState = {
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
  return turns.map((turn) => ({
    id: turn.id,
    role: turn.role === 'user' ? 'user' : 'assistant',
    content: turn.content,
    createdAt: new Date(turn.created_at),
    status: 'done',
    parts: turn.parts as ChatMessagePart[] | undefined,
    metadata: turn.metadata as ChatMessage['metadata'] | undefined,
    participant: parseParticipantMeta(turn.participant_meta as Record<string, unknown> | undefined),
    threadId: turn.thread_id,
    visibility: turn.visibility,
  }));
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
  pushOutcomeField(lines, 'summary', event.summary ?? fields.summary);

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

export function useSkuldChat(url: string | null): UseSkuldChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [participants, setParticipants] = useState<Map<string, RoomParticipant>>(new Map());
  const [meshEvents, setMeshEvents] = useState<MeshEvent[]>([]);
  const [agentEvents, setAgentEvents] = useState<Map<string, AgentInternalEvent[]>>(new Map());
  const [pendingPermissions, setPendingPermissions] = useState<PermissionRequest[]>([]);
  const [capabilities, setCapabilities] = useState<SessionCapabilities>({});
  const [connected, setConnected] = useState(false);
  const [historyLoadedForUrl, setHistoryLoadedForUrl] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState<string>('');
  const [streamingParts, setStreamingParts] = useState<ChatMessagePart[]>([]);
  const [streamingModel, setStreamingModel] = useState<string>('');

  const historyLoaded = historyLoadedForUrl === url;
  const participantsRef = useRef(participants);
  const agentEventsRef = useRef(agentEvents);
  const internalStreamsRef = useRef<Map<string, InternalParticipantStream>>(new Map());
  const toolJsonRef = useRef('');
  const toolIdRef = useRef('');
  const streamingMessageIdRef = useRef<string | null>(null);
  const streamingTextRef = useRef('');
  const streamingPartsRef = useRef<ChatMessagePart[]>([]);
  const streamingModelRef = useRef('');
  const streamingInputTokensRef = useRef<number | undefined>(undefined);
  const streamingOutputTokensRef = useRef<number | undefined>(undefined);
  const historyRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    setStreamingContent('');
    setStreamingParts([]);
    setStreamingModel('');
    toolJsonRef.current = '';
    toolIdRef.current = '';
    streamingInputTokensRef.current = undefined;
    streamingOutputTokensRef.current = undefined;
  }, []);

  const clearHistoryRetryTimer = useCallback(() => {
    if (historyRetryTimerRef.current !== null) {
      clearTimeout(historyRetryTimerRef.current);
      historyRetryTimerRef.current = null;
    }
  }, []);

  const finalizeStreaming = useCallback(
    (status: ChatMessage['status'] = 'done', overrideContent?: string) => {
      const content = overrideContent ?? streamingTextRef.current;
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
    clearHistoryRetryTimer();
    const cached = safeSessionStorageGet(url);
    if (!cached) return;
    if ((cached.messages?.length ?? 0) > 0) {
      setMessages(reviveMessages(cached.messages));
    }
    if ((cached.meshEvents?.length ?? 0) > 0) {
      setMeshEvents(reviveMeshEvents(cached.meshEvents));
    }
    if ((cached.participants?.length ?? 0) > 0) {
      setParticipants(
        new Map(cached.participants?.map((participant) => [participant.peerId, participant])),
      );
    }
    if (cached.agentEvents) {
      setAgentEvents(reviveAgentEvents(cached.agentEvents));
    }
  }, [clearHistoryRetryTimer, url]);

  useEffect(() => {
    if (!url || historyLoaded) return;

    const httpBase = wsUrlToHttpBase(url);
    if (!httpBase) {
      setHistoryLoadedForUrl(url);
      return;
    }

    let cancelled = false;
    const headers: Record<string, string> = {};
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const base = httpBase.endsWith('/') ? httpBase : `${httpBase}/`;
    const historyUrl = new URL('api/conversation/history', base);

    fetch(historyUrl.href, { headers })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`history_fetch_failed:${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        clearHistoryRetryTimer();
        const nextMessages = data.turns?.length ? transformTurns(data.turns) : [];
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
        setHistoryLoadedForUrl(url);
      })
      .catch(() => {
        if (cancelled) return;
        clearHistoryRetryTimer();
        historyRetryTimerRef.current = setTimeout(() => {
          if (!cancelled) {
            setHistoryLoadedForUrl((current) => (current === url ? null : current));
          }
        }, HISTORY_RETRY_DELAY_MS);
      });

    return () => {
      cancelled = true;
      clearHistoryRetryTimer();
    };
  }, [clearHistoryRetryTimer, ensureSingleParticipant, historyLoaded, url]);

  useEffect(() => {
    if (!url) return;
    safeSessionStorageSet(url, {
      messages: serializeMessages(messages),
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
        switch (event.type) {
          case 'assistant': {
            ensureSingleParticipant();
            if (streamingMessageIdRef.current) finalizeStreaming();
            const initialContent =
              event.message?.content
                ?.filter((entry) => entry.type === 'text' && entry.text)
                .map((entry) => entry.text)
                .join('') ?? '';
            const messageId = generateId();
            const participant = getDefaultAssistantParticipant();
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
              streamingPartsRef.current = [
                ...streamingPartsRef.current,
                { type: 'text', text: '' },
              ];
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
            } else if (blockType === 'tool_use') {
              toolIdRef.current = event.content_block?.id ?? '';
              toolJsonRef.current = '';
              streamingPartsRef.current = [
                ...streamingPartsRef.current,
                {
                  type: 'tool_use',
                  id: event.content_block?.id ?? '',
                  name: event.content_block?.name ?? '',
                  input: {},
                },
              ];
              setStreamingParts([...streamingPartsRef.current]);
              syncStreamingMessage();
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

            streamingTextRef.current = `${streamingTextRef.current}${textChunk}`;
            setStreamingContent(streamingTextRef.current);
            const next = [...streamingPartsRef.current];
            const last = next[next.length - 1];
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
            finalizeStreaming(event.is_error ? 'error' : 'done', event.result ?? undefined);
            break;
          }
          case 'error': {
            const errorMessage =
              typeof event.error === 'string'
                ? event.error
                : (event.error?.message ?? 'Unknown error');
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
            setCapabilities({
              interrupt: caps.interrupt === true,
              set_model: caps.set_model === true,
              set_thinking_tokens: caps.set_thinking_tokens === true,
              rewind_files: caps.rewind_files === true,
            });
            break;
          }
          case 'conversation_history': {
            const nextMessages = event.turns?.length ? transformTurns(event.turns) : [];
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
            setMessages((prev) => {
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
            setPendingPermissions((prev) => [
              ...prev,
              {
                requestId: event.request_id!,
                toolName: event.tool ?? 'unknown',
                description: `Allow ${event.tool ?? 'tool'} to run`,
              },
            ]);
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
                summary: event.summary,
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
            let stream = internalStreamsRef.current.get(peerId);
            if (!stream) {
              const messageId = generateId();
              stream = { messageId, parts: [], currentToolId: '' };
              internalStreamsRef.current.set(peerId, stream);
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
            const appendStreamingTextPart = (type: 'reasoning' | 'text', text: string) => {
              const lastPart = stream.parts.at(-1);
              if (lastPart?.type === type) {
                const updatedLastPart = {
                  ...lastPart,
                  text: `${lastPart.text ?? ''}${text}`,
                };
                stream.parts = [...stream.parts.slice(0, -1), updatedLastPart];
                return;
              }
              stream.parts = [...stream.parts, { type, text }];
            };
            if (frame.type === 'thought') {
              const text =
                typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data ?? '');
              appendStreamingTextPart('reasoning', text);
            } else if (frame.type === 'tool_start') {
              const toolName =
                (frame.metadata?.tool_name as string) ||
                (typeof frame.data === 'string' ? frame.data : '');
              const toolId = `tool-${generateId()}`;
              stream.currentToolId = toolId;
              const input =
                typeof frame.metadata?.input === 'object' && frame.metadata.input !== null
                  ? (frame.metadata.input as Record<string, unknown>)
                  : {};
              stream.parts = [
                ...stream.parts,
                { type: 'tool_use', id: toolId, name: toolName, input },
              ];
            } else if (frame.type === 'tool_result') {
              const result =
                typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data ?? '');
              const toolUseId = stream.currentToolId || `tool-${generateId()}`;
              stream.parts = [
                ...stream.parts,
                { type: 'tool_result', tool_use_id: toolUseId, content: result },
              ];
              stream.currentToolId = '';
            } else if (frame.type === 'text' || frame.type === 'message') {
              const text =
                typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data ?? '');
              appendStreamingTextPart('text', text);
            }
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
    [ensureSingleParticipant, finalizeStreaming, getDefaultAssistantParticipant, url],
  );

  const { sendJson } = useWebSocket(url, {
    onOpen: () => setConnected(true),
    onMessage: handleMessage,
    onClose: () => {
      setConnected(false);
      finalizeStreaming();
    },
    onError: () => setConnected(false),
  });

  const sendMessage = useCallback(
    (text: string, attachments: FileAttachment[]) => {
      const trimmed = text.trim();
      if (!trimmed && attachments.length === 0) return;

      Promise.all(attachments.map(attachmentToWireContent)).then((converted) => {
        const valid = converted.filter(
          (value): value is NonNullable<typeof value> => value !== null,
        );
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
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
          sendJson({ type: 'user', content: blocks });
          return;
        }

        sendJson({ type: 'user', content: trimmed });
      });
    },
    [sendJson],
  );

  const sendDirectedMessages = useCallback(
    (targetParticipants: RoomParticipant[], text: string, attachments: FileAttachment[]) => {
      const trimmed = text.trim();
      if (!trimmed || targetParticipants.length === 0) return;
      setMessages((prev) => [
        ...prev,
        {
          id: generateId(),
          role: 'user',
          content: trimmed,
          createdAt: new Date(),
          status: 'done',
        },
      ]);
      void attachments;
      for (const participant of targetParticipants) {
        sendJson({ type: 'directed_message', targetPeerId: participant.peerId, content: trimmed });
      }
    },
    [sendJson],
  );

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

  const clearMessages = useCallback(() => {
    setMessages([]);
    setMeshEvents([]);
    setAgentEvents(new Map());
    setPendingPermissions([]);
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

  return {
    messages,
    streamingContent: streamingContent || undefined,
    streamingParts: streamingParts.length > 0 ? streamingParts : undefined,
    streamingModel: streamingModel || undefined,
    connected,
    historyLoaded,
    participants: stableParticipants,
    meshEvents,
    agentEvents: stableAgentEvents,
    pendingPermissions,
    capabilities,
    sendMessage,
    sendDirectedMessages,
    respondToPermission,
    sendInterrupt: () => sendJson({ type: 'interrupt' }),
    sendSetModel: (model: string) => sendJson({ type: 'set_model', model }),
    sendSetThinkingTokens: (tokens: number) =>
      sendJson({ type: 'set_max_thinking_tokens', max_thinking_tokens: tokens }),
    sendRewindFiles: () => sendJson({ type: 'rewind_files' }),
    clearMessages,
  };
}
