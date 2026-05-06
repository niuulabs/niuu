import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LifecycleBadge, LIFECYCLE_META } from './LifecycleBadge';
import type { LifecycleState } from './LifecycleBadge';

const ALL_STATES: LifecycleState[] = [
  'provisioning',
  'ready',
  'running',
  'idle',
  'terminating',
  'terminated',
  'failed',
];

describe('LifecycleBadge', () => {
  it.each(ALL_STATES)('renders the state label for "%s"', (state) => {
    render(<LifecycleBadge state={state} />);
    expect(screen.getByText(state)).toBeInTheDocument();
  });

  it.each(ALL_STATES)('has the correct aria-label for "%s"', (state) => {
    const { container } = render(<LifecycleBadge state={state} />);
    const badge = container.querySelector('.niuu-lifecycle-badge');
    expect(badge).toHaveAttribute('aria-label', state);
  });

  it.each(ALL_STATES)('applies the state modifier class for "%s"', (state) => {
    const { container } = render(<LifecycleBadge state={state} />);
    expect(container.firstChild).toHaveClass(`niuu-lifecycle-badge--${state}`);
  });

  it.each(ALL_STATES)('renders a state dot for "%s"', (state) => {
    const { container } = render(<LifecycleBadge state={state} />);
    const meta = LIFECYCLE_META[state];
    expect(container.querySelector(`.niuu-state-dot--${meta.dotState}`)).toBeInTheDocument();
  });

  describe('pulsing states', () => {
    const PULSING: LifecycleState[] = ['provisioning', 'running', 'terminating'];
    const NON_PULSING: LifecycleState[] = ['ready', 'idle', 'terminated', 'failed'];

    it.each(PULSING)('"%s" has a pulsing dot', (state) => {
      const { container } = render(<LifecycleBadge state={state} />);
      expect(container.querySelector('.niuu-state-dot--pulse')).toBeInTheDocument();
    });

    it.each(NON_PULSING)('"%s" does not have a pulsing dot', (state) => {
      const { container } = render(<LifecycleBadge state={state} />);
      expect(container.querySelector('.niuu-state-dot--pulse')).not.toBeInTheDocument();
    });
  });

  it('accepts a custom className', () => {
    const { container } = render(<LifecycleBadge state="running" className="extra" />);
    expect(container.firstChild).toHaveClass('niuu-lifecycle-badge', 'extra');
  });

  it('LIFECYCLE_META covers all 7 states', () => {
    expect(Object.keys(LIFECYCLE_META)).toHaveLength(7);
    for (const state of ALL_STATES) {
      expect(LIFECYCLE_META[state]).toBeTruthy();
    }
  });
});
