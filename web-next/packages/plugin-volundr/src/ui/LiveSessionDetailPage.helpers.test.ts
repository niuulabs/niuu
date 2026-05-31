import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  VolundrSession,
  VolundrSessionTrace,
  VolundrSessionTraceSpan,
} from '../models/volundr.model';
import {
  copyText,
  buildTelemetrySpanTree,
  buildTelemetryTimelineRows,
  buildTelemetryToolOverview,
  buildTelemetryTurnRows,
  collectTelemetryDetailSpans,
  countTelemetryDescendants,
  deriveComparableSourceKey,
  extractTelemetryToolDescriptor,
  eventIcon,
  eventLabel,
  eventTone,
  fileChangeCount,
  formatRepoLabel,
  formatCompactDurationMs,
  formatCount,
  formatCurrencyCents,
  formatDurationMs,
  formatElapsedSince,
  formatEventTime,
  formatPercentOfTotal,
  formatSignedDuration,
  formatStageLabel,
  formatTelemetryTaskLabel,
  formatTimelineHeaderStamp,
  formatTimelineTick,
  formatTraceStamp,
  formatTurnDurationTick,
  isSessionBooting,
  looksLikeRunLabel,
  median,
  nearestTurnAncestorLabel,
  normalizeRepoLink,
  normalizeTimelineRowLabel,
  percentile,
  pickLongestStage,
  roundUpDurationMs,
  sessionForgeLabel,
  spanAttributes,
  timelineRowCategory,
  timelineRowTone,
  truncateLeadingPath,
  truncateMiddle,
} from './LiveSessionDetailPage';
import {
  AlertTriangle,
  FilePenLine,
  GitCommitHorizontal,
  MessageSquareText,
  ScrollText,
  SquareTerminal,
} from 'lucide-react';

function makeSpan(overrides: Partial<VolundrSessionTraceSpan> = {}): VolundrSessionTraceSpan {
  return {
    id: 'span-1',
    sessionId: 'session-1',
    traceId: 'trace-1',
    parentSpanId: null,
    kind: 'session.lifecycle',
    name: 'Session',
    status: 'completed',
    startedAt: '2026-05-23T09:25:01Z',
    endedAt: '2026-05-23T09:40:07Z',
    durationMs: 906_000,
    actorType: 'system',
    actorId: 'session-1',
    actorLabel: 'Session',
    sourceService: 'skuld',
    attributes: {},
    ...overrides,
  };
}

function makeTrace(spans: VolundrSessionTraceSpan[]): VolundrSessionTrace {
  return {
    traceId: 'trace-1',
    sessionId: 'session-1',
    startedAt: spans[0]?.startedAt ?? '2026-05-23T09:25:01Z',
    endedAt: spans[0]?.endedAt ?? '2026-05-23T09:40:07Z',
    durationMs: spans[0]?.durationMs ?? 0,
    spans,
    lanes: [],
  };
}

describe('LiveSessionDetailPage helpers', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-31T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('formats session status, counts, durations, and labels across edge cases', () => {
    expect(isSessionBooting('starting')).toBe(true);
    expect(isSessionBooting('provisioning')).toBe(true);
    expect(isSessionBooting('running')).toBe(false);

    expect(formatCount(Number.NaN)).toBe('0');
    expect(formatCount(1_250)).toBe('1.3k');
    expect(formatCount(12_500)).toBe('13k');
    expect(formatCount(1_250_000)).toBe('1.3m');
    expect(formatCount(12_500_000)).toBe('13m');

    expect(formatElapsedSince()).toBe('—');
    expect(formatElapsedSince('not-a-date')).toBe('—');
    expect(formatElapsedSince('2026-05-31T11:59:40Z')).toBe('<1m');
    expect(formatElapsedSince('2026-05-31T11:05:00Z')).toBe('55m');
    expect(formatElapsedSince('2026-05-31T09:30:00Z')).toBe('2h 30m');
    expect(formatElapsedSince('2026-05-29T10:00:00Z')).toBe('2d 2h');

    expect(formatCurrencyCents(undefined)).toBe('—');
    expect(formatCurrencyCents(Number.NaN)).toBe('—');
    expect(formatCurrencyCents(1234)).toBe('$12.34');

    expect(formatEventTime(125)).toBe('2:05');
    expect(formatDurationMs(0)).toBe('0s');
    expect(formatDurationMs(8_000)).toBe('8s');
    expect(formatDurationMs(125_000)).toBe('2m 05s');
    expect(formatDurationMs(3_660_000)).toBe('1h 01m');
    expect(formatCompactDurationMs(9_500)).toBe('9.5s');
    expect(formatCompactDurationMs(75_000)).toBe('1m 15s');
    expect(formatPercentOfTotal(20, 0)).toBe('0% of total');
    expect(formatPercentOfTotal(25, 50)).toBe('50% of total');
    expect(formatTraceStamp()).toBe('--:--:--Z');
    expect(formatTraceStamp('not-a-date')).toBe('--:--:--Z');
    expect(formatTraceStamp('2026-05-23T09:31:21Z')).toBe('09:31:21Z');
    expect(formatSignedDuration(0)).toBe('±0s');
    expect(formatSignedDuration(12_000)).toBe('+12s');
    expect(formatSignedDuration(-125_000)).toBe('-2m 05s');
    expect(formatTimelineTick(0)).toBe('0ms');
    expect(formatTimelineTick(9_000)).toBe('9s');
    expect(formatTimelineTick(120_000)).toBe('2m');
    expect(formatTimelineTick(125_000)).toBe('2m 05s');
    expect(formatTimelineHeaderStamp()).toBe('--:--:--Z');
    expect(formatTimelineHeaderStamp('2026-05-23T09:31:21Z')).toBe('09:31:21Z');
    expect(formatTurnDurationTick(0)).toBe('0ms');
    expect(formatTurnDurationTick(61_000)).toBe('1m 01s');
  });

  it('derives forge labels, source keys, and file change counts', () => {
    const session: VolundrSession = {
      id: 'session-1',
      name: 'release',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      status: 'running',
      instanceName: 'Guild Alpha',
      instanceId: 'instance-1',
      hostname: 'guild.local',
    };

    expect(sessionForgeLabel(session)).toBe('Guild Alpha');
    expect(sessionForgeLabel({ ...session, instanceName: undefined })).toBe('instance-1');
    expect(sessionForgeLabel({ ...session, instanceName: undefined, instanceId: undefined })).toBe(
      'guild.local',
    );
    expect(
      sessionForgeLabel({ ...session, instanceName: undefined, instanceId: undefined, hostname: undefined }),
    ).toBe('shared');

    expect(fileChangeCount()).toBeUndefined();
    expect(fileChangeCount({ added: 1, modified: 2, deleted: 3 })).toBe(6);

    expect(deriveComparableSourceKey(undefined)).toBe('unknown');
    expect(deriveComparableSourceKey(session)).toBe('git:github.com/niuulabs/volundr:main');
    expect(
      deriveComparableSourceKey({
        ...session,
        source: {
          type: 'local_mount',
          local_path: '',
          path: '/workspace',
          paths: [{ host_path: '/tmp/project', mount_path: '/workspace', read_only: false }],
        },
      }),
    ).toBe('local:');
    expect(
      deriveComparableSourceKey({
        ...session,
        source: {
          type: 'local_mount',
          local_path: undefined,
          path: undefined,
          paths: [{ host_path: '/tmp/project', mount_path: '/workspace', read_only: false }],
        },
      }),
    ).toBe('local:/tmp/project');

    expect(looksLikeRunLabel('s-120')).toBe(true);
    expect(looksLikeRunLabel('run#14')).toBe(true);
    expect(looksLikeRunLabel('release-train')).toBe(false);
  });

  it('maps chronicle event types, truncates paths, and formats repo links', async () => {
    expect(eventTone('message')).toMatchObject({ dot: 'niuu-bg-sky-400' });
    expect(eventTone('file')).toMatchObject({ dot: 'niuu-bg-emerald-400' });
    expect(eventTone('git')).toMatchObject({ dot: 'niuu-bg-violet-400' });
    expect(eventTone('terminal')).toMatchObject({ dot: 'niuu-bg-amber-400' });
    expect(eventTone('error')).toMatchObject({ dot: 'niuu-bg-rose-400' });
    expect(eventTone('other' as never)).toMatchObject({ dot: 'niuu-bg-text-muted' });

    expect(eventIcon('message')).toBe(MessageSquareText);
    expect(eventIcon('file')).toBe(FilePenLine);
    expect(eventIcon('git')).toBe(GitCommitHorizontal);
    expect(eventIcon('terminal')).toBe(SquareTerminal);
    expect(eventIcon('error')).toBe(AlertTriangle);
    expect(eventIcon('other' as never)).toBe(ScrollText);

    expect(eventLabel('message')).toBe('Message');
    expect(eventLabel('file')).toBe('File');
    expect(eventLabel('git')).toBe('Commit');
    expect(eventLabel('terminal')).toBe('Terminal');
    expect(eventLabel('error')).toBe('Error');
    expect(eventLabel('other' as never)).toBe('Session');

    expect(truncateLeadingPath('/short/path', 42)).toBe('/short/path');
    expect(truncateLeadingPath('/one/two', 7)).toBe('…ne/two');
    expect(truncateLeadingPath('/workspace/repo/src/components/DeepFile.tsx', 22)).toBe(
      '…/DeepFile.tsx',
    );
    expect(truncateMiddle('short value', 30)).toBe('short value');
    expect(truncateMiddle('abcdefghijklmnopqrstuvwxyz', 10)).toBe('abcde...yz');

    expect(normalizeRepoLink(undefined)).toBeNull();
    expect(normalizeRepoLink({ type: 'git', repo: '', branch: 'main' } as never)).toBeNull();
    expect(
      normalizeRepoLink({ type: 'git', repo: 'https://github.com/niuulabs/volundr', branch: 'main' } as never),
    ).toBe('https://github.com/niuulabs/volundr');
    expect(normalizeRepoLink({ type: 'git', repo: 'niuulabs/volundr', branch: 'main' } as never)).toBe(
      'https://github.com/niuulabs/volundr',
    );

    expect(formatRepoLabel('git@github.com:niuulabs/volundr.git')).toBe('niuulabs/volundr');
    expect(formatRepoLabel('niuulabs/volundr')).toBe('niuulabs/volundr');
    expect(formatRepoLabel('github.com/niuulabs/volundr')).toBe('niuulabs/volundr');
    expect(formatRepoLabel('https://github.com/niuulabs/volundr.git')).toBe('niuulabs/volundr');
    expect(formatRepoLabel('https://git.example.com/team/repo')).toBe('git.example.com/team/repo');

    const originalNavigator = globalThis.navigator;
    vi.stubGlobal('navigator', undefined);
    await expect(copyText('main')).resolves.toBe(false);

    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    await expect(copyText('feature/dev')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('feature/dev');

    const failedWrite = vi.fn().mockRejectedValue(new Error('denied'));
    vi.stubGlobal('navigator', { clipboard: { writeText: failedWrite } });
    await expect(copyText('release')).resolves.toBe(false);

    vi.stubGlobal('navigator', originalNavigator);
  });

  it('covers median, percentile, and stage-label helpers', () => {
    const toolSpan = makeSpan({ id: 'tool', parentSpanId: 'workflow', kind: 'tool.call', name: 'Write' });
    const workflowSpan = makeSpan({
      id: 'workflow',
      parentSpanId: 'root',
      kind: 'session.workflow',
      name: 'Execution',
      durationMs: 520_000,
    });

    expect(median([])).toBeNull();
    expect(median([9, 1, 5])).toBe(5);
    expect(median([9, 1, 5, 7])).toBe(6);
    expect(percentile([], 0.95)).toBeNull();
    expect(percentile([10, 20, 30], 0)).toBe(10);
    expect(percentile([10, 20, 30], 0.95)).toBe(30);
    expect(roundUpDurationMs(0, 15_000)).toBe(15_000);
    expect(roundUpDurationMs(31_000, 15_000)).toBe(45_000);

    expect(
      pickLongestStage(
        makeTrace([
          makeSpan({ id: 'root' }),
          workflowSpan,
          { ...toolSpan, durationMs: 21_000 },
          makeSpan({ id: 'wait', parentSpanId: 'workflow', kind: 'wait.permission', durationMs: 12_000 }),
        ]),
        { longestSpan: toolSpan } as never,
      )?.id,
    ).toBe('workflow');
    expect(pickLongestStage(makeTrace([makeSpan({ id: 'root' })]), { longestSpan: toolSpan } as never)?.id).toBe(
      'tool',
    );

    expect(formatStageLabel(null)).toBe('n/a');
    expect(formatStageLabel(workflowSpan)).toBe('execution');
    expect(formatStageLabel(makeSpan({ kind: 'turn.assistant', name: 'Draft' }))).toBe('draft');
    expect(formatStageLabel(toolSpan)).toBe('Write');
    expect(formatStageLabel(makeSpan({ kind: 'custom.stage', name: '', actorLabel: 'unused' }))).toBe(
      'custom.stage',
    );
  });

  it('normalizes telemetry row labels, categories, tones, and task labels', () => {
    const peerTurn = makeSpan({
      kind: 'turn.peer',
      name: 'Execute patch',
      actorLabel: 'Execution',
      actorType: 'peer',
    });
    const userTurn = makeSpan({
      kind: 'turn.user',
      name: 'Question',
      actorLabel: undefined,
      actorType: 'user',
    });
    const sessionSpan = makeSpan({ kind: 'session.publish', name: 'Publish', actorType: 'system' });
    const waitSpan = makeSpan({ kind: 'wait.permission', name: 'Await approval', actorType: 'assistant' });
    const blockedSpan = makeSpan({ kind: 'tool.call', name: 'Write', status: 'failed', actorType: 'assistant' });
    const terminalSpan = makeSpan({ kind: 'terminal.command', name: 'npm test' });

    expect(normalizeTimelineRowLabel(makeSpan({ kind: 'session.workflow', name: 'Execution' }))).toBe(
      'execution',
    );
    expect(normalizeTimelineRowLabel(peerTurn)).toBe('execution');
    expect(normalizeTimelineRowLabel(userTurn)).toBe('input');
    expect(normalizeTimelineRowLabel(sessionSpan)).toBe('publish');
    expect(normalizeTimelineRowLabel(makeSpan({ kind: 'custom.event', name: '' }))).toBe('custom.event');

    expect(timelineRowCategory(waitSpan)).toBe('wait');
    expect(timelineRowCategory(peerTurn)).toBe('work');
    expect(timelineRowCategory(userTurn)).toBe('input');
    expect(timelineRowCategory(makeSpan({ kind: 'session.workflow' }))).toBe('workflow');
    expect(timelineRowCategory(makeSpan({ kind: 'custom.event', actorType: undefined }))).toBe('system');

    expect(timelineRowTone(blockedSpan)).toBe('blocked');
    expect(timelineRowTone(waitSpan)).toBe('wait');
    expect(timelineRowTone(peerTurn)).toBe('active');
    expect(timelineRowTone(terminalSpan)).toBe('active');
    expect(timelineRowTone(makeSpan({ kind: 'custom.event' }))).toBe('system');

    expect(formatTelemetryTaskLabel(blockedSpan)).toBe('tool · write');
    expect(formatTelemetryTaskLabel(terminalSpan)).toBe('terminal · npm test');
    expect(formatTelemetryTaskLabel(waitSpan)).toBe('wait · await approval');
    expect(formatTelemetryTaskLabel(peerTurn)).toBe('peer · execution');
    expect(formatTelemetryTaskLabel(sessionSpan)).toBe('session · publish · publish');
  });

  it('builds telemetry trees, tool descriptors, and aggregate groups', () => {
    const root = makeSpan({ id: 'root' });
    const workflow = makeSpan({
      id: 'workflow',
      parentSpanId: 'root',
      kind: 'session.workflow',
      name: 'Execution',
      startedAt: '2026-05-23T09:29:12Z',
      durationMs: 520_000,
    });
    const earlyTool = makeSpan({
      id: 'tool-early',
      parentSpanId: 'workflow',
      kind: 'tool.call',
      name: 'Read docs',
      startedAt: '2026-05-23T09:29:14Z',
      durationMs: 5_000,
    });
    const lateTool = makeSpan({
      id: 'tool-late',
      parentSpanId: 'workflow',
      kind: 'terminal.command',
      name: 'git status',
      startedAt: '2026-05-23T09:29:40Z',
      durationMs: 7_000,
      status: 'cancelled',
      attributes: { command: 'git status' },
    });
    const mcpTool = makeSpan({
      id: 'tool-mcp',
      parentSpanId: 'workflow',
      kind: 'tool.call',
      name: 'github.search pulls',
      startedAt: '2026-05-23T09:29:50Z',
      durationMs: 3_000,
    });
    const editTool = makeSpan({
      id: 'tool-edit',
      parentSpanId: 'workflow',
      kind: 'tool.call',
      name: 'Update plan',
      startedAt: '2026-05-23T09:29:55Z',
      durationMs: 2_000,
    });
    const writeTool = makeSpan({
      id: 'tool-write',
      parentSpanId: 'workflow',
      kind: 'tool.call',
      name: 'Commit changes',
      startedAt: '2026-05-23T09:29:57Z',
      durationMs: 1_000,
    });
    const otherTool = makeSpan({
      id: 'tool-other',
      parentSpanId: 'workflow',
      kind: 'tool.call',
      name: 'CustomAction',
      startedAt: '2026-05-23T09:29:58Z',
      durationMs: 4_000,
      attributes: 'skip-object' as never,
    });
    const trace = makeTrace([root, workflow, lateTool, earlyTool, mcpTool, editTool, writeTool, otherTool]);

    const { root: builtRoot, nodeById } = buildTelemetrySpanTree(trace);
    expect(builtRoot?.span.id).toBe('root');
    expect(nodeById.get('workflow')?.children.map((child) => child.span.id)).toEqual([
      'tool-early',
      'tool-late',
      'tool-mcp',
      'tool-edit',
      'tool-write',
      'tool-other',
    ]);
    expect(countTelemetryDescendants(nodeById.get('workflow')!)).toBe(6);
    expect(collectTelemetryDetailSpans(nodeById.get('workflow')!)).toHaveLength(6);
    expect(spanAttributes(otherTool)).toEqual({});

    expect(extractTelemetryToolDescriptor(lateTool)).toMatchObject({
      category: 'shell',
      badge: 'shell',
      subtitle: 'git status',
    });
    expect(extractTelemetryToolDescriptor(mcpTool)).toMatchObject({
      category: 'mcp',
      title: 'github.search',
      subtitle: 'pulls',
    });
    expect(extractTelemetryToolDescriptor(earlyTool)).toMatchObject({ category: 'read', badge: 'read' });
    expect(extractTelemetryToolDescriptor(editTool)).toMatchObject({ category: 'edit', badge: 'edit' });
    expect(extractTelemetryToolDescriptor(writeTool)).toMatchObject({ category: 'write', badge: 'write' });
    expect(extractTelemetryToolDescriptor(otherTool)).toMatchObject({
      category: 'other',
      title: 'customaction',
    });

    const overview = buildTelemetryToolOverview(trace);
    expect(overview.totalToolCalls).toBe(6);
    expect(overview.totalToolMs).toBe(22_000);
    expect(overview.primaryFilters.map((filter) => filter.key)).toEqual([
      'all',
      'read',
      'edit',
      'write',
      'shell',
      'mcp',
      'other',
    ]);
    expect(overview.rows.find((row) => row.id === 'shell')?.blockedCount).toBe(1);
    expect(overview.rows.find((row) => row.id === 'mcp:github.search')?.subtitle).toBe('pulls');
  });

  it('builds telemetry timeline and turn breakdown rows for direct and nested turns', () => {
    const root = makeSpan({ id: 'root' });
    const workflow = makeSpan({
      id: 'workflow',
      parentSpanId: 'root',
      kind: 'session.workflow',
      name: 'Execution',
      actorType: 'workflow',
      actorLabel: 'Execution',
      startedAt: '2026-05-23T09:29:12Z',
      durationMs: 300_000,
    });
    const wait = makeSpan({
      id: 'wait',
      parentSpanId: 'workflow',
      kind: 'wait.permission',
      name: 'Await approval',
      startedAt: '2026-05-23T09:29:22Z',
      durationMs: 30_000,
      actorType: 'assistant',
    });
    const blocked = makeSpan({
      id: 'blocked',
      parentSpanId: 'workflow',
      kind: 'tool.call',
      name: 'Write',
      startedAt: '2026-05-23T09:29:55Z',
      durationMs: 50_000,
      status: 'failed',
      actorType: 'assistant',
    });
    const peerTurn = makeSpan({
      id: 'turn-peer',
      parentSpanId: 'workflow',
      kind: 'turn.peer',
      name: 'Execute patch',
      actorLabel: 'Execution',
      actorType: 'peer',
      startedAt: '2026-05-23T09:30:00Z',
      durationMs: 140_000,
    });
    const assistantTurn = makeSpan({
      id: 'turn-assistant',
      parentSpanId: 'workflow',
      kind: 'turn.assistant',
      name: 'Draft response',
      actorLabel: 'Planner',
      actorType: 'assistant',
      startedAt: '2026-05-23T09:32:40Z',
      durationMs: 40_000,
    });
    const nestedTurn = makeSpan({
      id: 'turn-nested',
      parentSpanId: 'turn-peer',
      kind: 'turn.assistant',
      name: 'Nested analysis',
      actorLabel: 'Analyst',
      actorType: 'assistant',
      startedAt: '2026-05-23T09:30:10Z',
      durationMs: 70_000,
      sourceService: 'worker',
    });
    const nestedTool = makeSpan({
      id: 'nested-tool',
      parentSpanId: 'turn-nested',
      kind: 'tool.call',
      name: 'Search',
      startedAt: '2026-05-23T09:30:20Z',
      durationMs: 10_000,
    });
    const nestedWait = makeSpan({
      id: 'nested-wait',
      parentSpanId: 'turn-nested',
      kind: 'wait.idle',
      name: 'Operator away',
      startedAt: '2026-05-23T09:30:35Z',
      durationMs: 5_000,
    });

    const trace = makeTrace([
      root,
      workflow,
      wait,
      blocked,
      peerTurn,
      assistantTurn,
      nestedTurn,
      nestedTool,
      nestedWait,
    ]);
    const rows = buildTelemetryTimelineRows(trace);
    const turnRows = buildTelemetryTurnRows(trace);
    const { nodeById } = buildTelemetrySpanTree(trace);

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      id: 'workflow',
      tone: 'active',
      category: 'workflow',
      childSpanCount: 4,
      hatch: false,
    });
    expect(rows[0]?.stateDurations.wait).toBe(30_000);
    expect(rows[0]?.stateDurations.blocked).toBe(50_000);
    expect(rows[0]?.childSegments).toHaveLength(4);

    expect(nearestTurnAncestorLabel(nodeById.get('nested-tool')!, nodeById)).toBe('analyst');
    expect(turnRows).toHaveLength(1);
    expect(turnRows[0]).toMatchObject({
      id: 'turn-nested',
      label: 'turn #1',
      stageLabel: 'execution',
      toolCallCount: 1,
      idleLabel: 'Operator away',
      sourceService: 'worker',
    });
    expect(turnRows[0]?.modelWaitMs).toBe(55_000);
    expect(turnRows[0]?.toolMs).toBe(10_000);
    expect(turnRows[0]?.idleMs).toBe(5_000);

    const directOnlyTrace = makeTrace([root, workflow, assistantTurn]);
    expect(buildTelemetryTurnRows(directOnlyTrace)).toHaveLength(1);
  });

  it('covers additional telemetry helper fallbacks and clamps', () => {
    const lifecycleRoot = makeSpan({ id: 'lifecycle-root', kind: 'session.lifecycle', name: 'Session' });
    const alternateRoot = makeSpan({ id: 'alternate-root', kind: 'session.publish', name: 'Publish' });
    const waitStage = makeSpan({
      id: 'wait-stage',
      parentSpanId: 'lifecycle-root',
      kind: 'wait.permission',
      name: 'Await approval',
      startedAt: '2026-05-23T09:29:20Z',
      durationMs: 20_000,
      actorType: 'assistant',
    });
    const blockedStage = makeSpan({
      id: 'blocked-stage',
      parentSpanId: 'lifecycle-root',
      kind: 'tool.call',
      name: 'Write',
      startedAt: '2026-05-23T09:29:50Z',
      durationMs: 10_000,
      status: 'failed',
      actorType: 'assistant',
    });
    const clampedWaitChild = makeSpan({
      id: 'clamped-wait',
      parentSpanId: 'blocked-stage',
      kind: 'wait.idle',
      name: 'Waiting',
      startedAt: '2026-05-23T09:30:05Z',
      durationMs: 15_000,
      actorType: 'assistant',
    });
    const directTurn = makeSpan({
      id: 'direct-turn',
      parentSpanId: 'lifecycle-root',
      kind: 'turn.assistant',
      name: 'Draft response',
      actorLabel: undefined,
      actorType: 'assistant',
      durationMs: 25_000,
      startedAt: '2026-05-23T09:30:20Z',
      sourceService: undefined,
    });
    const directWait = makeSpan({
      id: 'direct-wait',
      parentSpanId: 'direct-turn',
      kind: 'wait.permission',
      name: '',
      durationMs: 5_000,
      startedAt: '2026-05-23T09:30:30Z',
    });
    const trace = makeTrace([
      lifecycleRoot,
      waitStage,
      blockedStage,
      clampedWaitChild,
      directTurn,
      directWait,
    ]);

    const { root, nodeById } = buildTelemetrySpanTree(
      makeTrace([
        alternateRoot,
        lifecycleRoot,
        waitStage,
        blockedStage,
        clampedWaitChild,
        directTurn,
        directWait,
      ]),
    );
    expect(root?.span.id).toBe('lifecycle-root');
    expect(nearestTurnAncestorLabel(nodeById.get('blocked-stage')!, nodeById)).toBeNull();
    expect(nearestTurnAncestorLabel({ span: makeSpan({ parentSpanId: 'missing-parent' }), children: [] }, nodeById)).toBeNull();

    expect(spanAttributes(makeSpan({ attributes: null as never }))).toEqual({});
    expect(percentile([10, 20, 30], 2)).toBe(30);
    expect(percentile([10, 20, 30], -1)).toBe(10);

    expect(extractTelemetryToolDescriptor(makeSpan({
      kind: 'tool.call',
      name: 'Fallback',
      attributes: { tool_name: 'github.search' },
    }))).toMatchObject({
      category: 'mcp',
      title: 'github.search',
      subtitle: null,
    });
    expect(extractTelemetryToolDescriptor(makeSpan({
      kind: 'tool.call',
      name: 'Custom tool',
      attributes: { command: 'pnpm test --runInBand' },
    }))).toMatchObject({
      category: 'shell',
      subtitle: 'pnpm test --runInBand',
    });

    const timelineRows = buildTelemetryTimelineRows(trace);
    expect(timelineRows).toHaveLength(3);
    expect(timelineRows.find((row) => row.id === 'wait-stage')).toMatchObject({
      tone: 'wait',
      stateDurations: { active: 0, wait: 20_000, blocked: 0 },
    });
    expect(timelineRows.find((row) => row.id === 'blocked-stage')).toMatchObject({
      tone: 'blocked',
      stateDurations: { active: 0, wait: 10_000, blocked: 10_000 },
    });
    expect(timelineRows.find((row) => row.id === 'blocked-stage')?.childSegments).toEqual([]);

    expect(formatTelemetryTaskLabel(makeSpan({ kind: 'wait.permission', name: '' }))).toBe(
      'wait · permission',
    );
    expect(formatTelemetryTaskLabel(makeSpan({ kind: 'turn.user', name: 'Question', actorLabel: 'Operator' }))).toBe(
      'user · Operator',
    );

    const directTurnRows = buildTelemetryTurnRows(trace);
    expect(directTurnRows).toHaveLength(1);
    expect(directTurnRows[0]).toMatchObject({
      id: 'direct-turn',
      stageLabel: 'draft response',
      idleLabel: '',
      sourceService: undefined,
    });

    expect(buildTelemetryTimelineRows(makeTrace([]))).toEqual([]);
    expect(
      buildTelemetryTurnRows(
        makeTrace([
          makeSpan({
            id: 'orphan-turn',
            parentSpanId: null,
            kind: 'turn.peer',
            name: 'Execute',
            actorLabel: undefined,
            durationMs: 15_000,
          }),
        ]),
      )[0],
    ).toMatchObject({
      id: 'orphan-turn',
      stageLabel: 'execute',
      toolCallCount: 0,
    });
  });
});
