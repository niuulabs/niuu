import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EditRulesModal } from './EditRulesModal';
import type { RulesFormState } from './EditRulesModal';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRules(overrides: Partial<RulesFormState> = {}): RulesFormState {
  return {
    threshold: 70,
    maxConcurrentRaids: 3,
    autoContinue: false,
    retryCount: 2,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('EditRulesModal', () => {
  it('renders nothing when closed', () => {
    render(
      <EditRulesModal open={false} onOpenChange={vi.fn()} rules={makeRules()} onSave={vi.fn()} />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders dialog when open', () => {
    render(
      <EditRulesModal open={true} onOpenChange={vi.fn()} rules={makeRules()} onSave={vi.fn()} />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Edit dispatch rules')).toBeInTheDocument();
  });

  it('populates form with current rules', () => {
    render(
      <EditRulesModal
        open={true}
        onOpenChange={vi.fn()}
        rules={makeRules({ threshold: 80, maxConcurrentRaids: 5, retryCount: 3 })}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('spinbutton', { name: /confidence threshold/i })).toHaveValue(80);
    expect(screen.getByRole('spinbutton', { name: /max concurrent raids/i })).toHaveValue(5);
    expect(screen.getByRole('spinbutton', { name: /retry count/i })).toHaveValue(3);
  });

  it('shows autoContinue toggle as "off" initially', () => {
    render(
      <EditRulesModal
        open={true}
        onOpenChange={vi.fn()}
        rules={makeRules({ autoContinue: false })}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /toggle auto-continue/i })).toHaveTextContent('off');
  });

  it('shows autoContinue toggle as "on" when true', () => {
    render(
      <EditRulesModal
        open={true}
        onOpenChange={vi.fn()}
        rules={makeRules({ autoContinue: true })}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /toggle auto-continue/i })).toHaveTextContent('on');
  });

  it('toggles autoContinue when clicked', async () => {
    const user = userEvent.setup();
    render(
      <EditRulesModal
        open={true}
        onOpenChange={vi.fn()}
        rules={makeRules({ autoContinue: false })}
        onSave={vi.fn()}
      />,
    );
    const toggleBtn = screen.getByRole('button', { name: /toggle auto-continue/i });
    expect(toggleBtn).toHaveTextContent('off');
    await user.click(toggleBtn);
    expect(toggleBtn).toHaveTextContent('on');
  });

  it('calls onSave with updated values on Save click', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <EditRulesModal
        open={true}
        onOpenChange={onOpenChange}
        rules={makeRules({
          threshold: 70,
          maxConcurrentRaids: 3,
          autoContinue: false,
          retryCount: 2,
        })}
        onSave={onSave}
      />,
    );
    // Toggle autoContinue
    await user.click(screen.getByRole('button', { name: /toggle auto-continue/i }));
    await user.click(screen.getByRole('button', { name: /save/i }));
    expect(onSave).toHaveBeenCalledWith({
      threshold: 70,
      maxConcurrentRaids: 3,
      autoContinue: true,
      retryCount: 2,
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('closes when Cancel is clicked', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <EditRulesModal
        open={true}
        onOpenChange={onOpenChange}
        rules={makeRules()}
        onSave={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('resets to original rules when reopened', () => {
    const rules = makeRules({ threshold: 70 });
    const { rerender } = render(
      <EditRulesModal open={false} onOpenChange={vi.fn()} rules={rules} onSave={vi.fn()} />,
    );
    rerender(
      <EditRulesModal
        open={true}
        onOpenChange={vi.fn()}
        rules={makeRules({ threshold: 85 })}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('spinbutton', { name: /confidence threshold/i })).toHaveValue(85);
  });

  it('renders all four form fields', () => {
    render(
      <EditRulesModal open={true} onOpenChange={vi.fn()} rules={makeRules()} onSave={vi.fn()} />,
    );
    expect(screen.getByLabelText(/confidence threshold/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/max concurrent raids/i)).toBeInTheDocument();
    expect(screen.getByText('Auto-continue')).toBeInTheDocument();
    expect(screen.getByLabelText(/retry count/i)).toBeInTheDocument();
  });
});
