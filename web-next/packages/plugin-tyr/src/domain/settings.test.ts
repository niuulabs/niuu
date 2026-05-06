import { describe, it, expect } from 'vitest';
import {
  flockConfigSchema,
  dispatchDefaultsSchema,
  notificationSettingsSchema,
  auditEntrySchema,
  auditFilterSchema,
  type FlockConfig,
  type DispatchDefaults,
  type NotificationSettings,
  type AuditEntry,
  type AuditFilter,
} from './settings';

const VALID_FLOCK: FlockConfig = {
  flockName: 'Niuu Core',
  defaultBaseBranch: 'main',
  defaultTrackerType: 'linear',
  defaultRepos: ['niuulabs/volundr'],
  maxActiveSagas: 5,
  autoCreateMilestones: true,
  updatedAt: '2026-01-01T00:00:00Z',
};

const VALID_DISPATCH_DEFAULTS: DispatchDefaults = {
  confidenceThreshold: 70,
  maxConcurrentRaids: 3,
  autoContinue: false,
  batchSize: 10,
  retryPolicy: {
    maxRetries: 2,
    retryDelaySeconds: 30,
    escalateOnExhaustion: true,
  },
  quietHours: '22:00–07:00 UTC',
  escalateAfter: '30m',
  updatedAt: '2026-01-01T00:00:00Z',
};

const VALID_NOTIFICATION_SETTINGS: NotificationSettings = {
  channel: 'telegram',
  onRaidPendingApproval: true,
  onRaidMerged: false,
  onRaidFailed: true,
  onSagaComplete: true,
  onDispatcherError: true,
  webhookUrl: null,
  updatedAt: '2026-01-01T00:00:00Z',
};

const VALID_AUDIT_ENTRY: AuditEntry = {
  id: '00000000-0000-0000-0000-000000000001',
  kind: 'settings.flock_config.updated',
  summary: 'Updated flock name to "Niuu Core"',
  actor: 'user-1',
  payload: { before: { flockName: 'Old' }, after: { flockName: 'Niuu Core' } },
  createdAt: '2026-01-01T00:00:00Z',
};

describe('flockConfigSchema', () => {
  it('parses a valid flock config', () => {
    expect(flockConfigSchema.parse(VALID_FLOCK)).toEqual(VALID_FLOCK);
  });

  it('rejects empty flockName', () => {
    expect(() => flockConfigSchema.parse({ ...VALID_FLOCK, flockName: '' })).toThrow();
  });

  it('rejects zero maxActiveSagas', () => {
    expect(() => flockConfigSchema.parse({ ...VALID_FLOCK, maxActiveSagas: 0 })).toThrow();
  });

  it('accepts empty defaultRepos array', () => {
    const cfg = flockConfigSchema.parse({ ...VALID_FLOCK, defaultRepos: [] });
    expect(cfg.defaultRepos).toEqual([]);
  });
});

describe('dispatchDefaultsSchema', () => {
  it('parses valid dispatch defaults', () => {
    expect(dispatchDefaultsSchema.parse(VALID_DISPATCH_DEFAULTS)).toEqual(VALID_DISPATCH_DEFAULTS);
  });

  it('rejects confidenceThreshold > 100', () => {
    expect(() =>
      dispatchDefaultsSchema.parse({ ...VALID_DISPATCH_DEFAULTS, confidenceThreshold: 101 }),
    ).toThrow();
  });

  it('rejects confidenceThreshold < 0', () => {
    expect(() =>
      dispatchDefaultsSchema.parse({ ...VALID_DISPATCH_DEFAULTS, confidenceThreshold: -1 }),
    ).toThrow();
  });

  it('rejects non-positive maxConcurrentRaids', () => {
    expect(() =>
      dispatchDefaultsSchema.parse({ ...VALID_DISPATCH_DEFAULTS, maxConcurrentRaids: 0 }),
    ).toThrow();
  });

  it('rejects non-positive batchSize', () => {
    expect(() =>
      dispatchDefaultsSchema.parse({ ...VALID_DISPATCH_DEFAULTS, batchSize: 0 }),
    ).toThrow();
  });

  it('rejects negative retryDelaySeconds', () => {
    expect(() =>
      dispatchDefaultsSchema.parse({
        ...VALID_DISPATCH_DEFAULTS,
        retryPolicy: { maxRetries: 2, retryDelaySeconds: -1, escalateOnExhaustion: false },
      }),
    ).toThrow();
  });
});

describe('notificationSettingsSchema', () => {
  it('parses valid notification settings', () => {
    expect(notificationSettingsSchema.parse(VALID_NOTIFICATION_SETTINGS)).toEqual(
      VALID_NOTIFICATION_SETTINGS,
    );
  });

  it('accepts webhook channel with webhookUrl', () => {
    const cfg = notificationSettingsSchema.parse({
      ...VALID_NOTIFICATION_SETTINGS,
      channel: 'webhook',
      webhookUrl: 'https://example.com/hook',
    });
    expect(cfg.channel).toBe('webhook');
    expect(cfg.webhookUrl).toBe('https://example.com/hook');
  });

  it('rejects invalid channel', () => {
    expect(() =>
      notificationSettingsSchema.parse({ ...VALID_NOTIFICATION_SETTINGS, channel: 'slack' }),
    ).toThrow();
  });

  it('accepts none channel', () => {
    const cfg = notificationSettingsSchema.parse({
      ...VALID_NOTIFICATION_SETTINGS,
      channel: 'none',
    });
    expect(cfg.channel).toBe('none');
  });
});

describe('auditEntrySchema', () => {
  it('parses a valid audit entry', () => {
    expect(auditEntrySchema.parse(VALID_AUDIT_ENTRY)).toEqual(VALID_AUDIT_ENTRY);
  });

  it('accepts null payload', () => {
    const entry = auditEntrySchema.parse({ ...VALID_AUDIT_ENTRY, payload: null });
    expect(entry.payload).toBeNull();
  });

  it('rejects empty summary', () => {
    expect(() => auditEntrySchema.parse({ ...VALID_AUDIT_ENTRY, summary: '' })).toThrow();
  });

  it('rejects unknown kind', () => {
    expect(() => auditEntrySchema.parse({ ...VALID_AUDIT_ENTRY, kind: 'unknown.event' })).toThrow();
  });
});

describe('auditFilterSchema', () => {
  it('parses empty filter (all fields optional)', () => {
    const filter: AuditFilter = {};
    expect(auditFilterSchema.parse(filter)).toEqual({});
  });

  it('parses filter with kinds array', () => {
    const filter = auditFilterSchema.parse({
      kinds: ['raid.dispatched', 'raid.failed'],
      limit: 50,
    });
    expect(filter.kinds).toHaveLength(2);
    expect(filter.limit).toBe(50);
  });

  it('rejects zero limit', () => {
    expect(() => auditFilterSchema.parse({ limit: 0 })).toThrow();
  });
});
