import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfidenceDriftCard } from './ConfidenceDriftCard';

const SAGA_ID = '00000000-0000-0000-0000-000000000001';

describe('ConfidenceDriftCard', () => {
  it('renders the "Confidence drift" heading', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByText('Confidence drift')).toBeInTheDocument();
  });

  it('renders the section with accessible label', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByRole('region', { name: /confidence drift/i })).toBeInTheDocument();
  });

  it('renders the description paragraph', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByText(/how this saga's overall confidence has moved/i)).toBeInTheDocument();
  });

  it('renders the event count in the header', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByText(/10 events/i)).toBeInTheDocument();
  });

  it('renders the "now" current confidence as a decimal fraction', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByText('0.82')).toBeInTheDocument();
  });

  it('renders 0.50 when confidence is 50', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={50} />);
    expect(screen.getByText('0.50')).toBeInTheDocument();
  });

  it('renders 1.00 when confidence is 100', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={100} />);
    expect(screen.getByText('1.00')).toBeInTheDocument();
  });

  it('renders 0.00 when confidence is 0', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={0} />);
    expect(screen.getByText('0.00')).toBeInTheDocument();
  });

  it('renders the scope_adherence metric', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByText('0.94')).toBeInTheDocument();
  });

  it('renders the tests coverage metric', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByText('98%')).toBeInTheDocument();
  });

  it('renders the metrics container with accessible label', () => {
    render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(screen.getByLabelText(/confidence metrics/i)).toBeInTheDocument();
  });

  it('renders consistently for the same saga ID (deterministic)', () => {
    const { container: c1 } = render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    const { container: c2 } = render(<ConfidenceDriftCard sagaId={SAGA_ID} confidence={82} />);
    expect(c1.innerHTML).toEqual(c2.innerHTML);
  });

  it('renders different start values for different saga IDs', () => {
    const { unmount } = render(<ConfidenceDriftCard sagaId="saga-alpha" confidence={80} />);
    const firstStart = screen.getByText(/start/i).textContent;
    unmount();

    render(<ConfidenceDriftCard sagaId="saga-beta" confidence={80} />);
    const secondStart = screen.getByText(/start/i).textContent;

    // Two different saga IDs should (very likely) produce different start values
    // due to the hash-based seed — this is a sanity check for determinism.
    expect(firstStart).not.toBeUndefined();
    expect(secondStart).not.toBeUndefined();
    expect(firstStart).not.toEqual(secondStart);
  });
});
