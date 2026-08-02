import type { Topology, TopologyNode } from './index';

export type AgentKind = 'steward' | 'resident' | 'workflow-session';
export type AgentDirectorySourceStatus = 'healthy' | 'degraded' | 'failed';

/** One callable skill published by an A2A Agent Card. */
export interface AgentSkill {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
}

export interface AgentInterface {
  url: string;
  protocolBinding: string;
  protocolVersion: string;
  tenant: string;
}

export interface AgentProvenance {
  sourceAgentId: string;
  sourceInstanceId: string;
  clusterId: string;
  environmentId: string | null;
  topologyNodeId: string;
}

export interface AgentDirectoryEntry {
  id: string;
  canonicalId: string;
  sourceAgentId: string;
  sourceInstanceId: string;
  clusterId: string;
  environmentId: string | null;
  topologyNodeId: string;
  name: string;
  description: string;
  kind: AgentKind;
  cardUrl: string;
  cardVersion: string;
  cardHash: string;
  signatureVerified: boolean | null;
  signatureKeyIds: string[];
  signatureKeyFingerprints: string[];
  skillIds: string[];
  /**
   * The skills as the card describes them. Ting's skill ids are UUIDs, so a
   * name derived from the id read as spaced-out hex; the card carries the
   * real name and this is it.
   */
  skills?: AgentSkill[];
  tags: string[];
  defaultInputModes: string[];
  defaultOutputModes: string[];
  supportedInterfaces: AgentInterface[];
  capabilities: Record<string, unknown>;
  securitySchemes: Record<string, unknown>;
  securityRequirements: Array<Record<string, unknown>>;
  observedStatus: string;
  activity: string;
  lastSeen: string;
  ownerId: string | null;
  tenantId: string | null;
  visibility: string;
  provenance: AgentProvenance[];
}

export interface AgentDirectoryWarning {
  sourceInstanceId: string;
  sourceAgentId: string | null;
  code: string;
  message: string;
}

export interface AgentDirectorySourceHealth {
  instanceId: string;
  clusterId: string;
  status: AgentDirectorySourceStatus;
  revision: string;
  message: string;
}

export interface AgentDirectoryPage {
  items: AgentDirectoryEntry[];
  warnings: AgentDirectoryWarning[];
  sources: AgentDirectorySourceHealth[];
  partial: boolean;
  revision: string;
}

export interface AgentDirectoryFilters {
  skills?: string[];
  tags?: string[];
  kinds?: AgentKind[];
  statuses?: string[];
  environmentIds?: string[];
  clusterIds?: string[];
  instanceIds?: string[];
}

/** Resolve an aggregate directory entry back to the topology node it projects. */
export function findAgentTopologyNode(
  entry: Pick<AgentDirectoryEntry, 'topologyNodeId'>,
  topology: Topology,
): TopologyNode | undefined {
  return topology.nodes.find((node) => node.id === entry.topologyNodeId);
}
