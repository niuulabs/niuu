import { describe, expect, it } from 'vitest';
import type { Session } from '../domain/session';
import {
  compactSourceParts,
  compareSessionsByActivity,
  forgeGroupLabel,
  groupByForge,
  groupByRepo,
  looksLikeRepoLabel,
  repoGroupLabel,
  sessionActivityTs,
  sessionOriginBadge,
  shortenRepoLabel,
  toGroupTestId,
} from './SessionsPage';

function makeSession(
  overrides: Partial<Session> & Pick<Session, 'id' | 'personaName' | 'state'>,
): Session {
  return {
    id: overrides.id,
    ravnId: overrides.ravnId ?? `ravn-${overrides.id}`,
    personaName: overrides.personaName,
    templateId: overrides.templateId ?? 'tpl-default',
    clusterId: overrides.clusterId ?? 'forge-a',
    clusterName: overrides.clusterName,
    state: overrides.state,
    startedAt: overrides.startedAt ?? '2026-05-01T00:00:00.000Z',
    lastActivityAt: overrides.lastActivityAt,
    resources: overrides.resources ?? {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.5,
      memRequestMi: 512,
      memLimitMi: 1024,
      memUsedMi: 256,
      gpuCount: 0,
    },
    env: overrides.env ?? {},
    events: overrides.events ?? [],
    preview: overrides.preview,
    sagaId: overrides.sagaId,
    runId: overrides.runId,
  };
}

describe('SessionsPage helpers', () => {
  it('recognizes and compacts repo labels', () => {
    expect(looksLikeRepoLabel('github.com/niuulabs/volundr#main')).toBe(true);
    expect(looksLikeRepoLabel('~/code/niuu')).toBe(true);
    expect(looksLikeRepoLabel('/workspace/niuu')).toBe(true);
    expect(looksLikeRepoLabel('http://example.test/repo')).toBe(true);
    expect(looksLikeRepoLabel('persona-label')).toBe(false);

    expect(compactSourceParts('github.com/niuulabs/volundr#main')).toEqual({
      label: 'volundr',
      branch: 'main',
    });
    expect(compactSourceParts('https://github.com/niuulabs/volundr.git/')).toEqual({
      label: 'volundr',
    });

    expect(shortenRepoLabel('https://github.com/niuulabs/volundr.git/')).toBe('volundr');
    expect(shortenRepoLabel('~/code/niuu')).toBe('~/code/niuu');
    expect(shortenRepoLabel('/workspace/repo')).toBe('/workspace/repo');
    expect(toGroupTestId('Guild Alpha / Prod')).toBe('guild-alpha-prod');
  });

  it('groups sessions by repo and forge with stable activity ordering', () => {
    const sessions = [
      makeSession({
        id: 'repo-1',
        personaName: 'runner one',
        state: 'running',
        preview: 'github.com/niuulabs/volundr#feat/a',
        clusterId: 'forge-z',
        clusterName: 'Forge Z',
        lastActivityAt: '2026-05-01T00:10:00.000Z',
      }),
      makeSession({
        id: 'repo-2',
        personaName: 'runner two',
        state: 'idle',
        preview: 'github.com/niuulabs/volundr#feat/b',
        clusterId: 'forge-a',
        clusterName: null,
        lastActivityAt: '2026-05-01T00:20:00.000Z',
      }),
      makeSession({
        id: 'path-1',
        personaName: '~/code/local-repo',
        state: 'terminated',
        preview: 'not-a-repo-label',
        clusterId: null,
        clusterName: null,
        startedAt: '2026-05-01T00:30:00.000Z',
      }),
    ];

    expect(repoGroupLabel(sessions[0]!)).toBe('volundr');
    expect(repoGroupLabel(sessions[2]!)).toBe('~/code/local-repo');
    expect(forgeGroupLabel(sessions[0]!)).toBe('Forge Z');
    expect(
      forgeGroupLabel({
        ...sessions[2]!,
        clusterId: undefined,
        clusterName: undefined,
      }),
    ).toBe('unknown forge');
    expect(sessionActivityTs(sessions[2]!)).toBe(Date.parse('2026-05-01T00:30:00.000Z'));
    expect(compareSessionsByActivity(sessions[0]!, sessions[1]!)).toBeGreaterThan(0);

    const repoGroups = groupByRepo(sessions);
    expect(repoGroups.map((group) => group.label)).toEqual(['~/code/local-repo', 'volundr']);
    expect(repoGroups[1]?.sessions.map((session) => session.id)).toEqual(['repo-2', 'repo-1']);

    const forgeGroups = groupByForge(sessions);
    expect(forgeGroups.map((group) => group.label)).toEqual(['Forge Z', 'forge-a']);
  });

  it('derives origin badges only for external CLI origins', () => {
    expect(sessionOriginBadge({ origin: 'claude' })).toBe('claude');
    expect(sessionOriginBadge({ origin: 'codex' })).toBe('codex');
    expect(sessionOriginBadge({ origin: 'volundr' })).toBeNull();
    expect(sessionOriginBadge({ origin: 'manual' })).toBeNull();
    expect(sessionOriginBadge({ origin: undefined })).toBeNull();
  });
});
