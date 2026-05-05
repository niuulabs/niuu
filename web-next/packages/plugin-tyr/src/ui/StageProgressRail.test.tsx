import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StageProgressRail } from './StageProgressRail';
import type { Phase } from '../domain/saga';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makePhase(id: string, number: number, name: string, status: Phase['status']): Phase {
  return {
    id,
    sagaId: 'saga-001',
    trackerId: `NIU-M${number}`,
    number,
    name,
    status,
    confidence: 80,
    raids: [],
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('StageProgressRail', () => {
  it('renders the "Stage progress" heading', () => {
    render(<StageProgressRail phases={[]} />);
    expect(screen.getByText('Stage progress')).toBeInTheDocument();
  });

  it('renders the section with accessible label', () => {
    render(<StageProgressRail phases={[]} />);
    expect(screen.getByRole('region', { name: /stage progress/i })).toBeInTheDocument();
  });

  it('shows "No stages defined." when phases is empty', () => {
    render(<StageProgressRail phases={[]} />);
    expect(screen.getByText('No stages defined.')).toBeInTheDocument();
  });

  it('shows 0 / 0 count when phases is empty', () => {
    render(<StageProgressRail phases={[]} />);
    expect(screen.getByText('0 / 0')).toBeInTheDocument();
  });

  it('renders a stage dot for each phase', () => {
    const phases = [
      makePhase('p1', 1, 'Foundation', 'complete'),
      makePhase('p2', 2, 'PAT Support', 'active'),
      makePhase('p3', 3, 'Security', 'pending'),
    ];
    render(<StageProgressRail phases={phases} />);
    const dots = screen.getAllByRole('listitem');
    expect(dots).toHaveLength(3);
  });

  it('shows correct completed count in header', () => {
    const phases = [
      makePhase('p1', 1, 'Foundation', 'complete'),
      makePhase('p2', 2, 'PAT Support', 'active'),
      makePhase('p3', 3, 'Security', 'pending'),
    ];
    render(<StageProgressRail phases={phases} />);
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
  });

  it('renders aria-label for each dot with correct status', () => {
    const phases = [
      makePhase('p1', 1, 'Foundation', 'complete'),
      makePhase('p2', 2, 'PAT Support', 'active'),
      makePhase('p3', 3, 'Security', 'gated'),
      makePhase('p4', 4, 'Final', 'pending'),
    ];
    render(<StageProgressRail phases={phases} />);
    expect(screen.getByLabelText('Stage 1: Foundation, complete')).toBeInTheDocument();
    expect(screen.getByLabelText('Stage 2: PAT Support, active')).toBeInTheDocument();
    expect(screen.getByLabelText('Stage 3: Security, gated')).toBeInTheDocument();
    expect(screen.getByLabelText('Stage 4: Final, pending')).toBeInTheDocument();
  });

  it('marks stage dots with data-status attribute', () => {
    const phases = [makePhase('p1', 1, 'Foundation', 'complete')];
    render(<StageProgressRail phases={phases} />);
    expect(screen.getByLabelText('Stage 1: Foundation, complete')).toHaveAttribute(
      'data-status',
      'complete',
    );
  });

  it('renders stage labels below the rail', () => {
    const phases = [
      makePhase('p1', 1, 'Foundation', 'complete'),
      makePhase('p2', 2, 'PAT Support', 'active'),
    ];
    render(<StageProgressRail phases={phases} />);
    expect(screen.getByText('Foundation')).toBeInTheDocument();
    expect(screen.getByText('PAT Support')).toBeInTheDocument();
  });

  it('renders the dots container with accessible list label', () => {
    const phases = [makePhase('p1', 1, 'Foundation', 'complete')];
    render(<StageProgressRail phases={phases} />);
    expect(screen.getByRole('list', { name: /stage dots/i })).toBeInTheDocument();
  });

  it('renders 2 complete out of 3 in header', () => {
    const phases = [
      makePhase('p1', 1, 'Foundation', 'complete'),
      makePhase('p2', 2, 'PAT', 'complete'),
      makePhase('p3', 3, 'Security', 'pending'),
    ];
    render(<StageProgressRail phases={phases} />);
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
  });

  it('renders a single phase correctly', () => {
    const phases = [makePhase('p1', 1, 'Only Phase', 'active')];
    render(<StageProgressRail phases={phases} />);
    expect(screen.getByText('Only Phase')).toBeInTheDocument();
    expect(screen.getByText('0 / 1')).toBeInTheDocument();
  });
});
