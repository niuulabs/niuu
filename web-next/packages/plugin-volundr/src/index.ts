import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ForgePage } from './ui/ForgePage';
import { VolundrPage } from './ui/VolundrPage';
import { SessionsPage } from './ui/SessionsPage';
import { VolundrSessionRoute, VolundrArchivedRoute } from './ui/routes';
import { TemplatesPage } from './ui/TemplatesPage';
import { ClustersPage } from './ui/ClustersPage';
import { CredentialsPage } from './ui/CredentialsPage';
import { HistoryPage } from './ui/HistoryPage';

export const volundrPlugin = definePlugin({
  id: 'volundr',
  rune: 'ᚲ',
  title: 'Völundr',
  subtitle: 'session forge · remote dev pods',
  tabs: [
    { id: 'sessions', label: 'Sessions', path: '/volundr' },
    { id: 'templates', label: 'Templates', path: '/volundr/templates' },
    { id: 'credentials', label: 'Credentials', path: '/volundr/credentials' },
    { id: 'clusters', label: 'Clusters', path: '/volundr/clusters' },
    { id: 'forge', label: 'Forge', path: '/volundr/forge' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr',
      component: SessionsPage,
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
      component: SessionsPage,
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
      path: '/volundr/templates',
      component: TemplatesPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/clusters',
      component: ClustersPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/volundr/credentials',
      component: CredentialsPage,
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
export { createMockTemplateStore } from './adapters/mock';
export { createMockPtyStream } from './adapters/mock';
export { createMockMetricsStream } from './adapters/mock';
export { createMockFileSystemPort } from './adapters/mock';
export { buildVolundrHttpAdapter, buildVolundrFileSystemHttpAdapter } from './adapters/http';
export { buildVolundrPtyWsAdapter, buildVolundrMetricsSseAdapter } from './adapters/streams';

// Port types
export type { IVolundrService } from './ports/IVolundrService';
export type { IClusterAdapter } from './ports/IClusterAdapter';
export type { ISessionStore, SessionFilters } from './ports/ISessionStore';
export type { ITemplateStore } from './ports/ITemplateStore';
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
export { SessionsPage } from './ui/SessionsPage';
export { ForgePage } from './ui/ForgePage';
export { CredentialsPage } from './ui/CredentialsPage';

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
export type { Template } from './domain/template';
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
  VolundrTemplate,
  VolundrPreset,
  SessionChronicle,
  PullRequest,
  MergeResult,
  CIStatusValue,
  PersonalAccessToken,
  CreatePATResult,
  StoredCredential,
  SecretType,
  SessionSource,
  SessionDefinition,
} from './models/volundr.model';
