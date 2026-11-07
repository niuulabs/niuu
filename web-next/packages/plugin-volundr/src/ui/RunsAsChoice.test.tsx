import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { RunsAsChoice, RunsAsReadiness } from './RunsAsChoice';
import type { SessionRuntimeOffer } from '../domain/sessionRuntimes';

const container: SessionRuntimeOffer = {
  runtime: 'container',
  executionProfile: 'vm-container@1',
  isDefault: true,
  grants: [],
  readiness: { state: 'warm', warm: 1, remedy: null },
};
const host: SessionRuntimeOffer = {
  runtime: 'host',
  executionProfile: 'vm-host@1',
  isDefault: false,
  grants: ['sudo', 'docker'],
  readiness: { state: 'full', warm: 0, remedy: 'raise max machines' },
};

function renderChoice(offer: SessionRuntimeOffer, grants: SessionRuntimeOffer['grants'] = []) {
  const onSelect = vi.fn();
  const onGrantsChange = vi.fn();
  render(
    <RunsAsChoice
      offers={[container, host]}
      offer={offer}
      grants={grants}
      onSelect={onSelect}
      onGrantsChange={onGrantsChange}
    />,
  );
  return { onSelect, onGrantsChange };
}

describe('RunsAsChoice', () => {
  it('renders nothing for a host without a choice', () => {
    const { container: root } = render(
      <RunsAsChoice
        offers={[]}
        offer={undefined}
        grants={[]}
        onSelect={vi.fn()}
        onGrantsChange={vi.fn()}
      />,
    );
    expect(root).toBeEmptyDOMElement();
  });

  it('shows each offered runtime as a card and selects one', () => {
    const { onSelect } = renderChoice(container);
    expect(screen.getByRole('group', { name: 'Runs as' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Container.*Own image, own root/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.queryByText(/extra access/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /On the VM/ }));
    expect(onSelect).toHaveBeenCalledWith(host);
  });

  it('offers the chosen runtime grants with the machine-to-itself warning', () => {
    const { onGrantsChange } = renderChoice(host, ['docker']);
    expect(screen.getByRole('group', { name: 'On the VM: extra access' })).toBeInTheDocument();
    expect(screen.getByText('Either one gives this session a VM to itself.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Docker' })).toBeChecked();
    fireEvent.click(screen.getByRole('checkbox', { name: 'sudo' }));
    expect(onGrantsChange).toHaveBeenLastCalledWith(['sudo', 'docker']);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Docker' }));
    expect(onGrantsChange).toHaveBeenLastCalledWith([]);
  });
});

describe('RunsAsReadiness', () => {
  it('says how soon the chosen runtime starts', () => {
    const { rerender } = render(<RunsAsReadiness offer={container} />);
    expect(screen.getByRole('status')).toHaveTextContent('Free slot ready, starts in seconds');
    rerender(<RunsAsReadiness offer={host} />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'No capacity for a new session: raise max machines',
    );
    rerender(<RunsAsReadiness offer={undefined} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
