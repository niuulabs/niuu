import { describe, it, expect } from 'vitest';
import { transitionJob, isTerminal } from './types';
import type { IngestJob } from './types';

const baseJob: IngestJob = {
  id: 'job-1',
  title: 'Test Job',
  sourceType: 'document',
  status: 'queued',
  instanceName: 'worker-1',
  pagesUpdated: [],
};

describe('transitionJob', () => {
  it('merges update into job', () => {
    const result = transitionJob(baseJob, { status: 'running' });
    expect(result.status).toBe('running');
    expect(result.id).toBe('job-1');
  });

  it('preserves fields not in update', () => {
    const result = transitionJob(baseJob, { currentActivity: 'Processing...' });
    expect(result.title).toBe('Test Job');
    expect(result.currentActivity).toBe('Processing...');
  });

  it('returns a new object (does not mutate original)', () => {
    const result = transitionJob(baseJob, { status: 'complete' });
    expect(result).not.toBe(baseJob);
    expect(baseJob.status).toBe('queued');
  });
});

describe('isTerminal', () => {
  it('returns true for complete', () => {
    expect(isTerminal('complete')).toBe(true);
  });

  it('returns true for failed', () => {
    expect(isTerminal('failed')).toBe(true);
  });

  it('returns false for queued', () => {
    expect(isTerminal('queued')).toBe(false);
  });

  it('returns false for running', () => {
    expect(isTerminal('running')).toBe(false);
  });
});
