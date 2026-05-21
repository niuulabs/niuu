import yaml from 'js-yaml';
import type {
  CliTool,
  McpServerConfig,
  ResourceConfig,
  RuleConfig,
  SessionSource,
  SkillConfig,
  TerminalSidecarConfig,
  WorkloadConfig,
} from '../models/volundr.model';

interface PresetYamlDoc {
  cli_tool?: string;
  workload_type?: string;
  model?: string | null;
  system_prompt?: string | null;
  resource_config?: Record<string, string | undefined>;
  mcp_servers?: Array<{
    name: string;
    type: string;
    command?: string;
    url?: string;
    args?: string[];
    env?: Record<string, string>;
  }>;
  terminal_sidecar?: { enabled: boolean; allowed_commands: string[] };
  skills?: Array<{ name: string; path?: string; inline?: string }>;
  rules?: Array<{ path?: string; inline?: string }>;
  env_vars?: Record<string, string>;
  env_secret_refs?: string[];
  source?:
    | { type: 'git'; repo: string; branch: string }
    | {
        type: 'local_mount';
        local_path?: string;
        paths: Array<{ host_path: string; mount_path: string; read_only: boolean }>;
      }
    | null;
  integration_ids?: string[];
  setup_scripts?: string[];
  workload_config?: Record<string, string | number | boolean | undefined>;
}

export interface PresetRuntimeFields {
  cliTool: CliTool;
  workloadType: string;
  model: string;
  systemPrompt: string;
  resourceConfig: ResourceConfig;
  mcpServers: McpServerConfig[];
  terminalSidecar: TerminalSidecarConfig;
  skills: SkillConfig[];
  rules: RuleConfig[];
  envVars: Record<string, string>;
  envSecretRefs: string[];
  source: SessionSource | null;
  integrationIds: string[];
  setupScripts: string[];
  workloadConfig: WorkloadConfig;
}

export function serializePresetYaml(fields: PresetRuntimeFields): string {
  const doc: PresetYamlDoc = {
    cli_tool: fields.cliTool,
    workload_type: fields.workloadType,
    model: fields.model || null,
    system_prompt: fields.systemPrompt || null,
    resource_config:
      Object.keys(fields.resourceConfig).length > 0 ? fields.resourceConfig : undefined,
    mcp_servers:
      fields.mcpServers.length > 0
        ? fields.mcpServers.map((server) => ({
            name: server.name,
            type: server.type,
            ...(server.command ? { command: server.command } : {}),
            ...(server.url ? { url: server.url } : {}),
            ...(server.args?.length ? { args: server.args } : {}),
            ...(server.env && Object.keys(server.env).length > 0 ? { env: server.env } : {}),
          }))
        : undefined,
    terminal_sidecar: {
      enabled: fields.terminalSidecar.enabled,
      allowed_commands: fields.terminalSidecar.allowedCommands,
    },
    skills: fields.skills.length > 0 ? fields.skills : undefined,
    rules: fields.rules.length > 0 ? fields.rules : undefined,
    env_vars: Object.keys(fields.envVars).length > 0 ? fields.envVars : undefined,
    env_secret_refs: fields.envSecretRefs.length > 0 ? fields.envSecretRefs : undefined,
    source: fields.source ?? undefined,
    integration_ids: fields.integrationIds.length > 0 ? fields.integrationIds : undefined,
    setup_scripts: fields.setupScripts.length > 0 ? fields.setupScripts : undefined,
    workload_config:
      Object.keys(fields.workloadConfig).length > 0 ? fields.workloadConfig : undefined,
  };

  return yaml.dump(JSON.parse(JSON.stringify(doc)), { lineWidth: 120, noRefs: true });
}

export function parsePresetYaml(yamlContent: string): Partial<PresetRuntimeFields> {
  const parsed = yaml.load(yamlContent) as PresetYamlDoc | null;
  if (!parsed || typeof parsed !== 'object') return {};

  const result: Partial<PresetRuntimeFields> = {};

  if (parsed.cli_tool) result.cliTool = parsed.cli_tool as CliTool;
  if (parsed.workload_type) result.workloadType = parsed.workload_type;
  if (parsed.model !== undefined) result.model = parsed.model ?? '';
  if (parsed.system_prompt !== undefined) result.systemPrompt = parsed.system_prompt ?? '';
  if (parsed.resource_config) result.resourceConfig = parsed.resource_config;
  if (parsed.mcp_servers) {
    result.mcpServers = parsed.mcp_servers.map((server) => ({
      name: server.name,
      type: server.type as McpServerConfig['type'],
      command: server.command,
      url: server.url,
      args: server.args,
      env: server.env,
    }));
  }
  if (parsed.terminal_sidecar) {
    result.terminalSidecar = {
      enabled: parsed.terminal_sidecar.enabled,
      allowedCommands: parsed.terminal_sidecar.allowed_commands ?? [],
    };
  }
  if (parsed.skills) result.skills = parsed.skills;
  if (parsed.rules) result.rules = parsed.rules;
  if (parsed.env_vars) result.envVars = parsed.env_vars;
  if (parsed.env_secret_refs) result.envSecretRefs = parsed.env_secret_refs;
  if (parsed.source !== undefined) result.source = parsed.source as SessionSource | null;
  if (parsed.integration_ids) result.integrationIds = parsed.integration_ids;
  if (parsed.setup_scripts) result.setupScripts = parsed.setup_scripts;
  if (parsed.workload_config) result.workloadConfig = parsed.workload_config;

  return result;
}
