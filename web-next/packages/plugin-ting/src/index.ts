import { createElement } from 'react';
import { createRoute, redirect } from '@tanstack/react-router';
import { Workflow } from 'lucide-react';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { readUiMode } from '@niuulabs/shell';
import { TingPage } from './ui/TingPage';
import { WorkflowBuilderPage } from './ui/WorkflowBuilderPage';
import { WorkflowsRoute } from './ui/WorkflowsRoute';
import { SagasPage } from './ui/SagasPage';
import { DispatchView } from './ui/DispatchView';
import { TingTopbar } from './ui/TingTopbar';
import { TingFooter } from './ui/TingFooter';
import { PlanWizard } from './ui/PlanWizard';
import { PlanDetailPage } from './ui/PlanDetailPage';
import { TingSubnav } from './ui/TingSubnav';
import { ResearchCenterPage } from './ui/ResearchCenterPage';
import { ResearchNewPage } from './ui/ResearchNewPage';
import { ResearchCampaignPage } from './ui/ResearchCampaignPage';
import { SpecsCenterPage } from './ui/SpecsCenterPage';
import { SpecsNewPage } from './ui/SpecsNewPage';
import { SpecsCampaignPage } from './ui/SpecsCampaignPage';
import { WorkflowExecutionsPage } from './ui/WorkflowExecutionsPage';

export {
  buildWorkflowExecutionHttpAdapter,
  buildDeliveryExecutionHttpAdapter,
} from './adapters/workflowExecution';
export type { IWorkflowExecutionService, IDeliveryExecutionService } from './ports';
export { WorkflowExecutionGraph } from './ui/WorkflowExecutionGraph';
export type { WorkflowExecutionGraphProps } from './ui/WorkflowExecutionGraph';
export type * from './domain/workflowExecutionGraph';
export type * from './domain/workflowExecutionTrace';
export type {
  WaitObservation,
  WorkflowWait,
  WorkflowExecution,
  WorkflowExecutionLaunch,
  DeliveryExecution,
  DeliveryExecutionLaunch,
  WorkflowChildExecution,
  DeliveryChildExecution,
  DeliveryRemoteCheck,
} from './domain/workflowExecution';
export { buildWorkflowExecutionResultsMarkdown } from './application/workflowExecutionResults';
export { WorkflowResults } from './ui/WorkflowResults';
export type { WorkflowResultsContextItem, WorkflowResultsProps } from './ui/WorkflowResults';

const LEGACY_SETTINGS_SECTION_TARGETS: Record<string, string> = {
  general: '/settings/ting/general',
  dispatch: '/settings/ting/dispatch',
  flock: '/settings/ting/flock',
  integrations: '/settings/integrations/connections',
  notifications: '/settings/ting/notifications',
};

function resolveLegacySettingsTarget(sectionId?: string): string {
  if (!sectionId) return '/settings/ting';
  return LEGACY_SETTINGS_SECTION_TARGETS[sectionId] ?? '/settings/ting';
}

export const tingPlugin = definePlugin({
  id: 'ting',
  rune: 'T',
  title: 'Ting',
  subtitle: 'sagas · runs · dispatch',
  simple: {
    // Simple mode is one screen: the workflow, its gates, and its runs.
    tabs: ['workflows'],
    title: 'Workflows',
    subtitle: 'stages with gates you approve',
    icon: createElement(Workflow, { size: 17, 'aria-hidden': true }),
  },
  tabs: [
    { id: 'dashboard', label: 'Dashboard', rune: '◈', path: '/ting' },
    { id: 'sagas', label: 'Sagas', rune: '✦', path: '/ting/sagas' },
    { id: 'dispatch', label: 'Dispatch', rune: '⇥', path: '/ting/dispatch' },
    { id: 'plan', label: 'Plan', rune: '◇', path: '/ting/plan' },
    { id: 'research', label: 'Research', rune: '⌁', path: '/ting/research' },
    { id: 'specs', label: 'Specs', rune: '▤', path: '/ting/specs' },
    { id: 'workflows', label: 'Workflows', rune: '⚙', path: '/ting/workflows' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting',
      beforeLoad: () => {
        // Simple mode has no dashboard: workflows are the whole surface.
        if (readUiMode() === 'simple') {
          throw redirect({ to: '/ting/workflows' as never });
        }
      },
      component: TingPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/workflows',
      component: WorkflowsRoute,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/workflows/runs',
      component: WorkflowExecutionsPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/workflows/build',
      component: WorkflowBuilderPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/research',
      component: ResearchCenterPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/research/new',
      component: ResearchNewPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/research/$slug',
      component: ResearchCampaignPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/specs',
      component: SpecsCenterPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/specs/new',
      component: SpecsNewPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/specs/$slug',
      component: SpecsCampaignPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/sagas',
      component: SagasPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/sagas/$sagaId',
      component: SagasPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/dispatch',
      component: DispatchView,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/settings',
      beforeLoad: ({ location }) => {
        throw redirect({
          to: resolveLegacySettingsTarget() as never,
          // Preserve the active profile selector when old settings links bounce
          // into the unified settings shell.
          search: location.search as never,
        });
      },
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/settings/$sectionId',
      beforeLoad: ({ params, location }) => {
        throw redirect({
          to: resolveLegacySettingsTarget(params.sectionId) as never,
          search: location.search as never,
        });
      },
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/plan',
      component: PlanWizard,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ting/plan/$slug',
      component: PlanDetailPage,
    }),
  ],
  subnav: () => TingSubnav(),
  topbarRight: () => TingTopbar(),
  footer: () => TingFooter(),
});

// Mock adapters
export {
  createMockTingService,
  createMockDispatcherService,
  createMockTingSessionService,
  createMockTrackerService,
  createMockWorkflowService,
  createMockResearchService,
  createMockSpecsService,
  createMockDispatchBus,
  createMockTingSettingsService,
  createMockAuditLogService,
} from './adapters/mock';

// HTTP adapters
export {
  buildTingHttpAdapter,
  buildDispatcherHttpAdapter,
  buildTingSessionHttpAdapter,
  buildTrackerHttpAdapter,
  buildWorkflowHttpAdapter,
  buildResearchHttpAdapter,
  buildSpecsHttpAdapter,
  buildDispatchBusHttpAdapter,
  buildTingSettingsHttpAdapter,
  buildTingAuditLogHttpAdapter,
} from './adapters/http';

// Port interfaces + request/response types
export type {
  ITingService,
  IDispatcherService,
  ITingSessionService,
  ITrackerBrowserService,
  IWorkflowService,
  WorkflowLaunchRequest,
  WorkflowLaunchResult,
  WorkflowExport,
  WorkflowExportFormat,
  WorkflowPersonaImportStatus,
  WorkflowPersonaImportPreview,
  WorkflowImportRequirement,
  WorkflowImportSource,
  WorkflowImportPreview,
  IResearchService,
  ISpecsService,
  CreateResearchCampaignRequest,
  CreateSpecCampaignRequest,
  ReviewSpecCampaignRequest,
  UpdateResearchCampaignRequest,
  IDispatchBus,
  DispatchResult,
  ITingSettingsService,
  IAuditLogService,
  ITingPersonaViewService,
  TingPersonaSummary,
  TingPersonaDetail,
  CommitSagaRequest,
  PlanSession,
  RunSpec,
  PhaseSpec,
  ExtractedStructure,
  FlockConfig,
  DispatchDefaults,
  RetryPolicy,
  NotificationSettings,
  NotificationChannel,
  AuditEntry,
  AuditEntryKind,
  AuditFilter,
  // Re-exported domain types
  Saga,
  Phase,
  DispatcherState,
  DispatchRule,
  SessionInfo,
  TingSessionStatus,
  TrackerProject,
  TrackerMilestone,
  TrackerIssue,
  RepoInfo,
  ResearchCampaign,
  ResearchCampaignDetail,
  SpecCampaign,
  SpecCampaignDetail,
  CampaignArtifact,
  CampaignArtifactDetail,
  CampaignStageState,
} from './ports';

export { WorkflowRevisionConflictError } from './ports';

// Application layer — feasibility engine
export {
  checkFeasibility,
  checkRavenResolution,
  checkConfidence,
  checkUpstreamBlocked,
  checkClusterHealth,
  type FeasibilityGateName,
  type FeasibilityGate,
  type FeasibilityResult,
  type FeasibilityContext,
} from './application/dispatch-feasibility';

// Domain types (schemas + value objects)
export {
  sagaStatusSchema,
  phaseStatusSchema,
  runStatusSchema,
  confidenceEventTypeSchema,
  sagaPhaseSummarySchema,
  sagaSchema,
  runSchema,
  phaseSchema,
  confidenceEventSchema,
  type SagaStatus,
  type PhaseStatus,
  type RunStatus,
  type ConfidenceEventType,
  type SagaPhaseSummary,
  type Run,
  type ConfidenceEvent,
} from './domain/saga';

export {
  workflowNodeKindSchema,
  workflowStageNodeSchema,
  workflowGateNodeSchema,
  workflowCondNodeSchema,
  workflowWaitNodeSchema,
  workflowNodeSchema,
  workflowEdgeSchema,
  workflowSchema,
  workflowPersonaDependencySchema,
  workflowRequirementSchema,
  validateWorkflow,
  WorkflowValidationError,
  subworkflowNodes,
  workflowRequiresDeliveryPack,
  type WorkflowNodeKind,
  type WorkflowStageNode,
  type WorkflowGateNode,
  type WorkflowCondNode,
  type WorkflowWaitNode,
  type WorkflowSubworkflowNode,
  type WorkflowNode,
  type WorkflowEdge,
  type Workflow,
  type WorkflowPersonaDependency,
  type WorkflowRequirement,
} from './domain/workflow';

export {
  WORKFLOW_SCHEMA_VERSION,
  toPortableWorkflowDocument,
  serializePortableWorkflow,
  type PortableWorkflowDocument,
} from './domain/workflowPortable';

export {
  researchCampaignStatusSchema,
  campaignStageStateSchema,
  campaignArtifactSchema,
  researchCampaignSchema,
  researchCampaignDetailSchema,
  campaignArtifactDetailSchema,
} from './domain/research';

export { dispatcherStateSchema, dispatchRuleSchema } from './domain/dispatcher';

export {
  actionableSpecGates,
  activeStageLabel,
  pendingSpecGates,
  pendingWorkflowGates,
  progressPercent,
  statusForCampaign,
  type CampaignProgressInput,
  type CampaignReviewStatus,
  type PendingWorkflowGate,
} from './domain/campaignProgress';

export { topologicalSort, detectCycle } from './domain/topologicalSort';
export type { TopologicalLayer } from './domain/topologicalSort';

export { validateWorkflowFull } from './domain/workflowValidation';
export type { WorkflowIssue, WorkflowIssueKind } from './domain/workflowValidation';

// WorkflowBuilder UI
export { WorkflowBuilder } from './ui/WorkflowBuilder';
export { WorkflowImportDialog, type WorkflowImportDialogProps } from './ui/WorkflowImportDialog';
export { WorkflowLaunchModal, type WorkflowLaunchModalProps } from './ui/WorkflowLaunchModal';
export {
  WorkflowLaunchForm,
  useWorkflowLaunchDraft,
  workflowLaunchRequest,
  type WorkflowLaunchDraft,
  type WorkflowLaunchFormProps,
} from './ui/WorkflowLaunchForm';
export { WorkflowStrip, gateWaitsForPerson, type WorkflowStripProps } from './ui/WorkflowStrip';
export { SimpleWorkflowsPage } from './ui/SimpleWorkflowsPage';
export { WorkflowCard } from './ui/WorkflowCard';
export { StageProgressRail } from './ui/StageProgressRail';
export { StepDots } from './ui/StepDots';
export { useSagas } from './ui/useSagas';
export {
  useWorkflows,
  useWorkflow,
  useLaunchWorkflow,
  useExportWorkflow,
  usePreviewWorkflowImport,
  useApplyWorkflowImport,
} from './ui/useWorkflows';

export {
  PLAN_STEPS,
  PLAN_STEP_LABELS,
  planTransition,
  canTransition,
  stepIndex,
  PlanTransitionError,
  type PlanStep,
  type ClarifyingQuestion,
} from './domain/plan';

export { tingSessionStatusSchema, sessionInfoSchema } from './domain/session';

export {
  trackerProjectSchema,
  trackerMilestoneSchema,
  trackerIssueSchema,
  repoInfoSchema,
  type RepoInfo as TrackerRepoInfo,
} from './domain/tracker';

export {
  flockConfigSchema,
  dispatchDefaultsSchema,
  retryPolicySchema,
  notificationSettingsSchema,
  notificationChannelSchema,
  auditEntrySchema,
  auditEntryKindSchema,
  auditFilterSchema,
} from './domain/settings';
