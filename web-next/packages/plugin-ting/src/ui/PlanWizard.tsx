import { useQuery } from '@tanstack/react-query';
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
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const {
    state,
    submitPrompt,
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

  function handleNewPlan() {
    // Navigate back to /ting/plan to start fresh (the wizard unmounts and remounts)
    window.location.href = '/ting/plan';
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
            <PlanPrompt
              onSubmit={submitPrompt}
              loading={state.loading}
              error={state.error}
              repos={repos}
            />
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
