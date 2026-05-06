import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { SkuldChatMessage, MeshEvent } from '@/modules/shared/hooks/useSkuldChat';

/**
 * Serialisable shape stored in sessionStorage.
 * Dates are stored as ISO strings and rehydrated on read.
 */
interface SerializedMessage {
  readonly id: string;
  readonly role: 'user' | 'assistant' | 'system';
  readonly content: string;
  readonly createdAt: string;
  readonly status: 'running' | 'complete' | 'error';
  readonly metadata?: SkuldChatMessage['metadata'];
  readonly participantId?: string;
  readonly participant?: SkuldChatMessage['participant'];
  readonly threadId?: string;
  readonly visibility?: string;
  readonly parts?: SkuldChatMessage['parts'];
}

/** Serialised mesh event — timestamp stored as ISO string. */
interface SerializedMeshEvent {
  readonly [key: string]: unknown;
  readonly timestamp: string;
}

interface ChatStoreState {
  /** Messages keyed by WebSocket URL */
  sessions: Record<string, SerializedMessage[]>;
  /** Mesh events keyed by WebSocket URL */
  meshEventSessions: Record<string, SerializedMeshEvent[]>;
}

interface ChatStoreActions {
  /** Persist current messages for a session URL */
  setMessages: (url: string, messages: readonly SkuldChatMessage[]) => void;
  /** Retrieve persisted messages for a session URL */
  getMessages: (url: string) => SkuldChatMessage[];
  /** Persist mesh events for a session URL */
  setMeshEvents: (url: string, events: readonly MeshEvent[]) => void;
  /** Retrieve persisted mesh events for a session URL */
  getMeshEvents: (url: string) => MeshEvent[];
  /** Clear persisted messages and mesh events for a session URL */
  clearSession: (url: string) => void;
}

function serialize(msgs: readonly SkuldChatMessage[]): SerializedMessage[] {
  return msgs.map(m => ({
    ...m,
    createdAt: m.createdAt.toISOString(),
  }));
}

function deserialize(msgs: SerializedMessage[]): SkuldChatMessage[] {
  return msgs.map(m => ({
    ...m,
    createdAt: new Date(m.createdAt),
  }));
}

function serializeMeshEvents(events: readonly MeshEvent[]): SerializedMeshEvent[] {
  return events.map(e => ({
    ...e,
    timestamp: e.timestamp.toISOString(),
  })) as unknown as SerializedMeshEvent[];
}

function deserializeMeshEvents(events: SerializedMeshEvent[]): MeshEvent[] {
  return events.map(e => ({
    ...e,
    timestamp: new Date(e.timestamp as string),
  })) as unknown as MeshEvent[];
}

export const useChatStore = create<ChatStoreState & ChatStoreActions>()(
  persist(
    (set, get) => ({
      sessions: {},
      meshEventSessions: {},

      setMessages: (url, messages) => {
        set(state => ({
          sessions: {
            ...state.sessions,
            [url]: serialize(messages),
          },
        }));
      },

      getMessages: url => {
        const stored = get().sessions[url];
        if (!stored || stored.length === 0) {
          return [];
        }
        return deserialize(stored);
      },

      setMeshEvents: (url, events) => {
        set(state => ({
          meshEventSessions: {
            ...state.meshEventSessions,
            [url]: serializeMeshEvents(events),
          },
        }));
      },

      getMeshEvents: url => {
        const stored = get().meshEventSessions[url];
        if (!stored || stored.length === 0) {
          return [];
        }
        return deserializeMeshEvents(stored);
      },

      clearSession: url => {
        set(state => {
          const { [url]: _removed, ...rest } = state.sessions;
          const { [url]: _removedMesh, ...meshRest } = state.meshEventSessions;
          return { sessions: rest, meshEventSessions: meshRest };
        });
      },
    }),
    {
      name: 'volundr-chat',
      storage: createJSONStorage(() => sessionStorage),
    }
  )
);
