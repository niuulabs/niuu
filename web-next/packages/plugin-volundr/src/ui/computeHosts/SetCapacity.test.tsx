import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiClientError } from '@niuulabs/query';
import { seedComputeHosts } from '../../adapters/computeHosts.mock';
import type { ComputeHost } from '../../domain/computeHosts';
import { CapacityExplainer, SetCapacity } from './SetCapacity';

const glad = seedComputeHosts()[6]!;
const readyHost: ComputeHost = {
  ...glad,
  state: 'ready',
  health: 'healthy',
  suggestedSlots: {
    slots: 11,
    lines: ['CPU (48 − 4) ÷ 4 = 11', 'memory (192 − 8) ÷ 16 = 11'],
  },
};

function renderCapacity(
  host: ComputeHost = readyHost,
  onSave = vi.fn().mockResolvedValue(undefined),
) {
  const onSkip = vi.fn();
  render(<SetCapacity host={host} onSave={onSave} onSkip={onSkip} />);
  return { onSave, onSkip };
}

describe('SetCapacity', () => {
  it('shows what the check measured', () => {
    renderCapacity();
    expect(screen.getByText('Prepared and audited')).toBeInTheDocument();
    expect(
      screen.getByText('Recipe host@2026-11-07 ran and the audit found nothing.'),
    ).toBeInTheDocument();
    expect(screen.getByText('48 cores')).toBeInTheDocument();
    expect(screen.getByText('192 GiB')).toBeInTheDocument();
    expect(screen.getByText('1.5 TiB')).toBeInTheDocument();
    expect(screen.getByText('6.8.0 · cgroup v2')).toBeInTheDocument();
    expect(screen.getByText('x86_64')).toBeInTheDocument();
    expect(screen.getByText('none')).toBeInTheDocument();
  });

  it('shows the arithmetic behind the suggestion and starts from it', () => {
    renderCapacity();
    const arithmetic = screen.getByTestId('slot-arithmetic');
    expect(arithmetic).toHaveTextContent('CPU (48 − 4) ÷ 4 = 11');
    expect(arithmetic).toHaveTextContent('memory (192 − 8) ÷ 16 = 11');
    expect(arithmetic).toHaveTextContent('suggested: 11 sessions at once');
    expect(screen.getByLabelText('Sessions at once')).toHaveValue('11');
    expect(screen.getByText(/More than 11 is refused with this arithmetic/)).toBeInTheDocument();
  });

  it('puts the host in service with the number entered', async () => {
    const { onSave } = renderCapacity();
    fireEvent.change(screen.getByLabelText('Sessions at once'), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Put in service' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(8));
  });

  it('refuses a number that is not a positive whole number', () => {
    renderCapacity();
    const put = screen.getByRole('button', { name: 'Put in service' });
    for (const bad of ['', '0', '-3', '2.5', 'many']) {
      fireEvent.change(screen.getByLabelText('Sessions at once'), { target: { value: bad } });
      expect(put).toBeDisabled();
    }
    fireEvent.change(screen.getByLabelText('Sessions at once'), { target: { value: '1' } });
    expect(put).toBeEnabled();
  });

  it('shows the platform arithmetic when the number is refused', async () => {
    const { onSave } = renderCapacity(
      readyHost,
      vi
        .fn()
        .mockRejectedValue(
          new ApiClientError('failed', 422, '40 is over the 11 this host can hold'),
        ),
    );
    fireEvent.change(screen.getByLabelText('Sessions at once'), { target: { value: '40' } });
    fireEvent.click(screen.getByRole('button', { name: 'Put in service' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '40 is over the 11 this host can hold',
    );
    expect(onSave).toHaveBeenCalledWith(40);
    expect(screen.getByRole('button', { name: 'Put in service' })).toBeEnabled();
  });

  it('keeps the host out of service without setting a number', () => {
    const { onSave, onSkip } = renderCapacity();
    fireEvent.click(screen.getByRole('button', { name: 'Keep out of service' }));
    expect(onSkip).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('says so when the check made no suggestion, and uses unmeasured facts as dashes', () => {
    const unmeasured: ComputeHost = {
      ...readyHost,
      suggestedSlots: null,
      facts: {
        cpus: null,
        memoryMib: null,
        diskFreeMib: null,
        kernel: null,
        arch: null,
        cgroupV2: null,
        gpus: 2,
        os: null,
      },
    };
    renderCapacity(unmeasured);
    expect(
      screen.getByText('No suggestion yet: the check has not measured this host.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Sessions at once')).toHaveValue('');
    expect(screen.getByText(/A number over what the host can hold is refused/)).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('says one session in the singular', () => {
    renderCapacity({ ...readyHost, suggestedSlots: { slots: 1, lines: ['memory 1'] } });
    expect(screen.getByTestId('slot-arithmetic')).toHaveTextContent('suggested: 1 session at once');
  });
});

describe('CapacityExplainer', () => {
  it('explains what a session on a host runtime gets and how it is targeted', () => {
    render(<CapacityExplainer host={readyHost} />);
    expect(screen.getByText('Each session on this host gets')).toBeInTheDocument();
    expect(screen.getByText('When a session stops')).toBeInTheDocument();
    expect(screen.getByText('Targeting')).toBeInTheDocument();
    expect(screen.getByText('site:hnoss')).toBeInTheDocument();
    expect(screen.getByText('fast-disk')).toBeInTheDocument();
  });

  it('describes only targeting for another runtime, and nothing for an untagged host', () => {
    const { rerender } = render(
      <CapacityExplainer host={{ ...readyHost, runtime: 'openshell', tags: ['gpu'] }} />,
    );
    expect(screen.queryByText('Each session on this host gets')).toBeNull();
    expect(screen.getByText('gpu')).toBeInTheDocument();
    expect(screen.queryByText(/it also matches/)).toBeNull();
    rerender(<CapacityExplainer host={{ ...readyHost, runtime: 'openshell', tags: [] }} />);
    expect(screen.queryByText('Targeting')).toBeNull();
  });
});
