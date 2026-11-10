import { createElement } from 'react';
import { createRoute, redirect } from '@tanstack/react-router';
import { SessionsIcon } from '@niuulabs/ui';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ForgePage } from './ui/ForgePage';
import { VolundrPage } from './ui/VolundrPage';
import { NewSessionPage } from './ui/NewSessionPage';
import { VolundrSessionsRoute, VolundrArchivedRoute, VolundrHostsRoute } from './ui/routes';
import { LaunchCatalogPage } from './ui/LaunchCatalogPage';
import { HistoryPage } from './ui/HistoryPage';
import { NotificationsPage } from './ui/notifications/NotificationsPage';
import { useUnreadNotificationCount } from './ui/hooks/useNotifications';

export const volundrPlugin = definePlugin({
  id: 'volundr',
  rune: 'V',
  // Coding sessions: a terminal an agent works in.
  icon: createElement(SessionsIcon),
  title: 'Forge',
  subtitle: 'build and execute',
  tabs: [
    { id: 'forge', label: 'Overview', path: '/volundr/forge' },
    { id: 'sessions', label: 'Sessions', path: '/volundr/sessions' },
    { id: 'catalog', label: 'Catalog', path: '/volundr/catalog' },
    {
      id: 'notifications',
      label: 'Notifications',
      path: '/volundr/notifications',
      useCount: useUnreadNotificationCount,
    },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr',
      beforeLoad: ({ location }) => {
        throw redirect({
          to: '/volundr/forge' as never,
          search: location.search as never,
        });
      },
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/forge',
      component: ForgePage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/overview',
      component: VolundrPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/sessions',
      component: VolundrSessionsRoute,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/sessions/new',
      component: NewSessionPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/sessions/$sessionId',
      component: VolundrSessionsRoute,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      // The standalone session page is gone: an old link opens the session on the Sessions
      // page, keeping its anchor (a notification) and query.
      path: '/volundr/session/$sessionId',
      beforeLoad: ({ params, location }) => {
        throw redirect({
          to: '/volundr/sessions/$sessionId' as never,
          params: { sessionId: params.sessionId } as never,
          search: location.search as never,
          hash: location.hash,
        });
      },
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/session/$sessionId/archived',
      component: VolundrArchivedRoute,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/catalog',
      component: LaunchCatalogPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/hosts',
      component: VolundrHostsRoute,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/clusters',
      beforeLoad: ({ location }) => {
        throw redirect({
          to: '/guild' as never,
          search: location.search as never,
        });
      },
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/credentials',
      beforeLoad: ({ location }) => {
        throw redirect({
          to: '/settings/credentials/user' as never,
          search: location.search as never,
        });
      },
      component: () => null,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/history',
      component: HistoryPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/notifications',
      component: NotificationsPage,
    }),
  ],
});

// Adapters
export { createMockVolundrService } from './adapters/mock';
export { createMockClusterAdapter } from './adapters/mock';
export { createMockSessionStore } from './adapters/mock';
export { createMockPtyStream } from './adapters/mock';
export { createMockMetricsStream } from './adapters/mock';
export { createMockFileSystemPort } from './adapters/mock';
export {
  buildVolundrHttpAdapter,
  buildVolundrFileSystemHttpAdapter,
  forgeApiBasePath,
} from './adapters/http';
export { buildVolundrPtyWsAdapter, buildVolundrMetricsSseAdapter } from './adapters/streams';
export { buildNotificationFeedHttpAdapter } from './adapters/notifications';
export {
  createMockNotificationFeed,
  type MockNotificationFeed,
  type MockNotificationFeedOptions,
} from './adapters/notifications.mock';
export type { VolundrHttpAdapter } from './adapters/http';

// Port types
export type {
  IVolundrService,
  SessionChangesReadOptions,
  SessionReadOptions,
  VolundrSessionChanges,
} from './ports/IVolundrService';
export type { IClusterAdapter } from './ports/IClusterAdapter';
export { RUN_GATE_SERVICE, type IRunGateService, type RunGate } from './ports/IRunGateService';
export {
  RUN_WORKFLOW_VIEW_SERVICE,
  type RunWorkflowView,
  type RunWorkflowViewProps,
} from './ports/IRunWorkflowView';
export type {
  ISessionStore,
  SessionChangeReader,
  SessionFilters,
  SessionInventoryChanges,
  SessionLookupOptions,
} from './ports/ISessionStore';
export type { IPtyStream } from './ports/IPtyStream';
export type { IMetricsStream, MetricPoint } from './ports/IMetricsStream';
export type { IFileSystemPort, FileTreeNode } from './ports/IFileSystemPort';
export type {
  INotificationFeed,
  NotificationFeedPage,
  NotificationFeedSubscriber,
  NotificationGapPage,
  NotificationStreamStatus,
} from './ports/INotificationFeed';
export type {
  ForgeEventStreamSource,
  ForgeStreamEvent,
  ForgeStreamListener,
} from './ports/IForgeEventStream';

// UI components
export { Terminal } from './ui/Terminal/Terminal';
export type { TerminalProps } from './ui/Terminal/Terminal';
export { FileTree } from './ui/FileTree/FileTree';
export type { FileTreeProps } from './ui/FileTree/FileTree';
export { FileViewer } from '@niuulabs/ui';
export type { FileViewerProps } from '@niuulabs/ui';
export { SessionDetailPage } from './ui/SessionDetailPage';
export type { SessionDetailPageProps, SessionTab } from './ui/SessionDetailPage';
export {
  LiveSessionDetailPage,
  LiveLogsTab,
  TelemetryTab,
  type LiveSessionTab,
} from './ui/LiveSessionDetailPage';
export { SessionsPage } from './ui/SessionsPage';
export { NotificationsPage } from './ui/notifications/NotificationsPage';
export {
  NOTIFICATIONS_SERVICE,
  useNotifications,
  useNotificationReadState,
  useUnreadNotificationCount,
} from './ui/hooks/useNotifications';
export { NewSessionPage } from './ui/NewSessionPage';
export { useSessionList, useSessionDetail } from './ui/hooks/useSessionStore';
export { useSharedOwnerNames, sharedProjects } from './ui/hooks/useSharedOwnerNames';
export { useSessionRead, type UseSessionRead } from './ui/hooks/useSessionRead';
export { sessionActivityTs, compareSessionsByActivity } from './ui/sessions/sessionLabels';
export {
  useQuickLaunch,
  quickLaunchName,
  quickLaunchSource,
  defaultTargetId,
  type QuickLaunchRequest,
  type QuickLaunchOptions,
} from './ui/hooks/useQuickLaunch';
export { ForgePage } from './ui/ForgePage';
export { StructuredLogViewer } from './ui/components/StructuredLogViewer';
export { useSkuldChat } from './ui/hooks/useSkuldChat';
export {
  WizardSelect,
  StepIndicator,
  SectionCard,
  RuntimePanel,
} from './ui/LaunchWizardPrimitives';
export { ConfirmRow, BootingStep } from './ui/LaunchWizardSteps';
export { LaunchWizard } from './ui/LaunchWizard';
export { QuickLaunch } from './ui/QuickLaunch';
export { useFeatures } from './ui/useFeatures';
export { formatModelOption, type RuntimeModelDescriptor } from './ui/launchWizardModel';

// Atoms
export { CliBadge } from './ui/atoms/CliBadge';
export { SourceLabel } from './ui/atoms/SourceLabel';
export { ClusterChip } from './ui/atoms/ClusterChip';
export { ModelChip } from './ui/atoms/ModelChip';

// Domain types
export type { Session, SessionState, SessionResources, SessionEvent } from './domain/session';
export {
  canTransition,
  transitionSession,
  SessionForkError,
  SessionReadStateConflictError,
} from './domain/session';
export { toLifecycleState } from './ui/utils/toLifecycleState';
export type { ExecEntry, ExecStatus } from './domain/exec';
export type {
  RuntimeReadiness,
  SessionGrant,
  SessionRuntimeKind,
  SessionRuntimeOffer,
} from './domain/sessionRuntimes';
export { appendExecEntry, updateExecEntry } from './domain/exec';
export type { PodSpec, Mount, MountSource, ResourceSpec, MountKind } from './domain/pod';
export type { Cluster, ClusterNode, ClusterCapacity, NodeStatus } from './domain/cluster';
export type { Quota, QuotaLimit, QuotaScope } from './domain/quota';
export type {
  SessionNotification,
  NotificationFilter,
  NotificationReadState,
  NotificationRule,
  NotificationRuleDraft,
  NotificationSinkOption,
  NotificationDelivery,
  NotificationDeliveryStatus,
} from './domain/notifications';
export { NotificationReadStateConflictError } from './domain/notifications';

// Model types (lifted from web/)
export type {
  VolundrSession,
  VolundrStats,
  SessionStatus,
  VolundrFeatures,
  VolundrMessage,
  SessionMessageDelivery,
  VolundrLog,
  VolundrAggregatedLog,
  VolundrLogParticipant,
  VolundrLaunchSpec,
  LaunchScope,
  SessionChronicle,
  PullRequest,
  MergeResult,
  CIStatusValue,
  PersonalAccessToken,
  CreatePATResult,
  StoredCredential,
  SecretType,
  SessionSource,
  SessionRoom,
  SessionRoomRole,
  SessionParticipant,
  SessionProjectPerson,
  ParticipantCandidate,
  ParticipantRole,
  ParticipantStatus,
  ForgeProject,
  ForgeProjectChanges,
  ProjectRole,
  MemberSkill,
  ProjectMember,
  ProjectMemberCandidate,
  ProjectMemberChange,
  SessionProjectMembership,
  TeamInstallMethod,
  TeamConfigField,
  TeamCatalogEntry,
  TeamIntegration,
  TeamRouteEvent,
  TeamDeliverTo,
  TeamRoute,
  TeamRouteChange,
  TeamIntegrationUse,
  TeamIntegrationUsagePage,
  TeamServiceAccountConnect,
  SessionDefinition,
  SessionOrigin,
  ExternalSession,
  ExternalSessionHarness,
  SessionReadState,
  SessionReadStateChange,
  SessionStartup,
  StartupStage,
  StartupStageState,
  ForgeMcpGrant,
} from './models/volundr.model';

export { UserStorageSettings } from './ui/UserStorageSettings';

// Compute hosts (bare-metal machines registered as a Forge's compute)
export { ComputeHostsPanel } from './ui/computeHosts/ComputeHostsPanel';
export { ComputeHostsPage } from './ui/computeHosts/ComputeHostsPage';
export { ForgeHostsLink, computeHostsHref } from './ui/computeHosts/ForgeHostsLink';
export { createMockComputeHosts, seedComputeHosts } from './adapters/computeHosts.mock';
export type {
  ComputeHost,
  ComputeHostChanges,
  ComputeHostsOverview,
  ComputeSummary,
  HostState,
  NewPairingCode,
  PairingCode,
  PairingCodeRequest,
  RemoveHostResult,
} from './domain/computeHosts';

export { ForgeSessionSettings } from './ui/ForgeSessionSettings';

export {
  PROJECT_ROLES,
  isSharedSession,
  isInvitation,
  canAnswerGates,
  sharedSessionLabel,
  roomRoleOfProjectRole,
  PARTICIPANT_ROLES,
} from './models/volundr.model';
