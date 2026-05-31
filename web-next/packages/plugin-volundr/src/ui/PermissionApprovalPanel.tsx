import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Clock3, ShieldAlert, ShieldCheck } from 'lucide-react';
import { cn, type PermissionBehavior, type PermissionRequest } from '@niuulabs/ui';
import {
  failedPermissionAutoApprovalDecision,
  getPermissionCommand,
  type PermissionAutoApprovalDecision,
} from './permissionAutoApproval';

interface PermissionApprovalPanelProps {
  permissions: PermissionRequest[];
  onRespond: (requestId: string, behavior: PermissionBehavior) => void;
  evaluateAutoApproval: (permission: PermissionRequest) => Promise<PermissionAutoApprovalDecision>;
}

function statusLabel(decision: PermissionAutoApprovalDecision | null, checking: boolean): string {
  if (checking) return 'checking policy';
  switch (decision?.reason) {
    case 'allowed':
      return 'server allowlist match';
    case 'disabled':
      return 'auto off';
    case 'no_command':
      return 'no command';
    case 'denylist':
      return 'server denylist match';
    case 'no_allowlist_match':
      return 'not allowlisted';
    case 'endpoint_error':
    default:
      return 'policy check failed';
  }
}

function PermissionApprovalItem({
  permission,
  evaluateAutoApproval,
  onRespond,
}: {
  permission: PermissionRequest;
  evaluateAutoApproval: (permission: PermissionRequest) => Promise<PermissionAutoApprovalDecision>;
  onRespond: (requestId: string, behavior: PermissionBehavior) => void;
}) {
  const command = getPermissionCommand(permission);
  const visibleCommand = command ?? permission.description;
  const [decision, setDecision] = useState<PermissionAutoApprovalDecision | null>(null);
  const [checking, setChecking] = useState(true);
  const [remainingMs, setRemainingMs] = useState(0);
  const respondedRef = useRef(false);

  const respond = useCallback(
    (behavior: PermissionBehavior) => {
      if (respondedRef.current) return;
      respondedRef.current = true;
      onRespond(permission.requestId, behavior);
    },
    [onRespond, permission.requestId],
  );

  useEffect(() => {
    respondedRef.current = false;
  }, [permission.requestId]);

  useEffect(() => {
    let active = true;

    void evaluateAutoApproval(permission)
      .then((nextDecision) => {
        if (!active) return;
        setDecision(nextDecision);
        setRemainingMs(nextDecision.canAutoApprove ? nextDecision.delaySeconds * 1000 : 0);
      })
      .catch(() => {
        if (!active) return;
        setDecision(failedPermissionAutoApprovalDecision(permission));
      })
      .finally(() => {
        if (active) setChecking(false);
      });

    return () => {
      active = false;
    };
  }, [evaluateAutoApproval, permission]);

  useEffect(() => {
    if (!decision?.canAutoApprove || respondedRef.current) return undefined;

    const delayMs = Math.max(0, decision.delaySeconds * 1000);
    const startedAt = Date.now();
    const timer = window.setTimeout(() => {
      void evaluateAutoApproval(permission)
        .then((latestDecision) => {
          if (respondedRef.current) return;
          if (latestDecision.canAutoApprove) {
            respond('allow_once');
            return;
          }
          setDecision(latestDecision);
          setRemainingMs(0);
        })
        .catch(() => {
          if (respondedRef.current) return;
          setDecision(failedPermissionAutoApprovalDecision(permission));
          setRemainingMs(0);
        });
    }, delayMs);
    const ticker = window.setInterval(() => {
      const elapsed = Date.now() - startedAt;
      setRemainingMs(Math.max(0, delayMs - elapsed));
    }, 250);

    return () => {
      window.clearTimeout(timer);
      window.clearInterval(ticker);
    };
  }, [decision, evaluateAutoApproval, permission, respond]);

  const canAutoApprove = Boolean(decision?.canAutoApprove);
  const delayMs = Math.max(0, (decision?.delaySeconds ?? 0) * 1000);
  const remainingSeconds = canAutoApprove ? Math.max(0, Math.ceil(remainingMs / 1000)) : 0;
  const progress =
    canAutoApprove && delayMs > 0
      ? Math.max(0, Math.min(100, ((delayMs - remainingMs) / delayMs) * 100))
      : 0;

  return (
    <div
      className={cn(
        'niuu-grid niuu-gap-3 niuu-rounded-md niuu-border niuu-p-3 niuu-text-xs',
        decision?.reason === 'denylist'
          ? 'niuu-border-critical/45 niuu-bg-critical/8'
          : 'niuu-border-border-subtle niuu-bg-bg-secondary',
      )}
    >
      <div className="niuu-flex niuu-min-w-0 niuu-flex-wrap niuu-items-center niuu-gap-2">
        <span className="niuu-inline-flex niuu-items-center niuu-gap-1 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary">
          {permission.toolName}
        </span>
        <span
          className={cn(
            'niuu-inline-flex niuu-items-center niuu-gap-1 niuu-rounded-md niuu-border niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[11px]',
            canAutoApprove
              ? 'niuu-border-brand/35 niuu-bg-brand/10 niuu-text-brand'
              : 'niuu-border-border-subtle niuu-bg-bg-primary niuu-text-text-muted',
          )}
        >
          {canAutoApprove ? (
            <ShieldCheck className="niuu-h-3.5 niuu-w-3.5" />
          ) : (
            <ShieldAlert className="niuu-h-3.5 niuu-w-3.5" />
          )}
          {statusLabel(decision, checking)}
        </span>
        {canAutoApprove ? (
          <span className="niuu-inline-flex niuu-items-center niuu-gap-1 niuu-rounded-md niuu-border niuu-border-brand/35 niuu-bg-bg-primary niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-brand">
            <Clock3 className="niuu-h-3.5 niuu-w-3.5" />
            auto allow in {remainingSeconds}s
          </span>
        ) : null}
      </div>

      <pre className="niuu-max-h-28 niuu-overflow-auto niuu-whitespace-pre-wrap niuu-break-words niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-2 niuu-font-mono niuu-text-[11px] niuu-leading-5 niuu-text-text-primary">
        {visibleCommand}
      </pre>

      {canAutoApprove ? (
        <div className="niuu-h-1 niuu-overflow-hidden niuu-rounded-full niuu-bg-bg-primary">
          <div
            className="niuu-h-full niuu-rounded-full niuu-bg-brand niuu-transition-[width] niuu-duration-200"
            style={{ width: `${progress}%` }}
          />
        </div>
      ) : null}

      <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-justify-between niuu-gap-2">
        <span className="niuu-min-w-0 niuu-flex-1 niuu-font-mono niuu-text-[10px] niuu-text-text-faint">
          {decision?.matchedPattern ?? permission.requestId}
        </span>
        <div className="niuu-flex niuu-gap-2">
          <button
            type="button"
            className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary hover:niuu-border-border hover:niuu-text-text-primary"
            onClick={() => respond('deny')}
          >
            deny
          </button>
          <button
            type="button"
            className="niuu-inline-flex niuu-items-center niuu-gap-1 niuu-rounded-md niuu-border niuu-border-brand niuu-bg-brand niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-bg-primary hover:niuu-bg-brand/90"
            onClick={() => respond('allow_once')}
          >
            <Check className="niuu-h-3.5 niuu-w-3.5" />
            allow
          </button>
        </div>
      </div>
    </div>
  );
}

export function PermissionApprovalPanel({
  permissions,
  onRespond,
  evaluateAutoApproval,
}: PermissionApprovalPanelProps) {
  return (
    <div
      className="niuu-grid niuu-gap-2 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary/95 niuu-p-3 niuu-shadow-[0_18px_40px_-30px_rgba(0,0,0,0.55)]"
      data-testid="permission-approval-panel"
    >
      <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-justify-between niuu-gap-2">
        <span className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary">
          <ShieldCheck className="niuu-h-3.5 niuu-w-3.5 niuu-text-brand" />
          auto approvals
        </span>
        <span className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
          Volundr policy
        </span>
      </div>

      <div className="niuu-grid niuu-gap-2">
        {permissions.map((permission) => (
          <PermissionApprovalItem
            key={permission.requestId}
            permission={permission}
            evaluateAutoApproval={evaluateAutoApproval}
            onRespond={onRespond}
          />
        ))}
      </div>
    </div>
  );
}
