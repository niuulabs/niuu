import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfidenceDriftCard } from './ConfidenceDriftCard';
import type { Phase } from '../domain/saga';

const PHASES: Phase[] = [
  {
    id: 'phase-1',
    sagaId: 'saga-1',
    trackerId: 'NIU-M1',
    number: 1,
    name: 'Build',
    status: 'active',
    confidence: 72,
    runs: [
      {
        id: 'run-1',
        phaseId: 'phase-1',
        trackerId: 'NIU-1',
        name: 'Implement feature',
        description: '',
        acceptanceCriteria: [],
        declaredFiles: [],
        estimateHours: 4,
        status: 'review',
        confidence: 78,
        sessionId: null,
        reviewerSessionId: null,
        reviewRound: 1,
        branch: null,
        chronicleSummary: null,
        retryCount: 1,
        createdAt: '2026-05-14T12:00:00Z',
        updatedAt: '2026-05-14T12:20:00Z',
      },
      {
        id: 'run-2',
        phaseId: 'phase-1',
        trackerId: 'NIU-2',
        name: 'Security pass',
        description: '',
        acceptanceCriteria: [],
        declaredFiles: [],
        estimateHours: 2,
        status: 'escalated',
        confidence: 42,
        sessionId: null,
        reviewerSessionId: null,
        reviewRound: 0,
        branch: null,
        chronicleSummary: null,
        retryCount: 0,
        createdAt: '2026-05-14T12:05:00Z',
        updatedAt: '2026-05-14T12:25:00Z',
      },
    ],
  },
];

describe('ConfidenceDriftCard', () => {
  it('renders confidence signals instead of a synthetic drift chart', () => {
    render(<ConfidenceDriftCard confidence={82} phases={PHASES} />);
    expect(screen.getByText('Confidence signals')).toBeInTheDocument();
    expect(screen.queryByText('Confidence drift')).not.toBeInTheDocument();
  });

  it('renders the current saga confidence and average run confidence', () => {
    render(<ConfidenceDriftCard confidence={82} phases={PHASES} />);
    expect(screen.getByText('82%')).toBeInTheDocument();
    expect(screen.getByText('60%')).toBeInTheDocument();
  });

  it('renders live metrics from the provided phases', () => {
    render(<ConfidenceDriftCard confidence={82} phases={PHASES} />);
    expect(screen.getByLabelText(/confidence metrics/i)).toHaveTextContent('runs');
    expect(screen.getByLabelText(/confidence metrics/i)).toHaveTextContent('2');
    expect(screen.getByLabelText(/confidence metrics/i)).toHaveTextContent('low confidence');
    expect(screen.getByLabelText(/confidence metrics/i)).toHaveTextContent('1');
    expect(screen.getByLabelText(/confidence metrics/i)).toHaveTextContent('retries');
    expect(screen.getByLabelText(/confidence metrics/i)).toHaveTextContent('1');
  });

  it('handles empty phase lists without inventing history', () => {
    render(<ConfidenceDriftCard confidence={50} phases={[]} />);
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getAllByText('—')).toHaveLength(2);
  });
});
