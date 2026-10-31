import { describe, expect, it } from 'vitest';
import {
  isForgeNotificationKind,
  isForgeNotificationSeverity,
  isForgeNotificationSource,
  isForgeNotifyCall,
  parseForgeNotificationLinks,
  parseForgeNotificationPayload,
  severityRank,
} from './forge-notification';

describe('forge notification value types', () => {
  it('parses the broker turn input into a payload', () => {
    expect(
      parseForgeNotificationPayload({
        notification_id: 'n-1',
        kind: 'milestone',
        severity: 'success',
        title: '  Migration done ',
        body: 'All **green**',
        links: [
          { label: 'PR', url: 'https://example.test/pr/1', kind: 'pr', file_id: null },
          { label: 'Report', file_id: 'f_abc', kind: 'file' },
        ],
        correlation_id: 'corr',
      }),
    ).toEqual({
      notificationId: 'n-1',
      kind: 'milestone',
      severity: 'success',
      title: 'Migration done',
      body: 'All **green**',
      links: [
        { label: 'PR', url: 'https://example.test/pr/1', kind: 'pr', fileId: null },
        { label: 'Report', url: null, kind: 'file', fileId: 'f_abc' },
      ],
      correlationId: 'corr',
    });
  });

  it('degrades unknown kinds, severities and link kinds instead of hiding the card', () => {
    const payload = parseForgeNotificationPayload({
      kind: 'party',
      severity: 'apocalyptic',
      title: 'Hello',
      links: [{ label: 'Artifact', fileId: 'f1', kind: 'mystery' }],
    });
    expect(payload).toMatchObject({
      kind: 'info',
      severity: 'info',
      body: '',
      notificationId: null,
      correlationId: null,
      links: [{ label: 'Artifact', url: null, kind: 'file', fileId: 'f1' }],
    });
  });

  it('returns null when there is no title to show', () => {
    expect(parseForgeNotificationPayload(null)).toBeNull();
    expect(parseForgeNotificationPayload('title')).toBeNull();
    expect(parseForgeNotificationPayload({ kind: 'info', title: '   ' })).toBeNull();
  });

  it('drops malformed links', () => {
    expect(parseForgeNotificationLinks('nope')).toEqual([]);
    expect(
      parseForgeNotificationLinks([
        null,
        'x',
        { url: 'https://no-label.test' },
        { label: 'no target' },
        { label: 'ok', url: 'https://ok.test' },
      ]),
    ).toEqual([{ label: 'ok', url: 'https://ok.test', kind: 'url', fileId: null }]);
  });

  it('ranks severities like the backend and ranks unknown values lowest', () => {
    expect(severityRank('info')).toBe(0);
    expect(severityRank('success')).toBe(1);
    expect(severityRank('warning')).toBe(2);
    expect(severityRank('critical')).toBe(3);
    expect(severityRank('loud')).toBe(0);
    expect(severityRank(undefined)).toBe(0);
  });

  it('guards kind, severity and source values', () => {
    expect(isForgeNotificationKind('reply_ready')).toBe(true);
    expect(isForgeNotificationKind('nope')).toBe(false);
    expect(isForgeNotificationKind(3)).toBe(false);
    expect(isForgeNotificationSeverity('critical')).toBe(true);
    expect(isForgeNotificationSeverity('fatal')).toBe(false);
    expect(isForgeNotificationSource('operator')).toBe(true);
    expect(isForgeNotificationSource('robot')).toBe(false);
  });

  it('recognises the Forge MCP notify call for Claude and Codex transcripts', () => {
    expect(isForgeNotifyCall('mcp__forge__notify')).toBe(true);
    expect(isForgeNotifyCall('MCP__forge__notify')).toBe(true);
    expect(isForgeNotifyCall('forge.notify')).toBe(true);
    expect(isForgeNotifyCall('mcp__forge__forge_notify')).toBe(true);
    expect(isForgeNotifyCall('notify', { kind: 'milestone', title: 'x' })).toBe(true);
    expect(isForgeNotifyCall('notify', { kind: 'slack', title: 'x' })).toBe(false);
    expect(isForgeNotifyCall('notify')).toBe(false);
    expect(isForgeNotifyCall('mcp__other__notify')).toBe(false);
    expect(isForgeNotifyCall('Bash', { kind: 'info', title: 'x' })).toBe(false);
  });
});
