import type { PermissionRequest } from '@niuulabs/ui';
import type {
  PermissionAutoApprovalDecision,
  PermissionAutoApprovalRequest,
} from '../ports/IVolundrService';

export type { PermissionAutoApprovalDecision };

export function getPermissionCommand(permission: PermissionRequest): string | null {
  if (permission.command?.trim()) return permission.command.trim();
  const command = permission.input?.command;
  return typeof command === 'string' && command.trim() ? command.trim() : null;
}

export function buildPermissionAutoApprovalRequest(
  permission: PermissionRequest,
): PermissionAutoApprovalRequest {
  return {
    requestId: permission.requestId,
    toolName: permission.toolName,
    description: permission.description,
    command: getPermissionCommand(permission) ?? undefined,
    input: permission.input ?? {},
  };
}

export function failedPermissionAutoApprovalDecision(
  permission: PermissionRequest,
): PermissionAutoApprovalDecision {
  return {
    canAutoApprove: false,
    reason: 'endpoint_error',
    command: getPermissionCommand(permission),
    delaySeconds: 5,
    matchedPattern: null,
  };
}
