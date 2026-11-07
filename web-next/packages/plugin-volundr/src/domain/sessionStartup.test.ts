import { describe, expect, it } from 'vitest';
import type { SessionStartup, StartupStage } from '../models/volundr.model';
import {
  currentStage,
  formatDuration,
  launchElapsedMs,
  stageDurationMs,
  stagesForStatus,
  startupHeadline,
} from './sessionStartup';

const T0 = Date.parse('2026-11-06T12:00:00Z');
const iso = (seconds: number) => new Date(T0 + seconds * 1000).toISOString();

function stage(overrides: Partial<StartupStage> & Pick<StartupStage, 'key'>): StartupStage {
  return {
    label: overrides.key,
    state: 'done',
    detail: null,
    startedAt: iso(0),
    finishedAt: iso(1),
    ...overrides,
  };
}

function startup(stages: StartupStage[]): SessionStartup {
  return { startedAt: iso(0), stages, progressError: null };
}

describe('formatDuration', () => {
  it('reads seconds, minutes and hours', () => {
    expect(formatDuration(42_400)).toBe('42s');
    expect(formatDuration(102_000)).toBe('1:42');
    expect(formatDuration(3_725_000)).toBe('1:02:05');
    expect(formatDuration(-5)).toBe('0s');
  });
});

describe('stageDurationMs', () => {
  it('measures a finished stage, an open one until now, and nothing for one to come', () => {
    expect(stageDurationMs(stage({ key: 'a', finishedAt: iso(38) }), T0 + 99_000)).toBe(38_000);
    expect(
      stageDurationMs(stage({ key: 'a', state: 'active', finishedAt: null }), T0 + 6_000),
    ).toBe(6_000);
    expect(
      stageDurationMs(stage({ key: 'a', state: 'pending', startedAt: null, finishedAt: null }), T0),
    ).toBeNull();
  });
});

describe('currentStage', () => {
  it('is the last stage active or failed', () => {
    const stages = [
      stage({ key: 'done' }),
      stage({ key: 'open', state: 'active', finishedAt: null }),
      stage({ key: 'next', state: 'pending', startedAt: null, finishedAt: null }),
    ];
    expect(currentStage(stages)?.key).toBe('open');
    expect(currentStage([stage({ key: 'done' })])).toBeNull();
  });
});

describe('stagesForStatus', () => {
  const open = startup([
    stage({ key: 'launch' }),
    stage({ key: 'schedule', state: 'active', finishedAt: null, detail: '0/3 nodes' }),
    stage({ key: 'containers', state: 'pending', startedAt: null, finishedAt: null }),
  ]);

  it('leaves a starting launch as Forge recorded it', () => {
    expect(stagesForStatus(open, 'starting', null)).toBe(open.stages);
  });

  it('reads an open stage of a failed session as where it failed, for its reason', () => {
    const stages = stagesForStatus(open, 'failed', 'Insufficient memory');
    expect(stages.map((s) => [s.key, s.state])).toEqual([
      ['launch', 'done'],
      ['schedule', 'failed'],
    ]);
    expect(stages[1]!.detail).toBe('Insufficient memory');
    expect(stagesForStatus(open, 'error', null)[1]!.detail).toBe('0/3 nodes');
  });

  it('keeps a launch Forge already closed', () => {
    const closed = startup([stage({ key: 'schedule', state: 'failed', detail: 'x' })]);
    expect(stagesForStatus(closed, 'failed', 'y')).toBe(closed.stages);
  });
});

describe('launchElapsedMs', () => {
  it('runs until now while a stage is open and stops at the last end otherwise', () => {
    const open = startup([stage({ key: 'a', state: 'active', finishedAt: null })]);
    expect(launchElapsedMs(open, open.stages, T0 + 90_000)).toBe(90_000);

    const closed = startup([stage({ key: 'a', finishedAt: iso(40) })]);
    expect(launchElapsedMs(closed, closed.stages, T0 + 900_000)).toBe(40_000);

    const empty = startup([]);
    expect(launchElapsedMs(empty, [], T0 + 5_000)).toBe(5_000);
    expect(launchElapsedMs({ ...empty, startedAt: 'nope' }, [], T0)).toBe(0);
  });
});

describe('startupHeadline', () => {
  it('names the stage and what the backend said', () => {
    expect(
      startupHeadline(stage({ key: 'c', label: 'Starting containers', detail: 'Pulling image' })),
    ).toBe('Starting containers · Pulling image');
    expect(startupHeadline(stage({ key: 's', label: 'Scheduling', state: 'failed' }))).toBe(
      'Scheduling failed',
    );
    expect(startupHeadline(null)).toBeNull();
  });
});
