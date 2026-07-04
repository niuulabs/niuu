import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ReviewItem, ValkyrieEventTelemetry } from '../domain';
import { normalizeReviewItem } from '../domain';
import { ActivityDebugList, eventClasses } from './ActivityDebugList';

function event(
  overrides: Partial<ValkyrieEventTelemetry> & { id: string },
): ValkyrieEventTelemetry {
  return {
    eventType: 'ravn.log.emitted',
    kind: 'log',
    environmentId: 'env-k8s-valhalla',
    summary: '',
    observedAt: '2026-06-03T14:00:00Z',
    ...overrides,
  };
}

describe('eventClasses', () => {
  it('colors each kind distinctly', () => {
    expect(eventClasses('action')).toContain('state-warn');
    expect(eventClasses('judgment')).toContain('brand');
    expect(eventClasses('signal')).toContain('state-ok');
    expect(eventClasses('log')).toContain('text-muted');
    expect(eventClasses('task')).toContain('text-secondary');
  });
});

describe('ActivityDebugList', () => {
  it('falls back to the event type when a summary is missing', () => {
    render(<ActivityDebugList events={[event({ id: 'log-1' })]} settled={[]} />);
    const row = screen.getByTestId('activity-row');
    expect(row).toHaveTextContent('ravn.log.emitted');
    expect(row).not.toHaveTextContent('corr');
  });

  it('renders bare settled reviews with operator fallbacks', () => {
    const bare: ReviewItem = normalizeReviewItem({
      item_id: 'review-bare',
      kind: 'autonomy_change',
      status: 'approved',
      title: 'Autonomy raised',
      environment_id: 'env-k8s-valhalla',
      requested_at: '2026-06-03T13:00:00Z',
    });
    render(<ActivityDebugList events={[]} settled={[bare]} />);
    const row = screen.getByTestId('activity-row');
    expect(row).toHaveTextContent('decided by operator');
    expect(row).toHaveTextContent('autonomy');
    expect(row).not.toHaveTextContent('—');
  });
});
