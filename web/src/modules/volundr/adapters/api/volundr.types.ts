/**
 * Volundr API Response Types
 *
 * These types match the OpenAPI specification from the backend.
 * They are transformed to UI models in the adapter.
 */

/**
 * Session status enum from API
 */
export type ApiSessionStatus =
  | 'created'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'failed';

/**
 * Session response from API
 */
export interface ApiSessionSource {
  type: 'git' | 'local_mount';
  repo?: string;
  branch?: string;
  local_path?: string;
  paths?: Array<{ host_path: string; mount_path: string; read_only: boolean }>;
  node_selector?: Record<string, string>;
}

export interface ApiSessionResponse {
  id: string;
  name: string;
  model: string;
  source: ApiSessionSource;
  status: ApiSessionStatus;
  chat_endpoint: string | null;
  code_endpoint: string | null;
  created_at: string;
  updated_at: string;
  last_active: string;
  message_count: number;
  tokens_used: number;
  pod_name: string | null;
  error: string | null;
  task_type?: string | null;
  activity_state?: 'active' | 'idle' | 'tool_executing' | null;
  tracker_issue_id?: string | null;
  issue_tracker_url?: string | null;
  owner_id?: string | null;
  tenant_id?: string | null;
}

/**
 * Identity response from /api/v1/me
 */
export interface ApiIdentityResponse {
  user_id: string;
  email: string;
  tenant_id: string;
  roles: string[];
  display_name: string;
  status: string;
}

/**
 * User response from /api/v1/users
 */
export interface ApiUserResponse {
  id: string;
  email: string;
  display_name: string;
  status: string;
  created_at?: string;
}

/**
 * Tenant response from /api/v1/tenants
 */
export interface ApiTenantResponse {
  id: string;
  path: string;
  name: string;
  parent_id: string | null;
  tier: string;
  max_sessions: number;
  max_storage_gb: number;
  created_at: string | null;
}

/**
 * Session create request
 */
export interface ApiSessionCreate {
  name: string;
  model: string;
  source: ApiSessionSource;
  template_name?: string | null;
  task_type?: string | null;
  definition?: string | null;
  terminal_restricted?: boolean;
  workspace_id?: string | null;
  credential_names?: string[];
  integration_ids?: string[];
  resource_config?: Record<string, string | undefined>;
  system_prompt?: string;
  initial_prompt?: string;
  issue_id?: string | null;
  issue_url?: string | null;
}

/**
 * Session definition response from API
 */
export interface ApiSessionDefinitionResponse {
  key: string;
  display_name: string;
  description: string;
  labels: string[];
  default_model: string;
}

/**
 * Resource type from cluster discovery
 */
export interface ApiResourceType {
  name: string;
  resource_key: string;
  display_name: string;
  unit: string;
  category: string;
}

/**
 * Node resource summary from cluster discovery
 */
export interface ApiNodeResourceSummary {
  name: string;
  labels: Record<string, string>;
  allocatable: Record<string, string>;
  allocated: Record<string, string>;
  available: Record<string, string>;
}

/**
 * Cluster resource discovery response
 */
export interface ApiClusterResourceInfo {
  resource_types: ApiResourceType[];
  nodes: ApiNodeResourceSummary[];
}

/**
 * Session update request
 */
export interface ApiSessionUpdate {
  name?: string | null;
  model?: string | null;
  branch?: string | null;
}

/**
 * Model info from API
 */
export interface ApiModelInfo {
  id: string;
  name: string;
  description: string;
  provider: 'cloud' | 'local';
  tier: 'frontier' | 'balanced' | 'execution' | 'reasoning';
  color: string;
  cost_per_million_tokens?: number | null;
  vram_required?: string | null;
}

/**
 * Repo info from API
 */
export interface ApiRepoInfo {
  provider: string;
  org: string;
  name: string;
  clone_url?: string;
  url: string;
  default_branch: string;
  branches: string[];
}

/**
 * Repos response from API — keyed by provider group name
 */
export type ApiReposResponse = Record<string, ApiRepoInfo[]>;

/**
 * Stats response from API (if/when available)
 */
export interface ApiStatsResponse {
  active_sessions: number;
  total_sessions: number;
  tokens_today: number;
  local_tokens: number;
  cloud_tokens: number;
  cost_today: number;
}

/**
 * Message response from API
 */
export interface ApiMessageResponse {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  tokens_in?: number | null;
  tokens_out?: number | null;
  latency_ms?: number | null;
}

/**
 * Message create request
 */
export interface ApiMessageCreate {
  content: string;
}

/**
 * Log entry from API
 */
export interface ApiLogResponse {
  id: string;
  session_id: string;
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  source: string;
  message: string;
}

/**
 * SSE Event Types from the Volundr stream endpoint
 */
export type VolundrSSEEventType =
  | 'session_created'
  | 'session_updated'
  | 'session_deleted'
  | 'stats_updated'
  | 'message_received'
  | 'log_received'
  | 'chronicle_event'
  | 'heartbeat';

/**
 * SSE session event payload (session_created, session_updated)
 */
export interface SSESessionPayload {
  id: string;
  name: string;
  model: string;
  /** Flat repo/branch fields (legacy) */
  repo?: string;
  branch?: string;
  /** Nested source object (when present) */
  source?: ApiSessionSource;
  status: ApiSessionStatus;
  chat_endpoint: string | null;
  code_endpoint: string | null;
  created_at: string;
  updated_at: string;
  last_active: string;
  message_count: number;
  tokens_used: number;
  pod_name: string | null;
  error: string | null;
  tracker_issue_id?: string | null;
  issue_tracker_url?: string | null;
  task_type?: string | null;
  activity_state?: 'active' | 'idle' | 'tool_executing' | null;
  owner_id?: string | null;
  tenant_id?: string | null;
}

/**
 * SSE session deleted event payload
 */
export interface SSESessionDeletedPayload {
  id: string;
}

/**
 * SSE stats updated event payload
 */
export interface SSEStatsPayload {
  active_sessions: number;
  total_sessions: number;
  tokens_today: number;
  local_tokens: number;
  cloud_tokens: number;
  cost_today: number;
}

/**
 * SSE message event payload
 */
export interface SSEMessagePayload {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  tokens_in?: number | null;
  tokens_out?: number | null;
  latency_ms?: number | null;
}

/**
 * SSE log event payload
 */
export interface SSELogPayload {
  id: string;
  session_id: string;
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  source: string;
  message: string;
}

/**
 * Chronicle event from API
 */
export interface ApiChronicleEvent {
  t: number;
  type: 'message' | 'file' | 'git' | 'terminal' | 'error' | 'session';
  label: string;
  tokens?: number | null;
  action?: string | null;
  ins?: number | null;
  del?: number | null;
  hash?: string | null;
  exit?: number | null;
}

/**
 * Chronicle file summary from API
 */
export interface ApiChronicleFile {
  path: string;
  status: 'new' | 'mod' | 'del';
  ins: number;
  del: number;
}

/**
 * Chronicle commit from API
 */
export interface ApiChronicleCommit {
  hash: string;
  msg: string;
  time: string;
}

/**
 * Session chronicle timeline response from API
 */
export interface ApiChronicleResponse {
  events: ApiChronicleEvent[];
  files: ApiChronicleFile[];
  commits: ApiChronicleCommit[];
  token_burn: number[];
}

/**
 * Template response from API (merged with profile fields)
 */
export interface ApiTemplateResponse {
  name: string;
  description: string;
  is_default: boolean;
  // Workspace
  repos: Record<string, unknown>[];
  setup_scripts: string[];
  workspace_layout: Record<string, unknown>;
  // Runtime
  cli_tool: string;
  workload_type: string;
  model: string | null;
  system_prompt: string | null;
  resource_config: Record<string, string | undefined>;
  mcp_servers: Array<{
    name: string;
    type: string;
    command?: string;
    url?: string;
    args?: string[];
  }>;
  env_vars: Record<string, string>;
  env_secret_refs: string[];
  workload_config: Record<string, string | number | boolean | undefined>;
  // Terminal sidecar
  terminal_sidecar: { enabled: boolean; allowed_commands: string[] };
  // Skills & Rules
  skills: Array<{ name: string; path?: string; inline?: string }>;
  rules: Array<{ path?: string; inline?: string }>;
}

/**
 * Preset response from API
 */
export interface ApiPresetResponse {
  id: string;
  name: string;
  description: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  cli_tool: string;
  workload_type: string;
  model: string | null;
  system_prompt: string | null;
  resource_config: Record<string, string | undefined>;
  mcp_servers: Array<{
    name: string;
    type: string;
    command?: string;
    url?: string;
    args?: string[];
  }>;
  terminal_sidecar: { enabled: boolean; allowed_commands: string[] };
  skills: Array<{ name: string; path?: string; inline?: string }>;
  rules: Array<{ path?: string; inline?: string }>;
  env_vars: Record<string, string>;
  env_secret_refs: string[];
  source: ApiSessionSource | null;
  integration_ids: string[];
  setup_scripts: string[];
  workload_config: Record<string, string | number | boolean | undefined>;
}

/**
 * Preset create request
 */
export interface ApiPresetCreate {
  name: string;
  description: string;
  is_default?: boolean;
  cli_tool: string;
  workload_type: string;
  model: string | null;
  system_prompt: string | null;
  resource_config: Record<string, string | undefined>;
  mcp_servers: Array<{
    name: string;
    type: string;
    command?: string;
    url?: string;
    args?: string[];
  }>;
  terminal_sidecar: { enabled: boolean; allowed_commands: string[] };
  skills: Array<{ name: string; path?: string; inline?: string }>;
  rules: Array<{ path?: string; inline?: string }>;
  env_vars: Record<string, string>;
  env_secret_refs: string[];
  source: ApiSessionSource | null;
  integration_ids: string[];
  setup_scripts: string[];
  workload_config: Record<string, string | number | boolean | undefined>;
}

/**
 * Preset update request (same as create but all fields optional)
 */
export type ApiPresetUpdate = Partial<ApiPresetCreate>;

/**
 * MCP server config from API
 */
export interface ApiMcpServerConfig {
  name: string;
  type: string;
  command?: string;
  url?: string;
  args?: string[];
}

/**
 * Secret creation response
 */
export interface ApiCreateSecretResponse {
  name: string;
  keys: string[];
}

/**
 * SSE chronicle event payload
 */
export interface SSEChroniclePayload {
  session_id: string;
  event: ApiChronicleEvent;
  files: ApiChronicleFile[];
  commits: ApiChronicleCommit[];
  token_burn: number[];
}

/**
 * Pull request response from API
 */
export interface ApiPullRequestResponse {
  number: number;
  title: string;
  url: string;
  repo_url: string;
  provider: string;
  source_branch: string;
  target_branch: string;
  status: string;
  description?: string | null;
  ci_status?: string | null;
  review_status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/**
 * PR create request
 */
export interface ApiPRCreateRequest {
  session_id: string;
  title?: string | null;
  target_branch?: string;
}

/**
 * PR merge request
 */
export interface ApiPRMergeRequest {
  merge_method?: string;
}

/**
 * Merge result response from API
 */
export interface ApiMergeResultResponse {
  merged: boolean;
}

/**
 * CI status response from API
 */
export interface ApiCIStatusResponse {
  status: string;
}

/**
 * Credential response from secrets API
 */
export interface ApiCredentialResponse {
  name: string;
  keys: string[];
}

/**
 * Credential list response from secrets API
 */
export interface ApiCredentialListResponse {
  credentials: ApiCredentialResponse[];
}

/**
 * Stored credential response from the pluggable credential store
 */
export interface ApiStoredCredentialResponse {
  id: string;
  name: string;
  secret_type: string;
  keys: string[];
  metadata: Record<string, string>;
  created_at: string;
  updated_at: string;
}

/**
 * Stored credential list response
 */
export interface ApiStoredCredentialListResponse {
  credentials: ApiStoredCredentialResponse[];
}

/**
 * Workspace response from API
 */
export interface ApiWorkspaceResponse {
  id: string;
  pvc_name: string;
  session_id: string;
  user_id: string;
  tenant_id: string;
  size_gb: number;
  status: 'active' | 'archived' | 'deleted';
  created_at: string;
  archived_at: string | null;
  deleted_at: string | null;
  session_name: string | null;
  source_url: string | null;
  source_ref: string | null;
}

/**
 * Workspace list response
 */
export interface ApiWorkspaceListResponse {
  workspaces: ApiWorkspaceResponse[];
}

/**
 * Secret type info response
 */
export interface ApiSecretTypeInfoResponse {
  type: string;
  label: string;
  description: string;
  fields: Array<{
    key: string;
    label: string;
    type: 'text' | 'password' | 'textarea';
    required: boolean;
  }>;
  default_mount_type: string;
}

/**
 * Feature module response from API
 */
export interface ApiFeatureModuleResponse {
  key: string;
  label: string;
  icon: string;
  scope: string;
  enabled: boolean;
  default_enabled: boolean;
  admin_only: boolean;
  order: number;
}

/**
 * User feature preference response from API
 */
export interface ApiUserFeaturePreferenceResponse {
  feature_key: string;
  visible: boolean;
  sort_order: number;
}
