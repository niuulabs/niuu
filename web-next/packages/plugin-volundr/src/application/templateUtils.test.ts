import { describe, it, expect } from 'vitest';
import { maskSecretRefs, buildCloneSpec, cloneName } from './templateUtils';
import type { Template } from '../domain/template';

const BASE_TEMPLATE: Template = {
  id: 'tpl-1',
  name: 'base',
  version: 3,
  spec: {
    image: 'ghcr.io/niuulabs/skuld',
    tag: 'v2',
    mounts: [],
    env: { KEY: 'val', SECRET: 'shh' },
    envSecretRefs: ['SECRET'],
    tools: ['bash'],
    resources: {
      cpuRequest: '1',
      cpuLimit: '2',
      memRequestMi: 512,
      memLimitMi: 1_024,
      gpuCount: 0,
    },
    ttlSec: 3_600,
    idleTimeoutSec: 600,
  },
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
};

describe('maskSecretRefs', () => {
  it('returns env unchanged when no secret refs', () => {
    const env = { A: 'hello', B: 'world' };
    expect(maskSecretRefs(env, [])).toEqual(env);
  });

  it('masks keys listed in envSecretRefs', () => {
    const env = { A: 'public', TOKEN: 'secret-value' };
    const masked = maskSecretRefs(env, ['TOKEN']);
    expect(masked['TOKEN']).toBe('***');
    expect(masked['A']).toBe('public');
  });

  it('masks multiple secret refs', () => {
    const env = { A: '1', B: '2', C: '3' };
    const masked = maskSecretRefs(env, ['A', 'C']);
    expect(masked['A']).toBe('***');
    expect(masked['B']).toBe('2');
    expect(masked['C']).toBe('***');
  });

  it('returns empty object for empty env', () => {
    expect(maskSecretRefs({}, ['KEY'])).toEqual({});
  });

  it('ignores secretRef keys not present in env', () => {
    const env = { A: 'public' };
    const masked = maskSecretRefs(env, ['MISSING']);
    expect(masked).toEqual({ A: 'public' });
  });

  it('masks the secret key from the base template', () => {
    const masked = maskSecretRefs(BASE_TEMPLATE.spec.env, BASE_TEMPLATE.spec.envSecretRefs);
    expect(masked['SECRET']).toBe('***');
    expect(masked['KEY']).toBe('val');
  });
});

describe('buildCloneSpec', () => {
  it('returns a copy equal to the source spec', () => {
    const spec = buildCloneSpec(BASE_TEMPLATE);
    expect(spec).toEqual(BASE_TEMPLATE.spec);
  });

  it('returns a different object reference (shallow copy)', () => {
    const spec = buildCloneSpec(BASE_TEMPLATE);
    expect(spec).not.toBe(BASE_TEMPLATE.spec);
  });

  it('preserves all spec fields', () => {
    const spec = buildCloneSpec(BASE_TEMPLATE);
    expect(spec.image).toBe('ghcr.io/niuulabs/skuld');
    expect(spec.tag).toBe('v2');
    expect(spec.resources.cpuRequest).toBe('1');
    expect(spec.ttlSec).toBe(3_600);
  });
});

describe('cloneName', () => {
  it('prefixes "Clone of " to the original name', () => {
    expect(cloneName('default')).toBe('Clone of default');
  });

  it('handles names with spaces', () => {
    expect(cloneName('my template')).toBe('Clone of my template');
  });

  it('handles empty string', () => {
    expect(cloneName('')).toBe('Clone of ');
  });
});
