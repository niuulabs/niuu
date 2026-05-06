import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThresholdOverrideModal } from './ThresholdOverrideModal';

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ThresholdOverrideModal', () => {
  it('renders nothing when closed', () => {
    render(
      <ThresholdOverrideModal
        open={false}
        onOpenChange={vi.fn()}
        currentThreshold={0.7}
        onApply={vi.fn()}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders dialog when open', () => {
    render(
      <ThresholdOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        currentThreshold={0.7}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Override dispatch threshold')).toBeInTheDocument();
  });

  it('shows current threshold value', () => {
    render(
      <ThresholdOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        currentThreshold={0.75}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByText('0.75')).toBeInTheDocument();
  });

  it('renders slider with correct attributes', () => {
    render(
      <ThresholdOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        currentThreshold={0.6}
        onApply={vi.fn()}
      />,
    );
    const slider = screen.getByRole('slider', { name: /threshold value/i });
    expect(slider).toBeInTheDocument();
    expect(slider).toHaveAttribute('min', '0');
    expect(slider).toHaveAttribute('max', '1');
    expect(slider).toHaveAttribute('step', '0.05');
  });

  it('calls onApply with current value on Apply click', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <ThresholdOverrideModal
        open={true}
        onOpenChange={onOpenChange}
        currentThreshold={0.7}
        onApply={onApply}
      />,
    );
    await user.click(screen.getByRole('button', { name: /apply/i }));
    expect(onApply).toHaveBeenCalledWith(0.7);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('closes when Cancel is clicked', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <ThresholdOverrideModal
        open={true}
        onOpenChange={onOpenChange}
        currentThreshold={0.7}
        onApply={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('resets value to currentThreshold when reopened', () => {
    const { rerender } = render(
      <ThresholdOverrideModal
        open={false}
        onOpenChange={vi.fn()}
        currentThreshold={0.7}
        onApply={vi.fn()}
      />,
    );

    rerender(
      <ThresholdOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        currentThreshold={0.85}
        onApply={vi.fn()}
      />,
    );

    expect(screen.getByText('0.85')).toBeInTheDocument();
  });

  it('shows description text', () => {
    render(
      <ThresholdOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        currentThreshold={0.7}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByText(/raids with confidence below/i)).toBeInTheDocument();
  });
});
