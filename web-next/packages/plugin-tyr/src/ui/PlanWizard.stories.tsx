import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PlanWizard } from './PlanWizard';
import { PlanPrompt } from './PlanPrompt';
import { PlanQuestions } from './PlanQuestions';
import { PlanRaiding } from './PlanRaiding';
import { PlanDraft } from './PlanDraft';
import { PlanApproved } from './PlanApproved';
import { createMockTyrService } from '../adapters/mock';
import type { ExtractedStructure } from '../ports';
import type { Saga } from '../domain/saga';

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const QUESTIONS = [
  { id: 'q1', question: 'Which target repositories?', hint: 'e.g. niuulabs/volundr' },
  { id: 'q2', question: 'Base branch?', hint: 'e.g. main' },
  { id: 'q3', question: 'Any constraints or acceptance criteria for all raids?' },
];

const STRUCTURE: ExtractedStructure = {
  found: true,
  structure: {
    name: 'Auth Rewrite',
    phases: [
      {
        name: 'Phase 1: OIDC Foundation',
        raids: [
          {
            name: 'Implement OIDC login',
            description: 'Add OIDC login via Keycloak with PKCE.',
            acceptanceCriteria: ['SSO works', 'Token refreshes silently'],
            declaredFiles: ['src/auth/oidc.ts'],
            estimateHours: 8,
            confidence: 88,
          },
        ],
      },
      {
        name: 'Phase 2: PAT Support',
        raids: [
          {
            name: 'Add PAT generation',
            description: 'Personal access tokens for headless dispatch.',
            acceptanceCriteria: ['PATs creatable', 'PATs revocable'],
            declaredFiles: ['src/niuu/pat.ts'],
            estimateHours: 4,
            confidence: 72,
          },
        ],
      },
    ],
  },
};

const APPROVED_SAGA: Saga = {
  id: 'saga-story-1',
  trackerId: 'NIU-900',
  trackerType: 'linear',
  slug: 'auth-rewrite',
  name: 'Auth Rewrite',
  repos: ['niuulabs/volundr'],
  featureBranch: 'feat/auth-rewrite',
  status: 'active',
  confidence: 80,
  createdAt: '2026-01-01T00:00:00Z',
  phaseSummary: { total: 2, completed: 0 },
};

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function withServices(story: () => React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ tyr: createMockTyrService() }}>{story()}</ServicesProvider>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// PlanWizard full flow
// ---------------------------------------------------------------------------

const wizardMeta: Meta<typeof PlanWizard> = {
  title: 'plugin-tyr/PlanWizard',
  component: PlanWizard,
  decorators: [(story) => withServices(story)],
  parameters: {
    layout: 'padded',
    backgrounds: { default: 'dark' },
  },
};

export default wizardMeta;
type WizardStory = StoryObj<typeof PlanWizard>;

export const FullFlow: WizardStory = {};

// ---------------------------------------------------------------------------
// Individual step stories
// ---------------------------------------------------------------------------

type PromptStory = StoryObj<typeof PlanPrompt>;
export const Prompt: PromptStory = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanPrompt onSubmit={() => undefined} loading={false} error={null} />
    </div>
  ),
};

export const PromptLoading: PromptStory = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanPrompt onSubmit={() => undefined} loading={true} error={null} />
    </div>
  ),
};

export const PromptError: PromptStory = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanPrompt
        onSubmit={() => undefined}
        loading={false}
        error="Service temporarily unavailable"
      />
    </div>
  ),
};

export const Questions = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanQuestions questions={QUESTIONS} onSubmit={() => undefined} onBack={() => undefined} />
    </div>
  ),
};

export const QuestionsEmpty = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanQuestions questions={[]} onSubmit={() => undefined} onBack={() => undefined} />
    </div>
  ),
};

export const Raiding = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanRaiding error={null} onBack={() => undefined} />
    </div>
  ),
};

export const RaidingError = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanRaiding
        error="Ravens lost the signal — decomposition timed out."
        onBack={() => undefined}
      />
    </div>
  ),
};

export const Draft = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanDraft
        structure={STRUCTURE}
        loading={false}
        error={null}
        onApprove={() => undefined}
        onBack={() => undefined}
        onEditPhase={() => undefined}
      />
    </div>
  ),
};

export const DraftLoading = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanDraft
        structure={STRUCTURE}
        loading={true}
        error={null}
        onApprove={() => undefined}
        onBack={() => undefined}
        onEditPhase={() => undefined}
      />
    </div>
  ),
};

export const DraftError = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanDraft
        structure={STRUCTURE}
        loading={false}
        error="Failed to commit saga — tracker is unreachable."
        onApprove={() => undefined}
        onBack={() => undefined}
        onEditPhase={() => undefined}
      />
    </div>
  ),
};

export const Approved = {
  render: () => (
    <div style={{ maxWidth: 640, padding: 24 }}>
      <PlanApproved saga={APPROVED_SAGA} onNewPlan={() => undefined} />
    </div>
  ),
};
