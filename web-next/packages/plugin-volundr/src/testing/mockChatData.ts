import type { ParticipantMeta, MeshEvent } from '@niuulabs/ui';
import { resolveParticipantColor } from '@niuulabs/ui';
import type { Session } from '../domain/session';

// ---------------------------------------------------------------------------
// Chat turn types
// ---------------------------------------------------------------------------

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant' | 'thinking' | 'tool';
  peerId: string;
  content: string;
  tool?: string;
  args?: string;
  output?: string;
  status?: 'ok' | 'err' | 'run';
  dur?: string;
  tokens?: number;
  ms?: number;
  ts: number;
  outcome?: OutcomeData;
  directedTo?: string[];
}

export interface OutcomeData {
  verdict: string;
  eventType: string;
  summary: string;
  fields?: Record<string, unknown>;
  findings?: Array<{ severity: string; loc: string; msg: string }>;
}

// ---------------------------------------------------------------------------
// Mock room types
// ---------------------------------------------------------------------------

export interface PeerMeta extends ParticipantMeta {
  glyph: string;
  expanded?: boolean;
  gateway?: string;
}

export interface MockRoom {
  roomId: string;
  participants: PeerMeta[];
  byId: Record<string, PeerMeta>;
  meshEvents: MeshEvent[];
}

export type TurnGroup =
  | { kind: 'single'; turn: ChatTurn }
  | { kind: 'thinking'; turn: ChatTurn }
  | { kind: 'toolrun'; turns: ChatTurn[] };

// ---------------------------------------------------------------------------
// Mock data builders
// ---------------------------------------------------------------------------

export function buildMockRoom(session: Session): MockRoom {
  const now = Date.now();
  const participants: PeerMeta[] = [
    {
      peerId: 'human',
      persona: 'human',
      displayName: 'You',
      glyph: 'H',
      status: 'idle',
      participantType: 'human',
      color: resolveParticipantColor('human'),
      subscribesTo: [],
      emits: ['user.message'],
      tools: [],
    },
    {
      peerId: `ravn-${session.ravnId}`,
      persona: session.personaName,
      displayName: session.personaName,
      glyph: session.personaName[0]?.toUpperCase() ?? 'R',
      status: session.state === 'running' ? 'busy' : 'idle',
      participantType: 'ravn',
      color: resolveParticipantColor(`ravn-${session.ravnId}`),
      subscribesTo: ['user.message', 'code.changed', 'test.result'],
      emits: ['code.changed', 'outcome.review'],
      tools: ['read_file', 'write_file', 'run_command', 'search_files'],
      gateway: 'bifrost://anthropic/claude-sonnet',
      gatewayLatencyMs: 84,
      gatewayRegion: 'us-east-1',
      expanded: true,
    },
    {
      peerId: 'reviewer-1',
      persona: 'reviewer',
      displayName: 'Reviewer',
      glyph: 'V',
      status: 'idle',
      participantType: 'ravn',
      color: resolveParticipantColor('reviewer-1'),
      subscribesTo: ['code.changed', 'outcome.review'],
      emits: ['outcome.review'],
      tools: ['read_file', 'search_files'],
      gateway: 'bifrost://anthropic/claude-haiku',
      gatewayLatencyMs: 312,
      gatewayRegion: 'eu-west-1',
    },
  ];

  const byId: Record<string, PeerMeta> = {};
  for (const p of participants) {
    byId[p.peerId] = p;
  }

  const meshEvents: MeshEvent[] = [
    {
      id: 'me-1',
      type: 'outcome',
      timestamp: new Date(now - 300_000),
      participantId: 'reviewer-1',
      participant: { color: resolveParticipantColor('reviewer-1') },
      persona: 'reviewer',
      eventType: 'code.review',
      verdict: 'pass',
      summary: 'Code review passed - clean implementation',
    },
    {
      id: 'me-2',
      type: 'mesh_message',
      timestamp: new Date(now - 200_000),
      participantId: `ravn-${session.ravnId}`,
      participant: { color: resolveParticipantColor(`ravn-${session.ravnId}`) },
      fromPersona: session.personaName,
      eventType: 'code.changed',
      preview: 'Updated auth handler with new validation logic',
    },
    {
      id: 'me-3',
      type: 'notification',
      timestamp: new Date(now - 100_000),
      participantId: `ravn-${session.ravnId}`,
      participant: { color: resolveParticipantColor(`ravn-${session.ravnId}`) },
      persona: session.personaName,
      notificationType: 'build.complete',
      summary: 'Build completed successfully',
      urgency: 1,
      recommendation: 'Ready for review',
    },
  ];

  return { roomId: `room-${session.id}`, participants, byId, meshEvents };
}

export function buildMockTurns(_session: Session, room: MockRoom): ChatTurn[] {
  const now = Date.now();
  const mainPeer = room.participants[1]!;
  return [
    {
      id: 't-1',
      role: 'user',
      peerId: 'human',
      content: 'Implement the authentication handler with JWT validation',
      ts: now - 600_000,
    },
    {
      id: 't-2',
      role: 'thinking',
      peerId: mainPeer.peerId,
      content:
        'I need to create a JWT validation handler. Let me check the existing auth setup first.',
      ms: 1200,
      ts: now - 590_000,
    },
    {
      id: 't-3',
      role: 'tool',
      peerId: mainPeer.peerId,
      content: '',
      tool: 'read_file',
      args: 'src/auth/handler.ts',
      output: 'export class AuthHandler { ... }',
      status: 'ok',
      dur: '45ms',
      ts: now - 580_000,
    },
    {
      id: 't-4',
      role: 'tool',
      peerId: mainPeer.peerId,
      content: '',
      tool: 'write_file',
      args: 'src/auth/jwt.ts',
      status: 'ok',
      dur: '12ms',
      ts: now - 570_000,
    },
    {
      id: 't-5',
      role: 'assistant',
      peerId: mainPeer.peerId,
      content:
        "I've implemented the JWT validation handler. It validates tokens using RS256 and checks expiry, issuer, and audience claims.",
      tokens: 847,
      ms: 3200,
      ts: now - 560_000,
    },
    {
      id: 't-6',
      role: 'user',
      peerId: 'human',
      content: 'Run the tests to make sure everything passes',
      directedTo: [mainPeer.peerId, 'reviewer-1'],
      ts: now - 400_000,
    },
    {
      id: 't-7',
      role: 'tool',
      peerId: mainPeer.peerId,
      content: '',
      tool: 'run_command',
      args: 'npm test',
      output: 'PASS src/auth/jwt.test.ts\n  14 passing (1.2s)',
      status: 'ok',
      dur: '1.2s',
      ts: now - 390_000,
    },
    {
      id: 't-8',
      role: 'assistant',
      peerId: mainPeer.peerId,
      content: 'All 14 tests pass. The JWT validation is working correctly.',
      tokens: 156,
      ms: 890,
      ts: now - 380_000,
      outcome: {
        verdict: 'pass',
        eventType: 'test.result',
        summary: 'All tests passing',
      },
    },
    {
      id: 't-9',
      role: 'thinking',
      peerId: 'reviewer-1',
      content:
        'Tests passed. I should verify the auth handler also checks issuer and audience before approving this change.',
      ms: 680,
      ts: now - 300_000,
    },
    {
      id: 't-10',
      role: 'tool',
      peerId: 'reviewer-1',
      content: '',
      tool: 'search_files',
      args: 'issuer audience src/auth',
      output: 'src/auth/jwt.ts: validates exp, iss, aud',
      status: 'ok',
      dur: '61ms',
      ts: now - 292_000,
    },
    {
      id: 't-11',
      role: 'assistant',
      peerId: 'reviewer-1',
      content:
        'Reviewer pass: issuer and audience checks are present, and the test run covered the JWT path without regressions.',
      tokens: 218,
      ms: 1040,
      ts: now - 284_000,
      outcome: {
        verdict: 'verified',
        eventType: 'outcome.review',
        summary: 'Reviewer verified issuer and audience coverage',
      },
    },
  ];
}

export function groupTurns(turns: ChatTurn[]): TurnGroup[] {
  const out: TurnGroup[] = [];
  let bucket: { kind: 'toolrun'; peerId: string; turns: ChatTurn[] } | null = null;
  for (const t of turns) {
    if (t.role === 'tool') {
      if (!bucket || bucket.peerId !== t.peerId) {
        bucket = { kind: 'toolrun', peerId: t.peerId, turns: [] };
        out.push(bucket);
      }
      bucket.turns.push(t);
    } else {
      bucket = null;
      if (t.role === 'thinking') {
        out.push({ kind: 'thinking', turn: t });
      } else {
        out.push({ kind: 'single', turn: t });
      }
    }
  }
  return out;
}
