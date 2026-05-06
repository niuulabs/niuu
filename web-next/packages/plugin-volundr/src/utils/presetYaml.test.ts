import { describe, it, expect } from 'vitest';
import { serializePresetYaml, parsePresetYaml } from './presetYaml';
import type { PresetRuntimeFields } from './presetYaml';

const FULL_FIELDS: PresetRuntimeFields = {
  cliTool: 'claude',
  workloadType: 'skuld-claude',
  model: 'sonnet-primary',
  systemPrompt: 'Review changes carefully.',
  resourceConfig: { cpu: '4', memory: '16Gi' },
  mcpServers: [
    {
      name: 'filesystem',
      type: 'stdio',
      command: 'uvx',
      args: ['mcp-filesystem', '/workspace'],
      env: { LOG_LEVEL: 'debug' },
    },
    { name: 'remote', type: 'sse', url: 'https://mcp.example.com' },
  ],
  terminalSidecar: { enabled: true, allowedCommands: ['git', 'pnpm'] },
  skills: [{ name: 'review', path: '/skills/review.md' }],
  rules: [{ inline: 'always use early returns' }],
  envVars: { NODE_ENV: 'production' },
  envSecretRefs: ['anthropic-key'],
  source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
  integrationIds: ['github-primary'],
  setupScripts: ['pnpm install'],
  workloadConfig: { timeout: 300 },
};

const EMPTY_FIELDS: PresetRuntimeFields = {
  cliTool: 'aider',
  workloadType: 'skuld-aider',
  model: '',
  systemPrompt: '',
  resourceConfig: {},
  mcpServers: [],
  terminalSidecar: { enabled: false, allowedCommands: [] },
  skills: [],
  rules: [],
  envVars: {},
  envSecretRefs: [],
  source: null,
  integrationIds: [],
  setupScripts: [],
  workloadConfig: {},
};

describe('serializePresetYaml', () => {
  it('serialises full fields to valid YAML', () => {
    const yaml = serializePresetYaml(FULL_FIELDS);
    expect(yaml).toContain('cli_tool: claude');
    expect(yaml).toContain('workload_type: skuld-claude');
    expect(yaml).toContain('model: sonnet-primary');
    expect(yaml).toContain('system_prompt: Review changes carefully.');
  });

  it('includes mcp_servers with command, url, args, and env', () => {
    const yaml = serializePresetYaml(FULL_FIELDS);
    expect(yaml).toContain('name: filesystem');
    expect(yaml).toContain('command: uvx');
    expect(yaml).toContain('- mcp-filesystem');
    expect(yaml).toContain('LOG_LEVEL: debug');
    expect(yaml).toContain('name: remote');
    expect(yaml).toContain('url: https://mcp.example.com');
  });

  it('includes terminal_sidecar, skills, rules, env_vars, env_secret_refs', () => {
    const yaml = serializePresetYaml(FULL_FIELDS);
    expect(yaml).toContain('enabled: true');
    expect(yaml).toContain('- git');
    expect(yaml).toContain('- pnpm');
    expect(yaml).toContain('name: review');
    expect(yaml).toContain('inline: always use early returns');
    expect(yaml).toContain('NODE_ENV: production');
    expect(yaml).toContain('- anthropic-key');
  });

  it('includes source, integration_ids, setup_scripts, workload_config', () => {
    const yaml = serializePresetYaml(FULL_FIELDS);
    expect(yaml).toContain('type: git');
    expect(yaml).toContain('repo: github.com/niuulabs/volundr');
    expect(yaml).toContain('- github-primary');
    expect(yaml).toContain('- pnpm install');
    expect(yaml).toContain('timeout: 300');
  });

  it('omits optional collections when empty', () => {
    const yaml = serializePresetYaml(EMPTY_FIELDS);
    expect(yaml).not.toContain('mcp_servers');
    expect(yaml).not.toContain('skills');
    expect(yaml).not.toContain('rules');
    expect(yaml).not.toContain('env_vars');
    expect(yaml).not.toContain('env_secret_refs');
    expect(yaml).not.toContain('integration_ids');
    expect(yaml).not.toContain('setup_scripts');
    expect(yaml).not.toContain('workload_config');
    expect(yaml).not.toContain('resource_config');
    expect(yaml).not.toContain('source');
  });

  it('sets model and system_prompt to null when empty strings', () => {
    const yaml = serializePresetYaml(EMPTY_FIELDS);
    expect(yaml).toContain('model: null');
    expect(yaml).toContain('system_prompt: null');
  });
});

describe('parsePresetYaml', () => {
  it('round-trips through serialize and parse', () => {
    const yaml = serializePresetYaml(FULL_FIELDS);
    const parsed = parsePresetYaml(yaml);

    expect(parsed.cliTool).toBe('claude');
    expect(parsed.workloadType).toBe('skuld-claude');
    expect(parsed.model).toBe('sonnet-primary');
    expect(parsed.systemPrompt).toBe('Review changes carefully.');
    expect(parsed.resourceConfig).toEqual({ cpu: '4', memory: '16Gi' });
    expect(parsed.mcpServers).toHaveLength(2);
    expect(parsed.mcpServers?.[0]?.name).toBe('filesystem');
    expect(parsed.mcpServers?.[0]?.command).toBe('uvx');
    expect(parsed.mcpServers?.[0]?.args).toEqual(['mcp-filesystem', '/workspace']);
    expect(parsed.mcpServers?.[0]?.env).toEqual({ LOG_LEVEL: 'debug' });
    expect(parsed.mcpServers?.[1]?.url).toBe('https://mcp.example.com');
    expect(parsed.terminalSidecar?.enabled).toBe(true);
    expect(parsed.terminalSidecar?.allowedCommands).toEqual(['git', 'pnpm']);
    expect(parsed.skills).toHaveLength(1);
    expect(parsed.rules).toHaveLength(1);
    expect(parsed.envVars).toEqual({ NODE_ENV: 'production' });
    expect(parsed.envSecretRefs).toEqual(['anthropic-key']);
    expect(parsed.integrationIds).toEqual(['github-primary']);
    expect(parsed.setupScripts).toEqual(['pnpm install']);
    expect(parsed.workloadConfig).toEqual({ timeout: 300 });
  });

  it('returns empty object for empty/null YAML', () => {
    expect(parsePresetYaml('')).toEqual({});
    expect(parsePresetYaml('null')).toEqual({});
  });

  it('returns empty object for non-object YAML', () => {
    expect(parsePresetYaml('42')).toEqual({});
    expect(parsePresetYaml('"just a string"')).toEqual({});
  });

  it('parses model null as empty string', () => {
    const parsed = parsePresetYaml('model: null');
    expect(parsed.model).toBe('');
  });

  it('parses system_prompt null as empty string', () => {
    const parsed = parsePresetYaml('system_prompt: null');
    expect(parsed.systemPrompt).toBe('');
  });

  it('parses source as null when explicitly set', () => {
    const parsed = parsePresetYaml('source: null');
    expect(parsed.source).toBeNull();
  });

  it('parses terminal_sidecar with default allowed_commands', () => {
    const parsed = parsePresetYaml('terminal_sidecar:\n  enabled: true\n');
    expect(parsed.terminalSidecar?.enabled).toBe(true);
    expect(parsed.terminalSidecar?.allowedCommands).toEqual([]);
  });

  it('returns partial result when only some fields present', () => {
    const parsed = parsePresetYaml('cli_tool: codex\nworkload_type: skuld-codex\n');
    expect(parsed.cliTool).toBe('codex');
    expect(parsed.workloadType).toBe('skuld-codex');
    expect(parsed.model).toBeUndefined();
    expect(parsed.mcpServers).toBeUndefined();
  });
});
