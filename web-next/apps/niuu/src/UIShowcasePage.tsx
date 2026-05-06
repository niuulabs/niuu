import { PersonaAvatar, RavnAvatar, MountChip, DeployBadge, LifecycleBadge } from '@niuulabs/ui';
import type { PersonaRole } from '@niuulabs/domain';
import type { LifecycleState, DeploymentKind, MountChipRole } from '@niuulabs/ui';
import './UIShowcasePage.css';

const ROLES: PersonaRole[] = [
  'plan',
  'build',
  'verify',
  'review',
  'gate',
  'audit',
  'ship',
  'index',
  'report',
];

const LIFECYCLE_STATES: LifecycleState[] = [
  'provisioning',
  'ready',
  'running',
  'idle',
  'terminating',
  'terminated',
  'failed',
];

const DEPLOY_KINDS: DeploymentKind[] = ['k8s', 'systemd', 'pi', 'mobile', 'ephemeral'];

const MOUNT_ROLES: MountChipRole[] = ['primary', 'archive', 'ro', 'local', 'shared', 'domain'];

export function UIShowcasePage() {
  return (
    <div className="ui-showcase" data-testid="ui-showcase">
      <h1 className="ui-showcase__title">UI Composites Showcase</h1>

      <section className="ui-showcase__section" data-testid="section-persona-avatar">
        <div className="ui-showcase__section-label">PersonaAvatar — all roles</div>
        <div className="ui-showcase__row">
          {ROLES.map((role) => (
            <PersonaAvatar
              key={role}
              role={role}
              letter={(role[0] ?? '').toUpperCase()}
              size={32}
            />
          ))}
        </div>
      </section>

      <section className="ui-showcase__section" data-testid="section-ravn-avatar">
        <div className="ui-showcase__section-label">RavnAvatar — all roles</div>
        <div className="ui-showcase__row">
          {ROLES.map((role) => (
            <RavnAvatar key={role} role={role} rune="ᚱ" state="idle" size={32} />
          ))}
        </div>
      </section>

      <section className="ui-showcase__section" data-testid="section-mount-chip">
        <div className="ui-showcase__section-label">MountChip — all roles</div>
        <div className="ui-showcase__row">
          {MOUNT_ROLES.map((role) => (
            <MountChip key={role} name="knowledge-well" role={role} priority={1} />
          ))}
        </div>
      </section>

      <section className="ui-showcase__section" data-testid="section-deploy-badge">
        <div className="ui-showcase__section-label">DeployBadge — all kinds</div>
        <div className="ui-showcase__row">
          {DEPLOY_KINDS.map((d) => (
            <DeployBadge key={d} deployment={d} />
          ))}
        </div>
      </section>

      <section className="ui-showcase__section" data-testid="section-lifecycle-badge">
        <div className="ui-showcase__section-label">LifecycleBadge — all states</div>
        <div className="ui-showcase__row">
          {LIFECYCLE_STATES.map((state) => (
            <LifecycleBadge key={state} state={state} />
          ))}
        </div>
      </section>
    </div>
  );
}
