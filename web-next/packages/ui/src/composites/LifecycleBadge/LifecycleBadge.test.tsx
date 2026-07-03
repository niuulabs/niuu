import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LifecycleBadge, LIFECYCLE_META } from './LifecycleBadge';
import type { LifecycleState } from './LifecycleBadge';

const ALL_STATES: LifecycleState[] = [
  'provisioning',
  'ready',
  'running',
  'idle',
  'awaiting_input',
  'terminating',
  'terminated',
  'failed',
];

// States whose displayed label equals the raw state value (awaiting_input
// renders a friendly "needs you" label and is asserted separately).
const RAW_LABEL_STATES = ALL_STATES.filter((s) => s !== 'awaiting_input');

describe('LifecycleBadge', () => {
  it.each(RAW_LABEL_STATES)('renders the state label for "%s"', (state) => {
    render(<LifecycleBadge state={state} />);
    expect(screen.getByText(state)).toBeInTheDocument();
  });

  it('renders a friendly label for awaiting_input', () => {
    render(<LifecycleBadge state="awaiting_input" />);
    expect(screen.getByText('needs you')).toBeInTheDocument();
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
    const PULSING: LifecycleState[] = ['provisioning', 'running', 'awaiting_input', 'terminating'];
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

  it('LIFECYCLE_META covers all 8 states', () => {
    expect(Object.keys(LIFECYCLE_META)).toHaveLength(8);
    for (const state of ALL_STATES) {
      expect(LIFECYCLE_META[state]).toBeTruthy();
    }
  });
});
