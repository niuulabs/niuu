import { useState } from 'react';

interface PlanPromptProps {
  onSubmit(prompt: string, repo: string): void;
  loading: boolean;
  error: string | null;
}

const HINT_CHIPS = [
  {
    label: '+ Example: subscription validation',
    text: 'NIU-214: subscription validation — surface dead-letter warnings when a persona in a workflow has no downstream consumer for any of its produced event types.',
  },
  {
    label: '+ Example: simple endpoint',
    text: 'Add a health check endpoint to the Tyr service that reports queue depth and active raid counts.',
  },
  {
    label: '+ Example: OIDC auth',
    text: 'Add OIDC authentication with Keycloak, including silent token refresh and PAT support for headless agents.',
  },
];

/**
 * Step 1 of the Plan wizard — capture the human‐language goal and target repo.
 */
export function PlanPrompt({ onSubmit, loading, error }: PlanPromptProps) {
  const [prompt, setPrompt] = useState('');
  const [repo, setRepo] = useState('');

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;
    onSubmit(prompt.trim(), repo.trim());
  }

  const canSubmit = prompt.trim().length > 0 && !loading;

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Plan prompt form"
      className="tyr-plan-card niuu-flex niuu-flex-col niuu-gap-4"
    >
      <div className="tyr-plan-step-head">
        <div className="tyr-plan-step-index">1</div>
        <div className="niuu-flex niuu-flex-col niuu-gap-1">
          <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
            Describe your goal
          </h2>
          <p className="niuu-text-sm niuu-text-text-secondary">
            Rough is fine. A tracker ID, sentence, or paragraph all work; the planning raid will
            sharpen it next.
          </p>
        </div>
      </div>

      <div className="niuu-flex niuu-flex-col niuu-gap-1">
        <label
          htmlFor="plan-prompt"
          className="niuu-text-sm niuu-font-medium niuu-text-text-secondary"
        >
          Goal description
          <span className="niuu-ml-1 niuu-text-critical" aria-hidden="true">
            *
          </span>
        </label>
        <textarea
          id="plan-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. Add OIDC authentication with Keycloak, including silent token refresh and PAT support for headless agents."
          rows={5}
          required
          aria-required="true"
          aria-describedby={error ? 'plan-prompt-error' : undefined}
          className="tyr-plan-input tyr-plan-textarea"
        />
        <div className="niuu-flex niuu-flex-wrap niuu-gap-2 niuu-mt-2" aria-label="Example prompts">
          {HINT_CHIPS.map((chip) => (
            <button
              key={chip.label}
              type="button"
              onClick={() => setPrompt(chip.text)}
              className="tyr-plan-hint-chip"
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>

      <div className="niuu-flex niuu-flex-col niuu-gap-1">
        <label
          htmlFor="plan-repo"
          className="niuu-text-sm niuu-font-medium niuu-text-text-secondary"
        >
          Target repository
        </label>
        <input
          id="plan-repo"
          type="text"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="e.g. niuulabs/volundr"
          className="tyr-plan-input"
        />
      </div>

      {error && (
        <p id="plan-prompt-error" role="alert" className="niuu-text-sm niuu-text-critical">
          {error}
        </p>
      )}

      <div className="niuu-flex niuu-justify-end">
        <button
          type="submit"
          disabled={!canSubmit}
          className="tyr-plan-primary-btn disabled:niuu-opacity-50 disabled:niuu-cursor-not-allowed"
        >
          {loading ? 'Starting…' : 'Next →'}
        </button>
      </div>
    </form>
  );
}
