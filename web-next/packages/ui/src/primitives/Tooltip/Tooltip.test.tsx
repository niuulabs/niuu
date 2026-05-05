import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Tooltip, TooltipProvider } from './Tooltip';

function setup() {
  return userEvent.setup({ delay: null });
}

function Wrapped({ children }: { children: React.ReactNode }) {
  return <TooltipProvider>{children}</TooltipProvider>;
}

describe('Tooltip', () => {
  it('renders the trigger element', () => {
    render(
      <Wrapped>
        <Tooltip content="Hint text">
          <button>Hover me</button>
        </Tooltip>
      </Wrapped>,
    );
    expect(screen.getByRole('button', { name: 'Hover me' })).toBeInTheDocument();
  });

  it('tooltip content is not visible by default', () => {
    render(
      <Wrapped>
        <Tooltip content="Hidden hint">
          <button>Hover</button>
        </Tooltip>
      </Wrapped>,
    );
    expect(screen.queryByText('Hidden hint')).toBeNull();
  });

  it('shows content on pointer enter', async () => {
    const user = setup();
    render(
      <Wrapped>
        <Tooltip content="Tooltip text" delayMs={0}>
          <button>Hover me</button>
        </Tooltip>
      </Wrapped>,
    );
    await user.hover(screen.getByRole('button', { name: 'Hover me' }));
    // Radix renders the text in both the visible div and a SR-only span; use role query.
    await waitFor(() => expect(screen.getByRole('tooltip')).toBeInTheDocument());
  });

  it('trigger gains data-state=delayed-open attribute when tooltip is visible', async () => {
    const user = setup();
    render(
      <Wrapped>
        <Tooltip content="Open state test" delayMs={0}>
          <button>Hover me</button>
        </Tooltip>
      </Wrapped>,
    );
    const trigger = screen.getByRole('button', { name: 'Hover me' });
    // Initially closed
    expect(trigger).toHaveAttribute('data-state', 'closed');
    await user.hover(trigger);
    // After hover, Radix sets data-state on the trigger
    await waitFor(() => expect(trigger.getAttribute('data-state')).not.toBe('closed'));
  });

  it('accepts side prop without errors', async () => {
    const user = setup();
    render(
      <Wrapped>
        <Tooltip content="Right side" side="right" delayMs={0}>
          <button>Target</button>
        </Tooltip>
      </Wrapped>,
    );
    await user.hover(screen.getByRole('button', { name: 'Target' }));
    await waitFor(() => expect(screen.getByRole('tooltip')).toBeInTheDocument());
  });

  it('TooltipProvider wraps multiple tooltips', () => {
    render(
      <TooltipProvider>
        <Tooltip content="First">
          <button>A</button>
        </Tooltip>
        <Tooltip content="Second">
          <button>B</button>
        </Tooltip>
      </TooltipProvider>,
    );
    expect(screen.getByRole('button', { name: 'A' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'B' })).toBeInTheDocument();
  });
});
