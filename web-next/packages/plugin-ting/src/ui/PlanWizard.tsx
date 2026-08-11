import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import { Rune } from '@niuulabs/ui';
import type { RepoRecord } from '@niuulabs/ui';
import { PLAN_STEPS } from '../domain/plan';
import { StepDots } from './StepDots';
import { usePlanWizard } from './usePlanWizard';
import { useWorkflows } from './useWorkflows';
import { PlanPrompt } from './PlanPrompt';
import { PlanQuestions } from './PlanQuestions';
import { PlanRuns } from './PlanRuns';
import { PlanDraft } from './PlanDraft';
import { PlanApproved } from './PlanApproved';
import { PlanGuidanceRail } from './PlanGuidanceRail';
import type { ITingService, PlanSession } from '../ports';
import './PlanWizard.css';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
};

/**
 * Plan wizard — five‐step flow for decomposing a human goal into a saga.
 *
 * States: prompt → questions → running → draft → approved
 *
 * Layout: 2-column on prompt/questions/draft (left: wizard content, right: guidance rail).
 *         Full-width on running and approved (content fills the width).
 */
export function PlanWizard() {
  const navigate = useNavigate();
  const ting = useService<ITingService>('ting');
  const queryClient = useQueryClient();
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const {
    state,
    submitPrompt,
    resumePlanSession,
    submitAnswers,
    approveDraft,
    editPhase,
    removeRun,
    back,
    clearError,
    replan,
    saveDraft,
  } = usePlanWizard();
  const { data: workflows = [] } = useWorkflows();
  const { data: repos = [] } = useQuery({
    queryKey: ['ting-plan-repos'],
    queryFn: () => repoCatalog.getRepos(),
  });
  const { data: planSessions = [] } = useQuery({
    queryKey: ['ting-plan-sessions'],
    queryFn: () => (ting.listPlanSessions ? ting.listPlanSessions() : Promise.resolve([])),
    enabled: state.step === 'prompt' && Boolean(ting.listPlanSessions),
    refetchInterval: state.step === 'prompt' ? 5000 : false,
  });

  // A plan is resumable only while it is still running. The rest are history:
  // still reachable by slug, and now listed instead of only linkable.
  const RESUMABLE = ['pending', 'running', 'blocked'];
  const activePlanSessions = planSessions.filter((s) =>
    RESUMABLE.includes((s.status ?? 'running').toLowerCase()),
  );
  const completedPlanSessions = planSessions.filter(
    (s) => !RESUMABLE.includes((s.status ?? 'running').toLowerCase()),
  );

  function handleNewPlan() {
    // Navigate back to /ting/plan to start fresh (the wizard unmounts and remounts)
    window.location.href = '/ting/plan';
  }

  async function handleCancelPlanSession(session: PlanSession) {
    if (!session.campaignSlug || !ting.cancelPlanSession) return;
    if (!window.confirm(`Cancel "${session.name || session.prompt || session.campaignSlug}"?`)) {
      return;
    }
    await ting.cancelPlanSession(session.campaignSlug);
    await queryClient.invalidateQueries({ queryKey: ['ting-plan-sessions'] });
  }

  const showGuidance =
    state.step === 'prompt' || state.step === 'questions' || state.step === 'draft';

  return (
    <div className="ting-plan-shell">
      <div className="ting-plan-main">
        <div className="ting-plan-main__inner">
          <div className="ting-plan-title">
            <Rune glyph="ᚦ" size={24} />
            <div>
              <h1 className="niuu:text-base niuu:font-semibold niuu:text-text-secondary niuu:m-0">
                New saga plan
              </h1>
              <p className="ting-plan-title__copy">
                Turn a rough brief into a decomposed saga with workflow, acceptance criteria, and
                reviewable sub-tasks.
              </p>
            </div>
          </div>

          <StepDots steps={PLAN_STEPS} current={state.step} />

          {state.step === 'prompt' && (
            <>
              <ActivePlanSessions
                sessions={activePlanSessions}
                loading={state.loading}
                onResume={resumePlanSession}
                onCancel={handleCancelPlanSession}
              />
              <CompletedPlanSessions
                sessions={completedPlanSessions}
                loading={state.loading}
                onOpen={(session) =>
                  void navigate({
                    to: '/ting/plan/$slug',
                    params: { slug: session.campaignSlug ?? '' },
                  })
                }
              />
              <PlanPrompt
                onSubmit={submitPrompt}
                loading={state.loading}
                error={state.error}
                repos={repos}
              />
            </>
          )}

          {state.step === 'questions' && (
            <PlanQuestions
              questions={state.questions}
              initialAnswers={state.answers}
              prompt={state.prompt}
              workflows={workflows}
              onSubmit={submitAnswers}
              onBack={() => {
                clearError();
                back();
              }}
            />
          )}

          {state.step === 'running' && (
            <PlanRuns
              error={state.error}
              onBack={() => {
                clearError();
                back();
              }}
            />
          )}

          {state.step === 'draft' && state.structure && (
            <PlanDraft
              structure={state.structure}
              loading={state.loading}
              error={state.error}
              onApprove={approveDraft}
              onBack={() => {
                clearError();
                back();
              }}
              onReplan={replan}
              onSaveDraft={saveDraft}
              onEditPhase={editPhase}
              onRemoveRun={removeRun}
            />
          )}

          {state.step === 'approved' && state.saga && (
            <PlanApproved saga={state.saga} onNewPlan={handleNewPlan} />
          )}
        </div>
      </div>

      {showGuidance && (
        <div className="ting-plan-rail">
          <PlanGuidanceRail />
        </div>
      )}
    </div>
  );
}

function CompletedPlanSessions({
  sessions,
  loading,
  onOpen,
}: {
  sessions: PlanSession[];
  loading: boolean;
  onOpen(session: PlanSession): void;
}) {
  if (sessions.length === 0) return null;

  return (
    <section className="ting-plan-card ting-plan-sessions" aria-label="Completed planning sessions">
      <div className="ting-plan-sessions__head">
        <div>
          <h2>Completed plans</h2>
          <p>Open an approved plan and the runs it decomposed into.</p>
        </div>
        <span>{sessions.length}</span>
      </div>
      <div className="ting-plan-sessions__list">
        {sessions.map((session) => (
          <div key={session.campaignSlug ?? session.sessionId} className="ting-plan-session-row">
            <span>
              <strong>{session.name || session.prompt || session.campaignSlug || 'Plan'}</strong>
              <small>{session.repo || 'no repository selected'}</small>
            </span>
            <span className="ting-plan-session-row__actions">
              <span className="ting-plan-session-row__meta">{session.status || 'completed'}</span>
              <button
                type="button"
                disabled={loading || !session.campaignSlug}
                onClick={() => onOpen(session)}
              >
                View
              </button>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function ActivePlanSessions({
  sessions,
  loading,
  onResume,
  onCancel,
}: {
  sessions: PlanSession[];
  loading: boolean;
  onResume(session: PlanSession): void;
  onCancel(session: PlanSession): void;
}) {
  if (sessions.length === 0) return null;

  return (
    <section className="ting-plan-card ting-plan-sessions" aria-label="Active planning sessions">
      <div className="ting-plan-sessions__head">
        <div>
          <h2>Active plans</h2>
          <p>Resume a planning workflow that is already running.</p>
        </div>
        <span>{sessions.length}</span>
      </div>
      <div className="ting-plan-sessions__list">
        {sessions.map((session) => (
          <div key={session.campaignSlug ?? session.sessionId} className="ting-plan-session-row">
            <span>
              <strong>{session.name || session.prompt || session.campaignSlug || 'Plan'}</strong>
              <small>{session.repo || 'no repository selected'}</small>
            </span>
            <span className="ting-plan-session-row__actions">
              <span className="ting-plan-session-row__meta">{session.status || 'running'}</span>
              <button type="button" disabled={loading} onClick={() => onResume(session)}>
                Resume
              </button>
              <button
                type="button"
                disabled={loading || !session.campaignSlug}
                onClick={() => onCancel(session)}
              >
                Cancel
              </button>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
