import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { DispatchView } from './DispatchView';
import { createMockDispatcherService } from '../adapters/mock';
import { createMockDispatchBus } from '../adapters/mock';
import type { ITingService, IDispatcherService } from '../ports';
import type { Saga, Phase, Run } from '../domain/saga';
import type { DispatcherState } from '../domain/dispatcher';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function makeDispatcherState(overrides: Partial<DispatcherState> = {}): DispatcherState {
  return {
    id: '00000000-0000-0000-0000-000000000999',
    running: true,
    threshold: 70,
    maxConcurrentRuns: 3,
    autoContinue: false,
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function saga(id: string, name: string): Saga {
  return {
    id,
    trackerId: `NIU-${id}`,
    trackerType: 'linear',
    slug: name.toLowerCase().replace(/\s+/g, '-'),
    name,
    repos: ['niuulabs/volundr'],
    featureBranch: `feat/${name.toLowerCase().replace(/\s+/g, '-')}`,
    status: 'active',
    confidence: 80,
    createdAt: '2026-01-01T00:00:00Z',
    phaseSummary: { total: 2, completed: 0 },
  };
}

function phase(sagaId: string, runs: Run[], number = 1): Phase {
  return {
    id: `phase-${sagaId}-${number}`,
    sagaId,
    trackerId: `NIU-M${number}`,
    number,
    name: `Phase ${number}`,
    status: 'active',
    confidence: 80,
    runs,
  };
}

function run(
  id: string,
  name: string,
  status: Run['status'],
  confidence: number,
  phaseId: string,
): Run {
  return {
    id,
    phaseId,
    trackerId: `NIU-${id}`,
    name,
    description: 'A run in the dispatch queue.',
    acceptanceCriteria: ['AC 1', 'AC 2'],
    declaredFiles: ['src/example.ts'],
    estimateHours: 4,
    status,
    confidence,
    sessionId: status === 'running' ? `sess-${id}` : null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: status !== 'pending' ? 'feat/example' : null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  };
}

function storyWrapper(
  ting: ITingService,
  dispatcher: IDispatcherService = createMockDispatcherService(),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            ting,
            'ting.dispatcher': dispatcher,
            'ting.dispatch': createMockDispatchBus(),
          }}
        >
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

// ---------------------------------------------------------------------------
// Stories
// ---------------------------------------------------------------------------

const SAGA_1 = saga('saga-001', 'Auth Rewrite');
const PHASE_1_ID = 'phase-saga-001-1';

const READY_RUN = run('run-001', 'Implement OIDC flow', 'pending', 80, PHASE_1_ID);
const LOW_CONF_RUN = run('run-002', 'Write auth integration tests', 'pending', 45, PHASE_1_ID);
const RUNNING_RUN = run('run-003', 'Add PAT generation', 'running', 75, PHASE_1_ID);
const QUEUED_RUN = run('run-004', 'Add refresh token rotation', 'queued', 75, PHASE_1_ID);
const BLOCKED_RUN = run('run-005', 'Harden JWT validation', 'pending', 30, PHASE_1_ID);

function tingWithRuns(runs: Run[]): ITingService {
  const p = phase(SAGA_1.id, runs);
  return {
    getSagas: async () => [SAGA_1],
    getPhases: async () => [p],
    getSaga: async () => SAGA_1,
    createSaga: async () => SAGA_1,
    commitSaga: async () => SAGA_1,
    decompose: async () => [],
    spawnPlanSession: async () => ({ sessionId: 'plan-1', chatEndpoint: null }),
    extractStructure: async () => ({ found: false, structure: null }),
  };
}

const meta: Meta<typeof DispatchView> = {
  title: 'Ting/DispatchView',
  component: DispatchView,
  parameters: { layout: 'fullscreen', a11y: {} },
};
export default meta;

type Story = StoryObj<typeof DispatchView>;

/** All statuses visible in the "all" tab. */
export const AllStatuses: Story = {
  decorators: [
    (Story) => {
      const Wrapper = storyWrapper(
        tingWithRuns([READY_RUN, LOW_CONF_RUN, RUNNING_RUN, QUEUED_RUN, BLOCKED_RUN]),
      );
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

/** Only feasible runs — "ready" tab. */
export const ReadyOnly: Story = {
  decorators: [
    (Story) => {
      const Wrapper = storyWrapper(tingWithRuns([READY_RUN]));
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

/** Low confidence run — disabled dispatch with reason tooltip on the gate chip. */
export const DisabledDispatchLowConfidence: Story = {
  name: 'Disabled dispatch — low confidence',
  decorators: [
    (Story) => {
      const Wrapper = storyWrapper(tingWithRuns([LOW_CONF_RUN, BLOCKED_RUN]));
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

/** Runs in the execution queue (running + queued). */
export const QueueTab: Story = {
  name: 'Queue tab — running and queued runs',
  decorators: [
    (Story) => {
      const Wrapper = storyWrapper(tingWithRuns([RUNNING_RUN, QUEUED_RUN]));
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

/** Auto-continue on — rule card reflects the change. */
export const AutoContinueOn: Story = {
  decorators: [
    (Story) => {
      const dispatcher: IDispatcherService = {
        ...createMockDispatcherService(),
        getState: async () => makeDispatcherState({ autoContinue: true }),
      };
      const Wrapper = storyWrapper(tingWithRuns([READY_RUN]), dispatcher);
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

/** Empty state when no runs match the filter. */
export const Empty: Story = {
  decorators: [
    (Story) => {
      const Wrapper = storyWrapper(tingWithRuns([]));
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};
