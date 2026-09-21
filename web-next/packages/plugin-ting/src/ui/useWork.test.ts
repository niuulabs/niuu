import { describe, expect, it } from 'vitest';
import type { WorkCollection, WorkSummary } from '../domain/work';
import { mergeWorkPages } from './useWork';

const execution = (id: string, title = id): WorkSummary =>
  ({
    id: `execution:${id}`,
    kind: 'workflowExecution',
    title,
  }) as WorkSummary;

const page = (
  executions: WorkSummary[],
  executionNextCursor: string | null,
  source = 'page',
): WorkCollection => ({
  projects: [execution(`${source}-project`)],
  campaigns: [execution(`${source}-campaign`)],
  executions,
  executionNextCursor,
  coverage: [
    { source, connectionId: null, status: 'complete', error: null },
  ],
});

describe('mergeWorkPages', () => {
  it('returns null without a fetched page', () => {
    expect(mergeWorkPages(undefined)).toBeNull();
    expect(mergeWorkPages([])).toBeNull();
  });

  it('keeps unpaged sections from the first response and paging state from the latest', () => {
    const first = page([execution('one'), execution('shared', 'older')], 'cursor-2', 'first');
    const second = page([execution('shared', 'newer'), execution('two')], null, 'second');

    const merged = mergeWorkPages([first, second]);
    expect(merged?.projects).toBe(first.projects);
    expect(merged?.campaigns).toBe(first.campaigns);
    expect(merged?.coverage).toBe(second.coverage);
    expect(merged?.executionNextCursor).toBeNull();
    expect(merged?.executions.map((item) => [item.id, item.title])).toEqual([
      ['execution:one', 'one'],
      ['execution:shared', 'newer'],
      ['execution:two', 'two'],
    ]);
  });
});
