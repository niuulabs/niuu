import { describe, expect, it } from 'vitest';
import {
  EPHEMERAL_LOCAL_MOUNT,
  parseWorkflowRegistryMount,
  serializeWorkflowRegistryMount,
  type WorkflowRegistryMount,
} from './mimirRegistry';

describe('mimirRegistry', () => {
  it('serializes and parses a full registry mount payload', () => {
    const mount: WorkflowRegistryMount = {
      id: 'shared-docs',
      name: 'Shared Docs',
      kind: 'remote',
      lifecycle: 'registered',
      role: 'domain',
      url: 'https://mimir.example.com',
      path: '/wiki/shared',
      categories: ['docs', 'prod'],
      authRef: 'mimir-prod',
      defaultReadPriority: 7,
      enabled: false,
      healthStatus: 'degraded',
      healthMessage: 'latency spike',
      desc: 'Domain-wide reference memory',
    };

    expect(parseWorkflowRegistryMount(serializeWorkflowRegistryMount(mount))).toEqual(mount);
  });

  it('returns null for empty, invalid, or incomplete payloads', () => {
    expect(parseWorkflowRegistryMount('')).toBeNull();
    expect(parseWorkflowRegistryMount('{not json')).toBeNull();
    expect(parseWorkflowRegistryMount(JSON.stringify({ id: 'only-id' }))).toBeNull();
    expect(parseWorkflowRegistryMount(JSON.stringify({ name: 'only-name' }))).toBeNull();
  });

  it('fills safe defaults when optional fields are omitted or malformed', () => {
    expect(
      parseWorkflowRegistryMount(
        JSON.stringify({
          id: 'local-workspace',
          name: 'Local Workspace',
          kind: 'weird',
          lifecycle: 'later',
          role: 'unknown',
          url: 123,
          path: false,
          categories: ['notes', '', null, 'scratch'],
          authRef: 42,
          defaultReadPriority: 'fast',
          healthStatus: 'mystery',
          healthMessage: 99,
          desc: {},
        }),
      ),
    ).toEqual({
      id: 'local-workspace',
      name: 'Local Workspace',
      kind: 'local',
      lifecycle: 'registered',
      role: 'shared',
      url: '',
      path: '',
      categories: ['notes', 'scratch'],
      authRef: null,
      defaultReadPriority: 10,
      enabled: true,
      healthStatus: 'unknown',
      healthMessage: '',
      desc: '',
    });
  });

  it('preserves supported ephemeral and local variants', () => {
    expect(parseWorkflowRegistryMount(JSON.stringify(EPHEMERAL_LOCAL_MOUNT))).toEqual(
      EPHEMERAL_LOCAL_MOUNT,
    );

    expect(
      parseWorkflowRegistryMount(
        JSON.stringify({
          ...EPHEMERAL_LOCAL_MOUNT,
          role: 'local',
          healthStatus: 'healthy',
        }),
      ),
    ).toMatchObject({
      lifecycle: 'ephemeral',
      role: 'local',
      healthStatus: 'healthy',
    });
  });
});
