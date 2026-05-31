import { describe, expect, it, vi } from 'vitest';
import type { RavnWardenSummary } from '../application/useRavns';
import {
  consoleGatewayUrl,
  consoleTransportProtocol,
  deploymentLabel,
  formatConsoleAddress,
  formatDeploymentValue,
  formatModelOption,
  installLabel,
  lifecycleCopy,
  normalizeDeployment,
  normalizeLogLevel,
  startLabel,
  stopLabel,
  toStructuredLogs,
  toTitleCase,
  toggleSelection,
  uninstallLabel,
} from './RavnsPage';

const baseWarden: RavnWardenSummary = {
  id: 'warden-1',
  name: 'Warden One',
  persona: 'mimir-warden',
  profile: '',
  deployment: 'launchd',
  deploymentKwargs: {},
  model: 'claude-sonnet-4-6',
  mountNames: ['local'],
  writeMount: 'local',
  readMountNames: ['local'],
  writeMountNames: ['local'],
  status: 'active',
  summary: 'summary',
  lastDreamAt: null,
  totalDreams: 0,
  pagesTouched: 0,
  expertise: [],
  console: {
    enabled: true,
    host: '0.0.0.0',
    publicHost: '',
    port: 4317,
  },
};

describe('RavnsPage helpers', () => {
  it('toggles selections and formats model labels', () => {
    expect(toggleSelection(['a'], 'b', true)).toEqual(['a', 'b']);
    expect(toggleSelection(['a'], 'a', true)).toEqual(['a']);
    expect(toggleSelection(['a', 'b'], 'a', false)).toEqual(['b']);

    expect(formatModelOption('gpt-test')).toBe('gpt-test');
    expect(
      formatModelOption('gpt-test', { name: 'GPT Test', provider: 'openai', tier: 'fast' }),
    ).toBe('GPT Test · openai · fast');
    expect(formatModelOption('gpt-test', { vendor: 'OpenAI' })).toBe('gpt-test · OpenAI');
  });

  it('normalizes deployment labels and lifecycle copy across targets', () => {
    expect(normalizeDeployment('launchd')).toBe('launchd');
    expect(normalizeDeployment('weird')).toBe('unknown');

    expect(deploymentLabel('systemd')).toContain('systemd');
    expect(deploymentLabel('')).toBe('unknown');
    expect(lifecycleCopy('k8s-apply')).toContain('cluster');
    expect(lifecycleCopy('mystery')).toContain('deployment artifact');

    expect(installLabel('launchd', false)).toBe('Install on this Mac');
    expect(installLabel('launchd', true)).toBe('Reinstall on this Mac');
    expect(startLabel('k8s-gitops', false)).toBe('Set desired scale to 1');
    expect(startLabel('k8s-gitops', true)).toBe('Desired state active');
    expect(stopLabel('k8s-apply')).toBe('Scale down');
    expect(uninstallLabel('systemd')).toBe('Remove service');
  });

  it('formats title case, deployment values, and console gateway urls', () => {
    expect(toTitleCase('snake_case-value')).toBe('Snake case value');
    expect(formatDeploymentValue(true)).toBe('true');
    expect(formatDeploymentValue(5)).toBe('5');
    expect(formatDeploymentValue({ replicas: 2 })).toBe('{"replicas":2}');

    vi.stubGlobal('window', {
      ...(globalThis.window ?? {}),
      location: {
        ...(globalThis.window?.location ?? {}),
        hostname: 'niuu.local',
        protocol: 'https:',
      },
    });

    try {
      expect(formatConsoleAddress(baseWarden)).toBe('niuu.local:4317');
      expect(consoleTransportProtocol('http')).toBe('https');
      expect(consoleTransportProtocol('ws')).toBe('wss');
      expect(consoleGatewayUrl(baseWarden, '/health')).toBe('https://niuu.local:4317/health');
      expect(consoleGatewayUrl(baseWarden, '/events', 'ws')).toBe('wss://niuu.local:4317/events');
      expect(
        consoleGatewayUrl({ console: { ...baseWarden.console, enabled: false } }, '/health'),
      ).toBeNull();
      expect(
        consoleGatewayUrl({ console: { ...baseWarden.console, port: 0 } }, '/health'),
      ).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('normalizes log levels and structured log payloads', () => {
    expect(normalizeLogLevel('error')).toBe('error');
    expect(normalizeLogLevel('warning')).toBe('warn');
    expect(normalizeLogLevel('debug')).toBe('debug');
    expect(normalizeLogLevel('trace')).toBe('info');

    const participant = { id: 'daemon', label: 'Daemon', kind: 'system' } as const;
    const logs = toStructuredLogs(
      'session-1',
      [
        {
          id: 'log-1',
          timestamp: '2026-05-31T12:00:00.000Z',
          level: 'warning',
          logger: 'warden',
          source: 'stdout',
          message: 'started',
        },
        {
          id: 'log-2',
          timestamp: 'not-a-date',
          level: 'unknown',
          logger: '',
          source: 'stderr',
          message: 'fallback',
        },
      ],
      participant,
    );

    expect(logs).toHaveLength(2);
    expect(logs[0]).toMatchObject({
      level: 'warn',
      source: 'warden',
      stream: 'stdout',
      participant: 'daemon',
    });
    expect(logs[1]).toMatchObject({
      level: 'info',
      source: '',
      stream: 'stderr',
      sequence: 2,
    });
    expect(logs[1]!.timestamp).toBeGreaterThan(0);
  });
});
