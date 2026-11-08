import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { ProgressLane, progressSummary, stageStatus } from './ProgressLane';
import type { OutcomeTone, Worker, WorkerOutcome, WorkerStage } from '../../workers';

const outcome = (
  peerId: string,
  verdict: string,
  tone: OutcomeTone,
  minute = 0,
): WorkerOutcome => ({
  peerId,
  persona: peerId,
  eventType: `${peerId}.done`,
  verdict,
  fields: {},
  at: new Date(Date.UTC(2026, 10, 5, 5, minute)),
  tone,
});

const stage = (
  name: string,
  options: {
    status?: Worker['status'];
    outcomes?: WorkerOutcome[];
    current?: boolean;
  } = {},
): WorkerStage => {
  const outcomes = options.outcomes ?? [];
  return {
    worker: {
      peerId: name,
      name,
      persona: name,
      color: '#445566',
      status: options.status ?? 'idle',
      consumes: [],
      emits: [],
      outcomes,
      latest: outcomes.at(-1),
    },
    loops: Math.max(0, outcomes.length - 1),
    current: options.current ?? false,
  };
};

const flow: WorkerStage[] = [
  stage('spec', { outcomes: [outcome('spec', 'framed', 'pass')] }),
  stage('coder', {
    status: 'working',
    current: true,
    outcomes: [outcome('coder', 'pass', 'pass'), outcome('coder', 'pass', 'pass', 1)],
  }),
  stage('reviewer', { outcomes: [outcome('reviewer', 'needs_changes', 'changes', 2)] }),
  stage('security', { outcomes: [outcome('security', 'fail', 'blocked')] }),
  stage('publisher'),
];

describe('ProgressLane', () => {
  it('shows one stage per worker, in the order given', () => {
    render(<ProgressLane stages={flow} onSelect={vi.fn()} />);
    const stages = screen.getAllByTestId('progress-stage');
    expect(stages.map((el) => el.querySelector('.niuu-chat-stage-name')!.textContent)).toEqual([
      'spec',
      'coder',
      'reviewer',
      'security',
      'publisher',
    ]);
  });

  it('shows each latest verdict, toned by what it concluded', () => {
    render(<ProgressLane stages={flow} onSelect={vi.fn()} />);
    const pill = (name: string) =>
      within(screen.getByRole('button', { name: new RegExp(`^${name}`) })).getByText(
        /Framed|Working|Changes requested|Fail|Waiting/,
      );
    expect(pill('spec')).toHaveAttribute('data-tone', 'pass');
    expect(pill('reviewer')).toHaveTextContent('Changes requested');
    expect(pill('reviewer')).toHaveAttribute('data-tone', 'changes');
    expect(pill('security')).toHaveTextContent('Failed');
    expect(pill('security')).toHaveAttribute('data-tone', 'blocked');
  });

  it('shows Waiting for a worker that has not reported, and Working for one running now', () => {
    render(<ProgressLane stages={flow} onSelect={vi.fn()} />);
    expect(screen.getByText('Waiting')).toHaveAttribute('data-tone', 'waiting');
    expect(screen.getByText('Working')).toHaveAttribute('data-tone', 'working');
  });

  it('shows how many times a worker ran again, only when it did', () => {
    render(<ProgressLane stages={flow} onSelect={vi.fn()} />);
    expect(screen.getAllByText(/↺/)).toHaveLength(1);
    expect(
      within(screen.getByRole('button', { name: /^coder/ })).getByText('↺ 1'),
    ).toBeInTheDocument();
  });

  it('highlights the current stage', () => {
    render(<ProgressLane stages={flow} onSelect={vi.fn()} />);
    expect(screen.getByRole('button', { name: /^coder/ })).toHaveClass('is-current');
    expect(screen.getByRole('button', { name: /^coder/ })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByRole('button', { name: /^spec/ })).not.toHaveClass('is-current');
  });

  it('opens a worker from its stage', () => {
    const onSelect = vi.fn();
    render(<ProgressLane stages={flow} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: /^reviewer/ }));
    expect(onSelect).toHaveBeenCalledWith('reviewer');
  });

  it('is hidden with fewer than two workers', () => {
    const { container, rerender } = render(<ProgressLane stages={[]} onSelect={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ProgressLane stages={[flow[0]!]} onSelect={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ProgressLane stages={flow.slice(0, 2)} onSelect={vi.fn()} />);
    expect(screen.getByTestId('progress-lane')).toBeInTheDocument();
  });

  it('heads the lane with Progress and the summary', () => {
    render(<ProgressLane stages={flow} onSelect={vi.fn()} />);
    expect(screen.getByText('Progress')).toBeInTheDocument();
    expect(screen.getByTestId('progress-summary')).toHaveTextContent(
      'reviewer sent the work back once · coder is on it',
    );
  });

  it('leaves the summary out when the stages say nothing yet', () => {
    render(<ProgressLane stages={[stage('a'), stage('b')]} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('progress-summary')).toBeNull();
  });
});

describe('stageStatus', () => {
  it('reads an unknown verdict as information, in words', () => {
    expect(stageStatus(stage('a', { outcomes: [outcome('a', 'reported', 'info')] }))).toEqual({
      label: 'Reported',
      tone: 'info',
    });
  });
});

describe('progressSummary', () => {
  it('counts how often work was sent back', () => {
    const twice = stage('reviewer', {
      outcomes: [
        outcome('reviewer', 'needs_changes', 'changes'),
        outcome('reviewer', 'needs_changes', 'changes', 1),
      ],
    });
    const thrice = stage('auditor', {
      outcomes: [1, 2, 3].map((minute) => outcome('auditor', 'needs_changes', 'changes', minute)),
    });
    expect(progressSummary([twice, stage('x')])).toBe('reviewer sent the work back twice');
    expect(progressSummary([thrice, stage('x')])).toBe('auditor sent the work back 3 times');
  });

  it('names several workers that are on it', () => {
    const a = stage('a', { status: 'working', current: true });
    const b = stage('b', { status: 'working', current: true });
    const c = stage('c', { status: 'working', current: true });
    expect(progressSummary([a, b])).toBe('a and b are on it');
    expect(progressSummary([a, b, c])).toBe('a, b and c are on it');
  });

  it('says a stopped worker is blocked', () => {
    const blocked = stage('security', {
      current: true,
      outcomes: [outcome('security', 'fail', 'blocked')],
    });
    expect(progressSummary([blocked, stage('x')])).toBe('security is blocked');
  });

  it('says so when every worker has passed', () => {
    const done = [
      stage('a', { outcomes: [outcome('a', 'pass', 'pass')] }),
      stage('b', { outcomes: [outcome('b', 'pass', 'pass')] }),
    ];
    expect(progressSummary(done)).toBe('every worker has passed');
  });

  it('has nothing to say before anyone has reported', () => {
    expect(progressSummary([stage('a'), stage('b')])).toBeNull();
  });
});
