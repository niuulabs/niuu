import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TopbarChip } from './TopbarChip';

describe('TopbarChip', () => {
  it('renders icon and label', () => {
    render(<TopbarChip kind="ok" icon="●" label="3 active" />);
    const chip = screen.getByTestId('topbar-chip-ok');
    expect(chip).toBeInTheDocument();
    expect(chip.textContent).toContain('●');
    expect(chip.textContent).toContain('3 active');
  });

  it('uses default testId from kind', () => {
    render(<TopbarChip kind="err" icon="●" label="2 failed" />);
    expect(screen.getByTestId('topbar-chip-err')).toBeInTheDocument();
  });

  it('uses custom testId when provided', () => {
    render(
      <TopbarChip kind="dim" icon="◈" label="threshold 0.70" testId="tyr-chip-threshold-0.70" />,
    );
    expect(screen.getByTestId('tyr-chip-threshold-0.70')).toBeInTheDocument();
  });

  it('applies kind-specific CSS class', () => {
    render(<TopbarChip kind="ok" icon="●" label="test" />);
    const chip = screen.getByTestId('topbar-chip-ok');
    expect(chip.className).toContain('niuu-topbar-chip--ok');
  });

  it('marks icon as aria-hidden', () => {
    const { container } = render(<TopbarChip kind="dim" icon="◷" label="sessions" />);
    const iconSpan = container.querySelector('[aria-hidden="true"]');
    expect(iconSpan).toBeInTheDocument();
    expect(iconSpan?.textContent).toBe('◷');
  });
});
