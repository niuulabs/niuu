import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { NewPairingCode } from '../../domain/computeHosts';
import { PairingCommand } from './PairingCommand';

const NOW = Date.parse('2026-11-06T12:00:00Z');
const CODE = 'eyJhbGciOiJIUzI1NiJ9.payload-of-the-code.signature';
const code: NewPairingCode = {
  id: 'c-1',
  execution: 'host@2026-11-07',
  connector: 'direct-ssh',
  tags: [],
  slots: null,
  expiresAt: '2026-11-06T12:09:12Z',
  createdBy: 'jozef',
  consumedByHostId: null,
  code: CODE,
  command: `curl -fsSL -H 'Authorization: Bearer ${CODE}' https://forge/join.sh | sudo sh`,
  commandWget: `wget -qO- --header='Authorization: Bearer ${CODE}' https://forge/join.sh | sudo sh`,
  joinUrl: 'https://forge',
  pin: null,
};

beforeEach(() => {
  vi.useFakeTimers({ now: NOW });
});

afterEach(() => {
  vi.useRealTimers();
});

function renderCommand(overrides: Partial<Parameters<typeof PairingCommand>[0]> = {}) {
  const props = {
    code,
    revoking: false,
    error: null,
    onRevoke: vi.fn(),
    onRestart: vi.fn(),
    ...overrides,
  };
  render(<PairingCommand {...props} />);
  return props;
}

describe('PairingCommand', () => {
  it('shows the one-line command with the code cut down', () => {
    renderCommand();
    const command = screen.getByLabelText('Join command');
    expect(command).toHaveTextContent(
      "curl -fsSL -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9…'",
    );
    expect(command).not.toHaveTextContent('signature');
  });

  it('counts down to expiry', () => {
    renderCommand();
    expect(screen.getByRole('timer')).toHaveTextContent('One host · expires in 9:12');
    act(() => {
      vi.advanceTimersByTime(12_000);
    });
    expect(screen.getByRole('timer')).toHaveTextContent('expires in 9:00');
  });

  it('copies the whole command, not the cut-down one', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderCommand();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    });
    expect(writeText).toHaveBeenCalledWith(code.command);
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('switches to the wget variant and back', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderCommand();
    fireEvent.click(screen.getByRole('button', { name: 'Use wget' }));
    expect(screen.getByLabelText('Join command')).toHaveTextContent('wget -qO-');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    });
    expect(writeText).toHaveBeenCalledWith(code.commandWget);
    fireEvent.click(screen.getByRole('button', { name: 'Use curl' }));
    expect(screen.getByLabelText('Join command')).toHaveTextContent('curl -fsSL');
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('offers only curl for a pinned join and shows what it pins', () => {
    renderCommand({
      code: {
        ...code,
        command: `curl -fsSL -k --pinnedpubkey sha256//abc= -H 'Authorization: Bearer ${CODE}' https://192.168.1.20:8443/join.sh | sudo sh`,
        commandWget: null,
        joinUrl: 'https://192.168.1.20:8443',
        pin: 'sha256//abc=',
      },
    });
    expect(screen.queryByRole('button', { name: 'Use wget' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Join command')).toHaveTextContent('--pinnedpubkey sha256//abc=');
    const pinned = screen.getByText(/pinned to this Forge's certificate/);
    expect(pinned).toHaveTextContent('https://192.168.1.20:8443');
    expect(pinned).toHaveTextContent('sha256//abc=');
    expect(pinned).toHaveTextContent('wget cannot check a pin');
  });

  it('says nothing about a pin when the join is not pinned', () => {
    renderCommand();
    expect(screen.queryByText(/pinned to this Forge's certificate/)).not.toBeInTheDocument();
  });

  it('waits for a host to run it', () => {
    renderCommand();
    expect(screen.getByText('Waiting for a host…')).toBeInTheDocument();
    expect(screen.getByText(/Waiting for the command to run/)).toBeInTheDocument();
  });

  it('revokes the code', () => {
    const props = renderCommand();
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    expect(props.onRevoke).toHaveBeenCalled();
  });

  it('holds Revoke while it is in flight and shows a failure', () => {
    renderCommand({ revoking: true, error: 'could not revoke' });
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('could not revoke');
  });

  it('says when the command expired and offers a new one', () => {
    const props = renderCommand({ code: { ...code, expiresAt: '2026-11-06T11:59:00Z' } });
    expect(screen.getByRole('status')).toHaveTextContent('Expired');
    expect(screen.queryByText('Waiting for a host…')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Create a new command' }));
    expect(props.onRestart).toHaveBeenCalled();
  });

  it('expires as the clock reaches it', () => {
    renderCommand({ code: { ...code, expiresAt: '2026-11-06T12:00:02Z' } });
    expect(screen.getByRole('timer')).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByRole('status')).toHaveTextContent('Expired');
  });
});
