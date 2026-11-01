import './PlanWizard.css';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { StateDot, type DotState } from '@niuulabs/ui';
import type { ITingService, PlanRisk, PhaseSpec } from '../ports';

/**
 * A single plan, addressable by slug.
 *
 * `/ting/plan` is a wizard: it holds the plan you are currently making in
 * component state, so a finished plan had nowhere to live and nothing to link
 * to. Every sibling surface has a detail route — research, specs, sagas — and
 * this is plan's.
 *
 * It shows the plan and the breakdown together, because they answer different
 * questions: the plan is what was decided and what it risks, the breakdown is
 * the runs Ting would commit.
 */
export function PlanDetailPage() {
  const { slug } = useParams({ from: '/ting/plan/$slug' });
  const navigate = useNavigate();
  const ting = useService<ITingService>('ting');

  const session = useQuery({
    queryKey: ['ting-plan-session', slug],
    queryFn: () => (ting.getPlanSession ? ting.getPlanSession(slug) : Promise.resolve(null)),
    enabled: Boolean(ting.getPlanSession),
  });

  const draft = useQuery({
    queryKey: ['ting-plan-draft', slug],
    queryFn: () => (ting.getPlanDraft ? ting.getPlanDraft(slug) : Promise.resolve(null)),
    enabled: Boolean(ting.getPlanDraft),
  });

  if (session.isLoading) {
    return <div className="ting-plan-detail__loading">Loading plan…</div>;
  }

  if (session.isError || !session.data) {
    return (
      <div role="alert" className="ting-plan-detail__error">
        {session.error instanceof Error ? session.error.message : 'Plan not found.'}
      </div>
    );
  }

  const plan = session.data;
  const structure = draft.data?.structure ?? null;
  const phases: PhaseSpec[] = structure?.phases ?? [];
  const risks: PlanRisk[] = structure?.risks ?? [];
  const runCount = phases.reduce((total, phase) => total + phase.runs.length, 0);

  return (
    <div className="ting-plan-detail">
      <header className="ting-plan-detail__head">
        <button
          type="button"
          className="ting-plan-detail__back"
          onClick={() => void navigate({ to: '/ting/plan' as never })}
        >
          ← Plans
        </button>
        <h1>{plan.name || structure?.name || slug}</h1>
        <span className="ting-plan-detail__status">
          <StateDot state={statusDot(plan.status)} />
          {plan.status || 'unknown'}
        </span>
      </header>

      <section className="ting-plan-card" aria-label="Plan">
        <h2>Plan</h2>
        {plan.prompt ? <p className="ting-plan-detail__prompt">{plan.prompt}</p> : null}
        <dl className="ting-plan-detail__meta">
          <dt>Repository</dt>
          <dd>{plan.repo || 'none selected'}</dd>
          <dt>Workflow</dt>
          <dd>{plan.workflowName || 'Saga Planning'}</dd>
          <dt>Slug</dt>
          <dd>{slug}</dd>
        </dl>
      </section>

      {risks.length > 0 && (
        <section className="ting-plan-card" aria-label="Known risks">
          <h2>Known risks</h2>
          <ul className="ting-plan-detail__risks">
            {risks.map((risk) => (
              <li key={`${risk.kind}-${risk.message}`}>
                <strong>{risk.kind}</strong>
                <span>{risk.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="ting-plan-card" aria-label="Run breakdown">
        <div className="ting-plan-detail__runs-head">
          <h2>Breakdown</h2>
          <span>{runCount}</span>
        </div>
        {draft.isLoading && <p>Loading breakdown…</p>}
        {!draft.isLoading && runCount === 0 && (
          <p className="ting-plan-detail__empty">
            This plan has no decomposed runs. A plan only gains them once the planning workflow has
            decomposed it.
          </p>
        )}
        {phases.map((phase) => (
          <div key={phase.name} className="ting-plan-detail__phase">
            <h3>{phase.name}</h3>
            <ol>
              {phase.runs.map((run) => (
                <li key={run.name}>
                  <strong>{run.name}</strong>
                  {run.description ? <p>{run.description}</p> : null}
                  {run.acceptanceCriteria.length > 0 && (
                    <ul className="ting-plan-detail__criteria">
                      {run.acceptanceCriteria.map((criterion) => (
                        <li key={criterion}>{criterion}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          </div>
        ))}
      </section>
    </div>
  );
}

function statusDot(status?: string | null): DotState {
  const value = (status ?? '').toLowerCase();
  if (value === 'running' || value === 'pending' || value === 'blocked') return 'running';
  if (value === 'completed') return 'merged';
  if (value === 'failed' || value === 'cancelled') return 'failed';
  return 'unknown';
}
