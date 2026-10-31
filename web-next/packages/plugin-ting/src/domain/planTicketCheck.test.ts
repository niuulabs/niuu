import { describe, expect, it } from 'vitest';
import type { PhaseSpec, RunSpec } from '../ports';
import { buildRevisionRequest, checkPlanTickets, isSameRepository } from './planTicketCheck';

function run(name: string, overrides: Partial<RunSpec> = {}): RunSpec {
  return {
    name,
    description: 'Explains the work',
    acceptanceCriteria: ['It works'],
    declaredFiles: [],
    estimateHours: 1,
    confidence: 80,
    ...overrides,
  };
}

function phases(...runs: RunSpec[]): PhaseSpec[] {
  return [{ name: 'Build', runs }];
}

describe('checkPlanTickets', () => {
  it('stays quiet about a clear, unique ticket', () => {
    expect(checkPlanTickets(phases(run('Add retry budget')), [])).toEqual([]);
  });

  it('flags tickets with nothing for an agent to work from', () => {
    const flags = checkPlanTickets(
      phases(run('Fix auth', { description: '  ', acceptanceCriteria: [] })),
      [],
    );

    expect(flags.map((flag) => [flag.kind, flag.message])).toEqual([
      ['vague', 'No description'],
      ['vague', 'No acceptance criteria'],
    ]);
  });

  it('flags the later of two near-identical tickets in a plan', () => {
    const flags = checkPlanTickets(
      phases(run('Add the retry budget'), run('Add a retry budget'), run('Trace queue wait time')),
      [],
    );

    expect(flags).toHaveLength(1);
    expect(flags[0]).toMatchObject({
      kind: 'duplicate',
      phaseIndex: 0,
      runIndex: 1,
      message: 'Overlaps "Add the retry budget" in this plan',
    });
  });

  it('flags a ticket that repeats existing tracker work, across phases', () => {
    const flags = checkPlanTickets(
      [
        { name: 'Build', runs: [run('Unrelated work')] },
        { name: 'Verify', runs: [run('Implement retry budget')] },
      ],
      [{ identifier: 'PLT-207', title: 'Implement the retry budget' }],
    );

    expect(flags).toEqual([
      expect.objectContaining({
        phaseIndex: 1,
        runIndex: 0,
        message: 'Looks like PLT-207: Implement the retry budget',
      }),
    ]);
  });

  it('does not treat titles made only of stop words as repeats', () => {
    expect(
      checkPlanTickets(phases(run('of the')), [{ identifier: 'A-1', title: 'to the' }]),
    ).toEqual([]);
  });
});

describe('isSameRepository', () => {
  it.each([
    ['https://github.com/niuulabs/volundr.git', 'niuulabs/volundr'],
    ['git@github.com:niuulabs/volundr.git', 'niuulabs/volundr'],
    ['https://github.com/NiuuLabs/Volundr/', 'niuulabs/volundr'],
  ])('treats %s and %s as one repository', (left, right) => {
    expect(isSameRepository(left, right)).toBe(true);
  });

  it('keeps same-named repositories in different organizations apart', () => {
    expect(isSameRepository('niuulabs/volundr', 'other/volundr')).toBe(false);
  });
});

describe('buildRevisionRequest', () => {
  it('lists every flag so the planning workflow can act on it', () => {
    const [flag] = checkPlanTickets(phases(run('Fix auth', { description: '' })), []);

    expect(buildRevisionRequest([flag!])).toBe(
      'Fix these tickets, and merge or drop any that repeat existing work:\n' +
        '- Fix auth: No description',
    );
  });
});
