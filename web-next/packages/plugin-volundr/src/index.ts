import { createElement } from 'react';
import { createRoute, redirect } from '@tanstack/react-router';
import { SquareTerminal } from 'lucide-react';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { readUiMode } from '@niuulabs/shell';
import { ForgePage } from './ui/ForgePage';
import { VolundrPage } from './ui/VolundrPage';
import { SimpleLaunchPage } from './ui/SimpleLaunchPage';
import { VolundrSessionsRoute, VolundrSessionRoute, VolundrArchivedRoute } from './ui/routes';
import { LaunchCatalogPage } from './ui/LaunchCatalogPage';
import { HistoryPage } from './ui/HistoryPage';

export const volundrPlugin = definePlugin({
  id: 'volundr',
  rune: 'V',
  title: 'Völundr',
  subtitle: '',
  simple: {
    tabs: ['sessions'],
    title: 'Sessions',
    subtitle: 'coding agents in sandboxes',
    // Coding sessions: a terminal an agent works in.
    icon: createElement(SquareTerminal, { size: 17, 'aria-hidden': true }),
  },
  tabs: [
    { id: 'forge', label: 'Forge', path: '/volundr/forge' },
    { id: 'sessions', label: 'Sessions', path: '/volundr/sessions' },
    { id: 'catalog', label: 'Catalog', path: '/volundr/catalog' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr',
      beforeLoad: ({ location }) => {
        throw redirect({
          to: (readUiMode() === 'simple' ? '/volundr/sessions' : '/volundr/forge') as never,
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
      component: SimpleLaunchPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/sessions/$sessionId',
      component: VolundrSessionsRoute,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/session/$sessionId',
      component: VolundrSessionRoute,
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
  ],
});

// Adapters
export { createMockVolundrService } from './adapters/mock';
export { createMockClusterAdapter } from './adapters/mock';
export { createMockSessionStore } from './adapters/mock';
export { createMockPtyStream } from './adapters/mock';
export { createMockMetricsStream } from './adapters/mock';
export { createMockFileSystemPort } from './adapters/mock';
export { buildVolundrHttpAdapter, buildVolundrFileSystemHttpAdapter } from './adapters/http';
export { buildVolundrPtyWsAdapter, buildVolundrMetricsSseAdapter } from './adapters/streams';

// Port types
export type { IVolundrService, SessionReadOptions } from './ports/IVolundrService';
export type { IClusterAdapter } from './ports/IClusterAdapter';
export type { ISessionStore, SessionFilters, SessionLookupOptions } from './ports/ISessionStore';
export type { IPtyStream } from './ports/IPtyStream';
export type { IMetricsStream, MetricPoint } from './ports/IMetricsStream';
export type { IFileSystemPort, FileTreeNode } from './ports/IFileSystemPort';

// UI components
export { Terminal } from './ui/Terminal/Terminal';
export type { TerminalProps } from './ui/Terminal/Terminal';
export { FileTree } from './ui/FileTree/FileTree';
export type { FileTreeProps } from './ui/FileTree/FileTree';
export { FileViewer } from './ui/FileTree/FileViewer';
export type { FileViewerProps } from './ui/FileTree/FileViewer';
export { SessionDetailPage } from './ui/SessionDetailPage';
export type { SessionDetailPageProps, SessionTab } from './ui/SessionDetailPage';
export {
  LiveSessionDetailPage,
  LiveLogsTab,
  TelemetryTab,
  type LiveSessionTab,
} from './ui/LiveSessionDetailPage';
export { SessionsPage } from './ui/SessionsPage';
export { SimpleSessionsPage } from './ui/SimpleSessionsPage';
export { SimpleLaunchPage } from './ui/SimpleLaunchPage';
export { useSessionList, useSessionDetail } from './ui/hooks/useSessionStore';
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
export { canTransition, transitionSession } from './domain/session';
export { toLifecycleState } from './ui/utils/toLifecycleState';
export type { ExecEntry, ExecStatus } from './domain/exec';
export { appendExecEntry, updateExecEntry } from './domain/exec';
export type { PodSpec, Mount, MountSource, ResourceSpec, MountKind } from './domain/pod';
export type { Cluster, ClusterNode, ClusterCapacity, NodeStatus } from './domain/cluster';
export type { Quota, QuotaLimit, QuotaScope } from './domain/quota';

// Model types (lifted from web/)
export type {
  VolundrSession,
  VolundrStats,
  SessionStatus,
  VolundrFeatures,
  VolundrMessage,
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
  ForgeProject,
  SessionProjectMembership,
  SessionDefinition,
  SessionOrigin,
  ExternalSession,
  ExternalSessionHarness,
} from './models/volundr.model';

export { UserStorageSettings } from './ui/UserStorageSettings';

export { ForgeSessionSettings } from './ui/ForgeSessionSettings';
