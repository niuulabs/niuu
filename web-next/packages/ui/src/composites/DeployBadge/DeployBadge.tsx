import { cn } from '../../utils/cn';
import './DeployBadge.css';

export type DeploymentKind = 'k8s' | 'systemd' | 'pi' | 'mobile' | 'ephemeral';

/** Unicode glyphs for deployment kinds, matching the design spec. */
export const DEPLOY_GLYPH: Record<DeploymentKind, string> = {
  k8s: '◇',
  systemd: '◈',
  pi: '◆',
  mobile: '▲',
  ephemeral: '◌',
};

export interface DeployBadgeProps {
  deployment: DeploymentKind;
  className?: string;
  title?: string;
}

/**
 * Deployment badge — glyph + label for the runtime kind where a raven lives.
 *
 * Glyphs: k8s ◇ / systemd ◈ / pi ◆ / mobile ▲ / ephemeral ◌
 */
export function DeployBadge({ deployment, className, title }: DeployBadgeProps) {
  return (
    <span
      className={cn('niuu-deploy-badge', `niuu-deploy-badge--${deployment}`, className)}
      title={title ?? deployment}
      aria-label={title ?? deployment}
    >
      <span className="niuu-deploy-badge__glyph" aria-hidden>
        {DEPLOY_GLYPH[deployment]}
      </span>
      <span className="niuu-deploy-badge__label">{deployment}</span>
    </span>
  );
}
