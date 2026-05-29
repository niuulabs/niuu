import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionApprovalPanel } from './PermissionApprovalPanel';

describe('PermissionApprovalPanel', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the command inside the approval bar', async () => {
    render(
      <PermissionApprovalPanel
        permissions={[
          {
            requestId: 'perm-1',
            toolName: 'Bash',
            description: 'echo hello',
            command: 'echo hello',
            input: { command: 'echo hello' },
          },
        ]}
        onRespond={vi.fn()}
        evaluateAutoApproval={vi.fn().mockResolvedValue({
          canAutoApprove: false,
          reason: 'no_allowlist_match',
          command: 'echo hello',
          delaySeconds: 5,
        })}
      />,
    );

    expect(screen.getByTestId('permission-approval-panel')).toBeInTheDocument();
    expect(screen.getByText('echo hello')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('not allowlisted')).toBeInTheDocument());
  });

  it('auto approves an allowlisted command after the server-backed countdown', async () => {
    vi.useFakeTimers();
    const onRespond = vi.fn();
    const evaluateAutoApproval = vi.fn().mockResolvedValue({
      canAutoApprove: true,
      reason: 'allowed',
      command: 'echo ready',
      delaySeconds: 2,
      matchedPattern: '^echo',
    });

    render(
      <PermissionApprovalPanel
        permissions={[
          {
            requestId: 'perm-auto',
            toolName: 'Bash',
            description: 'echo ready',
            command: 'echo ready',
            input: { command: 'echo ready' },
          },
        ]}
        onRespond={onRespond}
        evaluateAutoApproval={evaluateAutoApproval}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText(/auto allow in 2s/i)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_999);
    });
    expect(onRespond).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(onRespond).toHaveBeenCalledWith('perm-auto', 'allow_once');
    expect(evaluateAutoApproval).toHaveBeenCalledTimes(2);
  });

  it('does not auto approve when the server denies the command', async () => {
    vi.useFakeTimers();
    const onRespond = vi.fn();

    render(
      <PermissionApprovalPanel
        permissions={[
          {
            requestId: 'perm-deny',
            toolName: 'Bash',
            description: 'rm -rf /tmp/volundr-test',
            command: 'rm -rf /tmp/volundr-test',
          },
        ]}
        onRespond={onRespond}
        evaluateAutoApproval={vi.fn().mockResolvedValue({
          canAutoApprove: false,
          reason: 'denylist',
          command: 'rm -rf /tmp/volundr-test',
          delaySeconds: 2,
          matchedPattern: 'rm\\s+-rf',
        })}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText('server denylist match')).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(onRespond).not.toHaveBeenCalled();
  });

  it('can still be manually denied before an auto approval fires', async () => {
    vi.useFakeTimers();
    const onRespond = vi.fn();

    render(
      <PermissionApprovalPanel
        permissions={[
          {
            requestId: 'perm-manual',
            toolName: 'Bash',
            description: 'echo maybe',
            command: 'echo maybe',
          },
        ]}
        onRespond={onRespond}
        evaluateAutoApproval={vi.fn().mockResolvedValue({
          canAutoApprove: true,
          reason: 'allowed',
          command: 'echo maybe',
          delaySeconds: 2,
          matchedPattern: '^echo',
        })}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText(/auto allow in 2s/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'deny' }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });

    expect(onRespond).toHaveBeenCalledTimes(1);
    expect(onRespond).toHaveBeenCalledWith('perm-manual', 'deny');
  });
});
