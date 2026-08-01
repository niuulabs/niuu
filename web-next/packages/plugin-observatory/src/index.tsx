import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { ObservatoryPage } from './ui/ObservatoryPage';
import { RegistryPage } from './ui/RegistryPage';
import { ObservatorySubnav } from './ui/ObservatorySubnav';
import { ObservatoryTopbar } from './ui/ObservatoryTopbar';

export const observatoryPlugin = definePlugin({
  id: 'observatory',
  rune: 'O',
  title: 'Observatory',
  subtitle: 'live topology · registry',
  tabs: [
    { id: 'topology', label: 'Topology', rune: '◎', path: '/observatory' },
    { id: 'registry', label: 'Registry', rune: 'ᛗ', path: '/observatory/registry' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/observatory',
      component: ObservatoryPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/observatory/registry',
      component: RegistryPage,
    }),
  ],
  subnav: () => <ObservatorySubnav />,
  topbarRight: () => <ObservatoryTopbar />,
});

export {
  createMockAgentDirectory,
  createMockRegistryRepository,
  createMockTopologyStream,
  createMockEventStream,
} from './adapters/mock';
export {
  buildObservatoryRegistryHttpAdapter,
  buildObservatoryTopologySseStream,
  buildObservatoryEventsSseStream,
  buildObservatoryAgentDirectoryHttpAdapter,
} from './adapters/http';
export type {
  IRegistryRepository,
  ILiveTopologyStream,
  IEventStream,
  IAgentDirectory,
} from './ports';
export { useAgent, useAgents } from './application/useAgents';
export type {
  EntityType,
  EntityTypeField,
  EntityShape,
  EntityCategory,
  Registry,
  EdgeKind,
  LayoutMode,
  LayoutScope,
  LayoutAnchor,
  LayoutHints,
  NodeStatus,
  NodeActivity,
  TopologyNode,
  TopologyEdge,
  Topology,
  Realm,
  Cluster,
  Host,
  Run,
  ObservatoryEventType,
  ObservatoryEvent,
  AgentKind,
  AgentInterface,
  AgentProvenance,
  AgentDirectoryEntry,
  AgentDirectoryWarning,
  AgentDirectorySourceHealth,
  AgentDirectorySourceStatus,
  AgentDirectoryPage,
  AgentDirectoryFilters,
} from './domain';
export { findAgentTopologyNode } from './domain';

// Overlay components — re-exported for consumers who want to embed them
export { EntityDrawer } from './ui/overlays/EntityDrawer';
export type { EntityDrawerProps } from './ui/overlays/EntityDrawer';
export { EventLog } from './ui/overlays/EventLog';
export type { EventLogProps } from './ui/overlays/EventLog';
export { ConnectionLegend } from './ui/overlays/ConnectionLegend';
export { Minimap } from './ui/overlays/Minimap';
export type { MinimapProps } from './ui/overlays/Minimap';
export { ObservatorySubnav } from './ui/ObservatorySubnav';
export { ObservatoryTopbar } from './ui/ObservatoryTopbar';
