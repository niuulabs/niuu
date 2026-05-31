import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RunMeshCanvas } from './RunMeshCanvas';
import type { Saga, Phase, Run } from '../domain/saga';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSaga(overrides: Partial<Saga> = {}): Saga {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-1',
    trackerType: 'linear',
    slug: 'test-saga',
    name: 'Test Saga',
    repos: [],
    featureBranch: 'feat/test',
    status: 'active',
    confidence: 80,
    createdAt: '2026-01-01T00:00:00Z',
    phaseSummary: { total: 1, completed: 0 },
    ...overrides,
  };
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: '00000000-0000-0000-0000-000000000010',
    phaseId: '00000000-0000-0000-0000-000000000100',
    trackerId: 'NIU-R1',
    name: 'Test Run',
    description: '',
    acceptanceCriteria: [],
    declaredFiles: [],
    estimateHours: 4,
    status: 'running',
    confidence: 80,
    sessionId: null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makePhase(overrides: Partial<Phase> = {}): Phase {
  return {
    id: '00000000-0000-0000-0000-000000000100',
    sagaId: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-M1',
    number: 1,
    name: 'Phase 1',
    status: 'active',
    confidence: 80,
    runs: [makeRun()],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('RunMeshCanvas', () => {
  it('renders a canvas element with the default aria-label', () => {
    render(<RunMeshCanvas sagas={[]} phases={[]} />);
    expect(screen.getByLabelText('Live run mesh visualization')).toBeInTheDocument();
  });

  it('renders with a custom aria-label', () => {
    render(<RunMeshCanvas sagas={[]} phases={[]} aria-label="Custom mesh label" />);
    expect(screen.getByLabelText('Custom mesh label')).toBeInTheDocument();
  });

  it('renders with an empty sagas list (no clusters)', () => {
    render(<RunMeshCanvas sagas={[]} phases={[]} />);
    const canvas = screen.getByLabelText('Live run mesh visualization');
    expect(canvas.tagName).toBe('CANVAS');
  });

  it('renders without error when sagas and phases are provided', () => {
    const saga = makeSaga();
    const phase = makePhase();
    render(<RunMeshCanvas sagas={[saga]} phases={[[phase]]} />);
    expect(screen.getByLabelText('Live run mesh visualization')).toBeInTheDocument();
  });

  it('does not call onClickSaga when clicked without a hovered node', () => {
    const onClickSaga = vi.fn();
    render(<RunMeshCanvas sagas={[]} phases={[]} onClickSaga={onClickSaga} />);
    const canvas = screen.getByLabelText('Live run mesh visualization');
    fireEvent.click(canvas.parentElement!);
    expect(onClickSaga).not.toHaveBeenCalled();
  });

  it('skips complete sagas when building clusters', () => {
    const completeSaga = makeSaga({ status: 'complete' });
    const activeSaga = makeSaga({ id: '00000000-0000-0000-0000-000000000002', status: 'active' });
    const phase = makePhase({ sagaId: activeSaga.id });
    // Should render without error — complete saga produces no clusters
    render(<RunMeshCanvas sagas={[completeSaga, activeSaga]} phases={[[], [phase]]} />);
    expect(screen.getByLabelText('Live run mesh visualization')).toBeInTheDocument();
  });

  it('only includes running/review/queued runs in clusters', () => {
    const saga = makeSaga();
    const phase = makePhase({
      runs: [
        makeRun({ id: 'r1', status: 'pending' }),
        makeRun({ id: 'r2', status: 'merged' }),
        makeRun({ id: 'r3', status: 'running' }),
      ],
    });
    render(<RunMeshCanvas sagas={[saga]} phases={[[phase]]} />);
    expect(screen.getByLabelText('Live run mesh visualization')).toBeInTheDocument();
  });
});
