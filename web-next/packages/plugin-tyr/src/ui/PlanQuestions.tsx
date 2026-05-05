import { useState } from 'react';
import type { ClarifyingQuestion } from '../domain/plan';
import type { Workflow } from '../domain/workflow';

interface PlanQuestionsProps {
  questions: ClarifyingQuestion[];
  initialAnswers?: Record<string, string>;
  /** The user's original prompt — shown as a "YOUR BRIEF" quote card. */
  prompt?: string;
  /**
   * Workflow templates for the workflow-kind question picker.
   * Fetched by the parent (PlanWizard) and passed down to keep this component service-free.
   */
  workflows?: Workflow[];
  onSubmit(answers: Record<string, string>): void;
  onBack(): void;
}

/**
 * Step 2 of the Plan wizard — answer clarifying questions from the planning raven.
 *
 * Renders a "YOUR BRIEF" quote card above the questions, and supports a special
 * `kind: 'workflow'` question type that renders a 3-column template picker grid.
 */
export function PlanQuestions({
  questions,
  initialAnswers = {},
  prompt,
  workflows = [],
  onSubmit,
  onBack,
}: PlanQuestionsProps) {
  const [answers, setAnswers] = useState<Record<string, string>>(initialAnswers);

  function handleChange(id: string, value: string) {
    setAnswers((prev) => ({ ...prev, [id]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit(answers);
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Clarifying questions form"
      className="tyr-plan-card niuu-flex niuu-flex-col niuu-gap-4"
    >
      <div className="tyr-plan-step-head">
        <div className="tyr-plan-step-index">2</div>
        <div className="niuu-flex niuu-flex-col niuu-gap-1">
          <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
            Clarify your plan
          </h2>
          <p className="niuu-text-sm niuu-text-text-secondary">
            Sharpen the planning raid&apos;s output. Skip anything optional and the draft will
            surface assumptions.
          </p>
        </div>
      </div>

      {prompt && (
        <div className="tyr-plan-quote" aria-label="Your brief">
          <p className="niuu-text-xs niuu-font-semibold niuu-text-text-muted niuu-uppercase niuu-tracking-wider niuu-font-mono niuu-mb-1">
            Your Brief
          </p>
          <p className="niuu-text-xs niuu-text-text-secondary niuu-leading-relaxed">{prompt}</p>
        </div>
      )}

      {questions.length === 0 && (
        <p className="niuu-text-sm niuu-text-text-muted niuu-italic">
          No clarifying questions — you can proceed directly.
        </p>
      )}

      <ol className="niuu-flex niuu-flex-col niuu-gap-4 niuu-list-none niuu-p-0 niuu-m-0">
        {questions.map((q, idx) => (
          <li key={q.id} className="niuu-flex niuu-flex-col niuu-gap-1">
            <label
              htmlFor={`q-${q.id}`}
              className="niuu-text-sm niuu-font-medium niuu-text-text-primary"
            >
              <span className="niuu-text-text-muted niuu-mr-2">{idx + 1}.</span>
              {q.question}
            </label>
            {q.hint && (
              <p className="niuu-text-xs niuu-text-text-muted" id={`q-${q.id}-hint`}>
                {q.hint}
              </p>
            )}
            {q.kind === 'workflow' ? (
              <div
                role="group"
                aria-label="Workflow template picker"
                className="niuu-grid niuu-grid-cols-3 niuu-gap-2 niuu-mt-1"
              >
                {workflows.map((wf) => {
                  const stageCount = wf.nodes.filter((n) => n.kind === 'stage').length;
                  const isSelected = answers[q.id] === wf.id;
                  return (
                    <button
                      key={wf.id}
                      type="button"
                      onClick={() => handleChange(q.id, wf.id)}
                      aria-pressed={isSelected}
                      className={[
                        'tyr-plan-workflow-chip',
                        isSelected ? 'tyr-plan-workflow-chip--selected' : '',
                      ].join(' ')}
                    >
                      <div className="niuu-text-xs niuu-font-medium niuu-text-text-primary">
                        {wf.name}
                      </div>
                      <div className="niuu-text-xs niuu-text-text-muted niuu-font-mono niuu-mt-1">
                        {stageCount} stage{stageCount !== 1 ? 's' : ''}
                      </div>
                    </button>
                  );
                })}
                {workflows.length === 0 && (
                  <p className="niuu-col-span-3 niuu-text-xs niuu-text-text-muted niuu-italic">
                    No workflow templates available.
                  </p>
                )}
              </div>
            ) : (
              <input
                id={`q-${q.id}`}
                type="text"
                value={answers[q.id] ?? ''}
                onChange={(e) => handleChange(q.id, e.target.value)}
                aria-describedby={q.hint ? `q-${q.id}-hint` : undefined}
                placeholder="Your answer (optional)"
                className="tyr-plan-input"
              />
            )}
          </li>
        ))}
      </ol>

      <div className="niuu-flex niuu-justify-between">
        <button type="button" onClick={onBack} className="tyr-plan-secondary-btn">
          ← Back
        </button>
        <button type="submit" className="tyr-plan-primary-btn">
          Decompose →
        </button>
      </div>
    </form>
  );
}
