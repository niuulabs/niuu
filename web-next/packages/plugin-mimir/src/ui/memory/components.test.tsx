import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { renderWithMimir } from '../../testing/renderWithMimir';
import type { FactEvidence, EvidenceTrend } from '../../domain/evidence';
import { AskBox } from './AskBox';
import { NeighbourhoodGraph } from './NeighbourhoodGraph';
import { ProofPill } from './ProofPill';
import { ReviseFact } from './ReviseFact';

function evidence(overrides: Partial<FactEvidence> = {}): FactEvidence {
  return {
    fact: 'Raw SQL with asyncpg — no ORM',
    proofCount: 4,
    trend: 'stable',
    latestSupport: '2026-03-20',
    supportingDates: ['2026-03-20'],
    sourceProofCount: 2,
    ...overrides,
  };
}

describe('ProofPill', () => {
  it.each<EvidenceTrend>(['new', 'strengthening', 'stable', 'weakening', 'stale'])(
    'renders the %s trend',
    (trend) => {
      render(<ProofPill evidence={evidence({ trend })} />);
      const pill = screen.getByTestId('proof-pill');
      expect(pill.dataset['trend']).toBe(trend);
      expect(pill).toHaveTextContent(trend);
    },
  );

  it('counts one proof in the singular', () => {
    render(<ProofPill evidence={evidence({ proofCount: 1 })} />);
    expect(screen.getByTestId('proof-pill')).toHaveTextContent('1 proof ·');
  });

  it('says when nothing supports the fact', () => {
    render(<ProofPill evidence={evidence({ proofCount: 0, trend: 'new', latestSupport: null })} />);
    expect(screen.getByTestId('proof-pill').title).toContain('no proof on record');
  });

  it('names the newest proof on hover', () => {
    render(<ProofPill evidence={evidence()} />);
    expect(screen.getByTestId('proof-pill').title).toContain('newest proof 2026-03-20');
  });
});

describe('AskBox', () => {
  it('replaces the draft when the question in the URL changes', () => {
    const { rerender } = render(<AskBox value="first" onAsk={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Ask memory'), { target: { value: 'typed over it' } });
    rerender(<AskBox value="second" onAsk={vi.fn()} />);
    expect(screen.getByLabelText('Ask memory')).toHaveValue('second');
  });

  it('keeps the draft while the question stays the same', () => {
    const { rerender } = render(<AskBox value="first" onAsk={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Ask memory'), { target: { value: 'still typing' } });
    rerender(<AskBox value="first" onAsk={vi.fn()} />);
    expect(screen.getByLabelText('Ask memory')).toHaveValue('still typing');
  });

  it('trims the question it hands on', () => {
    const onAsk = vi.fn();
    render(<AskBox value="" onAsk={onAsk} />);
    fireEvent.change(screen.getByLabelText('Ask memory'), { target: { value: '  spaced  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask memory' }));
    expect(onAsk).toHaveBeenCalledWith('spaced');
  });

  it('takes a custom placeholder', () => {
    render(<AskBox value="" placeholder="Ask about this realm…" onAsk={vi.fn()} />);
    expect(screen.getByPlaceholderText('Ask about this realm…')).toBeInTheDocument();
  });
});

describe('NeighbourhoodGraph', () => {
  const related = [
    { path: '/api/overview', hop: 1, rel: 'documents', direction: 'out' as const },
    { path: '/infra/k8s', hop: 1, rel: null, direction: 'in' as const },
  ];

  it('labels a typed edge and leaves an untyped one bare', () => {
    render(<NeighbourhoodGraph title="Architecture" related={related} onOpen={vi.fn()} />);
    expect(screen.getByText('documents')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'open k8s' })).toBeInTheDocument();
  });

  it('opens a neighbour from the keyboard', () => {
    const onOpen = vi.fn();
    render(<NeighbourhoodGraph title="Architecture" related={related} onOpen={onOpen} />);
    fireEvent.keyDown(screen.getByRole('button', { name: 'open overview' }), { key: 'Enter' });
    expect(onOpen).toHaveBeenCalledWith('/api/overview');
  });

  it('ignores other keys', () => {
    const onOpen = vi.fn();
    render(<NeighbourhoodGraph title="Architecture" related={related} onOpen={onOpen} />);
    fireEvent.keyDown(screen.getByRole('button', { name: 'open overview' }), { key: 'Escape' });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('counts the neighbours it could not draw', () => {
    const many = Array.from({ length: 10 }, (_, index) => ({
      path: `/p${index}`,
      hop: 1,
      rel: null,
      direction: 'out' as const,
    }));
    render(<NeighbourhoodGraph title="Architecture" related={many} onOpen={vi.fn()} />);
    expect(screen.getByText('+2 more')).toBeInTheDocument();
  });
});

describe('ReviseFact', () => {
  const FACT = 'Hexagonal architecture with ports and adapters';

  it('cancelling puts the original wording back', async () => {
    renderWithMimir(<ReviseFact path="/arch/overview" fact={FACT} index={0} />);
    fireEvent.click(screen.getByTestId('memory-revise-0'));
    fireEvent.change(screen.getByLabelText(`Revise: ${FACT}`), { target: { value: 'scratch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.click(screen.getByTestId('memory-revise-0'));
    expect(screen.getByLabelText(`Revise: ${FACT}`)).toHaveValue(FACT);
  });

  it('will not save wording that did not change', () => {
    renderWithMimir(<ReviseFact path="/arch/overview" fact={FACT} index={0} />);
    fireEvent.click(screen.getByTestId('memory-revise-0'));
    expect(screen.getByRole('button', { name: 'Save revision' })).toBeDisabled();
  });

  it('writes the revision and closes', async () => {
    renderWithMimir(<ReviseFact path="/arch/overview" fact={FACT} index={2} />);
    fireEvent.click(screen.getByTestId('memory-revise-2'));
    fireEvent.change(screen.getByLabelText(`Revise: ${FACT}`), {
      target: { value: 'Ports and adapters, all the way down' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save revision' }));
    await waitFor(() => expect(screen.getByTestId('memory-revise-2')).toBeInTheDocument());
  });
});
