import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { RunsAs, RunsAsReadiness } from './RunsAs';
import type { SessionRuntimeOffer } from '../domain/sessionRuntimes';

const container: SessionRuntimeOffer = {
  runtime: 'container',
  grants: [],
  readiness: { state: 'warm', warm: 1, remedy: null },
};
const host: SessionRuntimeOffer = {
  runtime: 'host',
  grants: ['sudo', 'docker'],
  readiness: { state: 'full', warm: 0, remedy: 'raise max machines' },
};

function renderRunsAs(offer: SessionRuntimeOffer, grants: SessionRuntimeOffer['grants'] = []) {
  const onGrantsChange = vi.fn();
  render(<RunsAs offer={offer} grants={grants} onGrantsChange={onGrantsChange} />);
  return { onGrantsChange };
}

describe('RunsAs', () => {
  it('renders nothing for a host that declares no runtime', () => {
    const { container: root } = render(
      <RunsAs offer={undefined} grants={[]} onGrantsChange={vi.fn()} />,
    );
    expect(root).toBeEmptyDOMElement();
  });

  it('names the host runtime as a read-only line with no choice and no grants', () => {
    renderRunsAs(container);
    expect(screen.getByTestId('runs-as')).toHaveTextContent('Runs as: Container');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.queryByText(/extra access/)).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('offers the runtime grants with the machine-to-itself warning', () => {
    const { onGrantsChange } = renderRunsAs(host, ['docker']);
    expect(screen.getByTestId('runs-as')).toHaveTextContent('Runs as: On the VM');
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
  it('says how soon a session starts', () => {
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
