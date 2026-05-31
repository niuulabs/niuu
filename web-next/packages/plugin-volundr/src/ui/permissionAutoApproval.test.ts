import { describe, expect, it } from 'vitest';
import {
  buildPermissionAutoApprovalRequest,
  failedPermissionAutoApprovalDecision,
  getPermissionCommand,
} from './permissionAutoApproval';

describe('permission auto approval request helpers', () => {
  it('extracts the structured command from permission input', () => {
    expect(
      getPermissionCommand({
        requestId: 'perm-1',
        toolName: 'Bash',
        description: 'Run command',
        input: { command: './start-dev' },
      }),
    ).toBe('./start-dev');
  });

  it('prefers the top-level command threaded through chat state', () => {
    expect(
      getPermissionCommand({
        requestId: 'perm-1',
        toolName: 'Bash',
        description: 'Run command',
        command: './stop-dev',
        input: { command: './start-dev' },
      }),
    ).toBe('./stop-dev');
  });

  it('builds the endpoint request payload without evaluating policy in the browser', () => {
    expect(
      buildPermissionAutoApprovalRequest({
        requestId: 'perm-1',
        toolName: 'Bash',
        description: 'Run command',
        command: './start-dev',
        input: { command: './start-dev' },
      }),
    ).toEqual({
      requestId: 'perm-1',
      toolName: 'Bash',
      description: 'Run command',
      command: './start-dev',
      input: { command: './start-dev' },
    });
  });

  it('creates a conservative failure decision for endpoint errors', () => {
    expect(
      failedPermissionAutoApprovalDecision({
        requestId: 'perm-1',
        toolName: 'Bash',
        description: 'Run command',
        command: './start-dev',
      }),
    ).toMatchObject({
      canAutoApprove: false,
      reason: 'endpoint_error',
      command: './start-dev',
    });
  });
});
