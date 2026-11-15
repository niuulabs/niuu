import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiClientError } from '@niuulabs/query';
import { seedComputeHosts } from '../../adapters/computeHosts.mock';
import { EditSlotsDialog } from './EditSlotsDialog';

const [, skinfaxi] = seedComputeHosts();

function renderDialog(overrides: Partial<Parameters<typeof EditSlotsDialog>[0]> = {}) {
  const props = {
    host: { ...skinfaxi!, state: 'out_of_service' as const, sessions: [], slots: 2 },
    onSave: vi.fn().mockResolvedValue(undefined),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<EditSlotsDialog {...props} />);
  return props;
}

describe('EditSlotsDialog', () => {
  it('starts at the host current slots and keeps Save off until it changes', () => {
    renderDialog();
    expect(screen.getByRole('dialog', { name: 'Slots of skinfaxi' })).toBeInTheDocument();
    expect(screen.getByLabelText('Sessions at once')).toHaveValue('2');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('saves a new count and closes', async () => {
    const props = renderDialog();
    fireEvent.change(screen.getByLabelText('Sessions at once'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
    expect(props.onSave).toHaveBeenCalledWith(3);
  });

  it('refuses a count that is not a positive whole number', () => {
    renderDialog();
    const input = screen.getByLabelText('Sessions at once');
    for (const value of ['', '0', '-1', '1.5', 'two']) {
      fireEvent.change(input, { target: { value } });
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    }
  });

  it("shows Forge's refusal and stays open", async () => {
    const props = renderDialog({
      onSave: vi
        .fn()
        .mockRejectedValue(
          new ApiClientError('failed', 409, 'Drain the host before changing its slots'),
        ),
    });
    fireEvent.change(screen.getByLabelText('Sessions at once'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Drain the host');
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('closes without saving on Cancel', () => {
    const props = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(props.onClose).toHaveBeenCalled();
    expect(props.onSave).not.toHaveBeenCalled();
  });

  it('shows the slot arithmetic only when the host has a suggestion', () => {
    renderDialog();
    expect(screen.queryByTestId('slot-arithmetic')).not.toBeInTheDocument();
  });

  it('shows the slot arithmetic when Forge suggests a count', () => {
    renderDialog({
      host: {
        ...skinfaxi!,
        slots: 2,
        suggestedSlots: { slots: 4, lines: ['16 cores / 4 cores per slot = 4'] },
      },
    });
    expect(screen.getByTestId('slot-arithmetic')).toHaveTextContent('16 cores / 4 cores per slot');
    expect(screen.getByTestId('slot-arithmetic')).toHaveTextContent('suggested: 4');
  });
});
