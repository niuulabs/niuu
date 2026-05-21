import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MountChip } from './MountChip';

describe('MountChip', () => {
  it('renders a passive chip when no click handler is provided', () => {
    render(<MountChip name="scratch" role="read" />);

    const chip = screen.getByLabelText('mount: scratch');
    expect(chip.tagName).toBe('SPAN');
    expect(chip).toHaveClass('mm-mount-chip--read');
    expect(screen.getByText('read')).toBeInTheDocument();
  });

  it('renders a button chip and calls through when clickable', () => {
    const onClick = vi.fn();
    render(<MountChip name="research" onClick={onClick} />);

    const chip = screen.getByRole('button', { name: 'mount: research' });
    expect(chip).toHaveClass('mm-mount-chip--local');

    fireEvent.click(chip);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
