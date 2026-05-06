import { cn, PersonaAvatar } from '@niuulabs/ui';
import type { PersonaRole } from '@niuulabs/domain';
import type { PersonaSummary } from '../ports';
import { usePersonas } from './usePersonas';
import { PERSONA_ROLE_ORDER } from '../catalog';
import './PersonaList.css';

const ROLE_LABEL: Record<PersonaRole, string> = {
  arbiter: 'Arbiter',
  audit: 'Audit',
  autonomy: 'Autonomy',
  build: 'Build',
  coord: 'Coord',
  gate: 'Gate',
  index: 'Index',
  investigate: 'Investigate',
  knowledge: 'Knowledge',
  observe: 'Observe',
  plan: 'Plan',
  qa: 'QA',
  report: 'Report',
  review: 'Review',
  ship: 'Ship',
  verify: 'Verify',
  write: 'Write',
};

const ROLE_HINT: Partial<Record<PersonaRole, string>> = {
  arbiter: 'final decisions',
  audit: 'controls and review',
  autonomy: 'self-directed',
  build: 'implementation',
  coord: 'orchestration',
  gate: 'release gates',
  index: 'knowledge upkeep',
  investigate: 'root cause work',
  knowledge: 'mimir curation',
  observe: 'signals and health',
  plan: 'goal decomposition',
  qa: 'verification',
  report: 'status output',
  review: 'code scrutiny',
  ship: 'delivery',
  verify: 'acceptance',
  write: 'notes and drafts',
};

export interface PersonaListProps {
  selectedName: string | null;
  onSelect: (name: string) => void;
  onNew?: () => void;
  personas?: PersonaSummary[];
  isLoadingOverride?: boolean;
  errorOverride?: string | null;
  showFooterAction?: boolean;
  showRoleHint?: boolean;
  showMeta?: boolean;
  className?: string;
  dataTestId?: string;
}

export function PersonaList({
  selectedName,
  onSelect,
  onNew,
  personas,
  isLoadingOverride,
  errorOverride,
  showFooterAction = true,
  showRoleHint = true,
  showMeta = true,
  className,
  dataTestId = 'persona-list',
}: PersonaListProps) {
  const query = usePersonas();
  const data = personas ?? query.data;
  const isLoading = isLoadingOverride ?? query.isLoading;
  const errorMessage =
    errorOverride ??
    (query.error instanceof Error ? query.error.message : 'Failed to load personas');
  const isError = errorOverride != null || query.isError;

  if (isLoading) {
    return (
      <div
        data-testid="persona-list-loading"
        className="niuu-flex niuu-flex-col niuu-gap-1 niuu-p-3 niuu-text-sm niuu-text-text-muted"
      >
        <span>Loading personas…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div data-testid="persona-list-error" className="niuu-p-3 niuu-text-sm niuu-text-critical">
        {errorMessage}
      </div>
    );
  }

  if (!data || data.length === 0) {
    return <div className="niuu-p-3 niuu-text-sm niuu-text-text-muted">No personas found.</div>;
  }

  // Group by role, following PERSONA_ROLE_ORDER
  const byRole = new Map<PersonaRole, typeof data>();
  for (const role of PERSONA_ROLE_ORDER) {
    byRole.set(role, []);
  }
  for (const p of data) {
    const group = byRole.get(p.role);
    if (group) {
      group.push(p);
    } else {
      // Unknown role — fall back to first group
      byRole.get('plan')!.push(p);
    }
  }

  return (
    <nav
      aria-label="Personas"
      className={cn(
        'niuu-flex niuu-flex-col niuu-overflow-y-auto niuu-h-full niuu-py-2',
        className,
      )}
      data-testid={dataTestId}
    >
      {PERSONA_ROLE_ORDER.map((role) => {
        const personas = byRole.get(role) ?? [];
        if (personas.length === 0) return null;

        return (
          <div key={role} className="niuu-mb-3">
            <div
              className="rv-persona-role-header"
              data-role={role}
              data-testid={`persona-role-header-${role}`}
            >
              <div className="rv-persona-role-header__copy">
                <span className="rv-persona-role-header__title">
                  {ROLE_LABEL[role].toLowerCase()}
                </span>
                {showRoleHint && (
                  <span className="rv-persona-role-header__hint">{ROLE_HINT[role]}</span>
                )}
              </div>
              <span className="rv-persona-role-header__count">{personas.length}</span>
            </div>
            {personas.map((p) => {
              const isSelected = p.name === selectedName;
              return (
                <button
                  key={p.name}
                  type="button"
                  aria-current={isSelected ? 'page' : undefined}
                  onClick={() => onSelect(p.name)}
                  className={cn('rv-persona-row', isSelected && 'rv-persona-row--selected')}
                >
                  <PersonaAvatar role={p.role} letter={p.letter} size={24} />
                  <div className="rv-persona-row__copy">
                    <span className="rv-persona-row__name">{p.name}</span>
                    {showMeta && (
                      <span className="rv-persona-row__meta">
                        {p.isBuiltin ? 'builtin' : 'user'} · {p.iterationBudget} iter
                      </span>
                    )}
                  </div>
                  <div className="rv-persona-row__badges">
                    {!p.isBuiltin && <span className="rv-persona-usr-badge">USR</span>}
                    {p.hasOverride && <span className="rv-persona-ovr-badge">OVR</span>}
                  </div>
                </button>
              );
            })}
          </div>
        );
      })}

      {showFooterAction && (
        <div className="niuu-px-3 niuu-mt-auto niuu-pt-3 niuu-pb-2">
          <button
            type="button"
            onClick={onNew}
            data-testid="persona-new-button"
            className="niuu-w-full niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-muted niuu-border niuu-border-dashed niuu-border-border niuu-rounded-md niuu-bg-transparent niuu-cursor-pointer hover:niuu-border-brand hover:niuu-text-text-primary niuu-transition-colors"
          >
            + New persona
          </button>
        </div>
      )}
    </nav>
  );
}
