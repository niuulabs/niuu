import { describe, expect, it } from 'vitest';
import type { WorkSummary, WorkTask } from './work';
import { groupTasksByStatus, groupWorkByCondition } from './workBoard';

function work(id: string, condition: WorkSummary['condition']): WorkSummary {
  return { id: `campaign:${id}`, title: id, condition } as WorkSummary;
}

function ticket(id: string, statusType: string): WorkTask {
  return { title: id, source: { issueId: id, statusType } } as WorkTask;
}

function columnIds<T>(columns: { id: string; items: T[] }[]) {
  return columns.map((column) => column.id);
}

describe('groupWorkByCondition', () => {
  it('keeps every condition on the board and failed work beside attention items', () => {
    const columns = groupWorkByCondition([
      work('a', 'needsAttention'),
      work('b', 'failed'),
      work('c', 'active'),
      work('d', 'unknown'),
      work('e', 'waiting'),
      work('f', 'completed'),
    ]);

    expect(columns.map((column) => [column.id, column.items.map((item) => item.title)])).toEqual([
      ['attention', ['a', 'b']],
      ['active', ['c', 'd']],
      ['waiting', ['e']],
      ['completed', ['f']],
    ]);
  });
});

describe('groupTasksByStatus', () => {
  it('shows the core columns even when they are empty', () => {
    expect(columnIds(groupTasksByStatus([]))).toEqual(['backlog', 'todo', 'started', 'completed']);
  });

  it('maps Linear and Jira status types into columns, ignoring case and padding', () => {
    const columns = groupTasksByStatus([
      ticket('triage', 'triage'),
      ticket('backlog', ' Backlog '),
      ticket('todo', 'unstarted'),
      ticket('doing', 'STARTED'),
      ticket('done', 'completed'),
    ]);

    expect(columns.map((column) => column.items.map((task) => task.title))).toEqual([
      ['triage', 'backlog'],
      ['todo'],
      ['doing'],
      ['done'],
    ]);
  });

  it('adds optional columns only when a ticket needs them', () => {
    const columns = groupTasksByStatus([ticket('stuck', 'blocked'), ticket('dropped', 'canceled')]);

    expect(columnIds(columns)).toEqual([
      'backlog',
      'todo',
      'blocked',
      'started',
      'completed',
      'canceled',
    ]);
  });

  it('never loses a ticket with a status type no column claims', () => {
    const columns = groupTasksByStatus([ticket('new', 'in_triage_v2'), ticket('blank', '')]);

    expect(columns.at(-1)?.id).toBe('other');
    expect(columns.at(-1)?.items.map((task) => task.title)).toEqual(['new', 'blank']);
  });
});
