import { useCallback, useState } from 'react';
import { Field, Input, Textarea } from '@niuulabs/ui';
import type { McpServerConfig } from '../models/volundr.model';
import { parsePresetYaml, serializePresetYaml } from '../utils/presetYaml';
import { buildYamlRuntimeFields, type WizardForm } from './launchWizardModel';
import {
  MUTED_BUTTON_CLASS,
  RuntimePanel,
  SECONDARY_BUTTON_CLASS,
  SectionCard,
  WizardSelect,
} from './LaunchWizardPrimitives';

export function AdvancedRuntimeSection({
  form,
  update,
  availableMcpServers,
}: {
  form: WizardForm;
  update: (patch: Partial<WizardForm>) => void;
  availableMcpServers: McpServerConfig[];
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [showCustomMcp, setShowCustomMcp] = useState(false);
  const [customMcpName, setCustomMcpName] = useState('');
  const [customMcpType, setCustomMcpType] = useState<McpServerConfig['type']>('stdio');
  const [customMcpCommand, setCustomMcpCommand] = useState('');
  const [customMcpArgs, setCustomMcpArgs] = useState('');
  const [customMcpUrl, setCustomMcpUrl] = useState('');
  const [customMcpEnvKey, setCustomMcpEnvKey] = useState('');
  const [customMcpEnvValue, setCustomMcpEnvValue] = useState('');
  const [customMcpEnv, setCustomMcpEnv] = useState<Record<string, string>>({});
  const selectedMcpNames = new Set(form.mcpServers.map((server) => server.name));
  const availablePresetServers = availableMcpServers.filter(
    (server) => !selectedMcpNames.has(server.name),
  );

  const resetCustomMcp = useCallback(() => {
    setShowCustomMcp(false);
    setCustomMcpName('');
    setCustomMcpType('stdio');
    setCustomMcpCommand('');
    setCustomMcpArgs('');
    setCustomMcpUrl('');
    setCustomMcpEnvKey('');
    setCustomMcpEnvValue('');
    setCustomMcpEnv({});
  }, []);

  const handleToggleYaml = useCallback(() => {
    if (!form.yamlMode) {
      update({
        yamlMode: true,
        yamlContent: serializePresetYaml(buildYamlRuntimeFields(form)),
      });
      setYamlError(null);
      return;
    }

    try {
      const parsed = parsePresetYaml(form.yamlContent);
      const patch: Partial<WizardForm> = { yamlMode: false };

      if (parsed.cliTool) patch.definition = `skuld-${parsed.cliTool}`;
      if (parsed.model !== undefined) patch.model = parsed.model;
      if (parsed.systemPrompt !== undefined) patch.systemPrompt = parsed.systemPrompt;
      if (parsed.resourceConfig) {
        patch.cpu = parsed.resourceConfig.cpu ?? form.cpu;
        patch.mem = parsed.resourceConfig.memory ?? form.mem;
        patch.gpu = parsed.resourceConfig.gpu ?? form.gpu;
      }
      if (parsed.mcpServers) patch.mcpServers = parsed.mcpServers;
      if (parsed.envVars) {
        patch.envVars = Object.entries(parsed.envVars).map(([key, value]) => ({ key, value }));
      }
      if (parsed.envSecretRefs) patch.selectedCredentials = parsed.envSecretRefs;
      if (parsed.integrationIds) patch.selectedIntegrations = parsed.integrationIds;
      if (parsed.setupScripts) patch.setupScripts = parsed.setupScripts;
      if (parsed.source !== undefined) {
        if (parsed.source === null) {
          patch.sourcetype = 'blank';
          patch.repo = '';
          patch.branch = '';
          patch.mountPath = form.mountPath;
        } else if (parsed.source.type === 'git') {
          patch.sourcetype = 'git';
          patch.repo = parsed.source.repo;
          patch.branch = parsed.source.branch;
        } else {
          patch.sourcetype = 'local_mount';
          patch.mountPath =
            parsed.source.local_path ?? parsed.source.paths[0]?.host_path ?? form.mountPath;
        }
      }

      update(patch);
      setYamlError(null);
    } catch (error) {
      setYamlError(error instanceof Error ? error.message : 'Invalid YAML');
    }
  }, [form, update]);

  const handleAddCustomMcp = useCallback(() => {
    const server: McpServerConfig = {
      name: customMcpName.trim(),
      type: customMcpType,
      ...(customMcpType === 'stdio'
        ? {
            command: customMcpCommand.trim(),
            args: customMcpArgs.trim() ? customMcpArgs.trim().split(/\s+/) : [],
          }
        : {
            url: customMcpUrl.trim(),
          }),
      ...(Object.keys(customMcpEnv).length > 0 ? { env: customMcpEnv } : {}),
    };

    if (!server.name) return;
    update({
      mcpServers: [...form.mcpServers.filter((item) => item.name !== server.name), server],
    });
    resetCustomMcp();
  }, [
    customMcpArgs,
    customMcpCommand,
    customMcpEnv,
    customMcpName,
    customMcpType,
    customMcpUrl,
    form.mcpServers,
    resetCustomMcp,
    update,
  ]);
  return (
      <SectionCard
        title="Advanced"
        description="Prompts, MCP wiring, environment variables, and setup scripts."
      >
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <button
            type="button"
            className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
            onClick={() => setShowAdvanced((value) => !value)}
          >
            {showAdvanced ? 'hide advanced' : 'show advanced'}
          </button>
          {showAdvanced ? (
            <button
              type="button"
              className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
              onClick={handleToggleYaml}
            >
              {form.yamlMode ? 'form view' : 'edit as yaml'}
            </button>
          ) : null}
        </div>
        {showAdvanced && form.yamlMode ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            <Textarea
              value={form.yamlContent}
              onChange={(e) => update({ yamlContent: e.target.value })}
              rows={20}
              spellCheck={false}
              placeholder="Launch spec YAML"
            />
            {yamlError ? <div className="niuu:text-xs niuu:text-danger">{yamlError}</div> : null}
          </div>
        ) : null}
        {showAdvanced && !form.yamlMode ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-6">
            <RuntimePanel
              title="System prompt"
              description="Override the default agent behavior for this run."
            >
              <Textarea
                value={form.systemPrompt}
                onChange={(e) => update({ systemPrompt: e.target.value })}
                rows={5}
                placeholder="Override the default system prompt"
              />
            </RuntimePanel>

            <RuntimePanel
              title="MCP servers"
              description="Attach launch-spec tools and custom MCP definitions."
            >
              <div className="niuu:flex niuu:flex-col niuu:gap-2">
                {form.mcpServers.length > 0 ? (
                  <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
                    {form.mcpServers.map((server) => (
                      <div
                        key={server.name}
                        className="niuu:flex niuu:flex-col niuu:gap-1 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs"
                      >
                        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
                          <span className="niuu:font-mono niuu:text-text-primary">
                            {server.name}
                          </span>
                          <button
                            type="button"
                            className="niuu:text-text-faint niuu:hover:text-text-primary"
                            onClick={() =>
                              update({
                                mcpServers: form.mcpServers.filter(
                                  (item) => item.name !== server.name,
                                ),
                              })
                            }
                          >
                            remove
                          </button>
                        </div>
                        <span className="niuu:text-text-faint">
                          {server.type === 'stdio'
                            ? [server.command, ...(server.args ?? [])].filter(Boolean).join(' ')
                            : (server.url ?? server.type)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-faint">
                    No MCP servers selected.
                  </div>
                )}
                {availablePresetServers.length > 0 ? (
                  <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
                    {availablePresetServers.map((server) => (
                      <button
                        key={server.name}
                        type="button"
                        className={`${SECONDARY_BUTTON_CLASS} niuu:text-left`}
                        onClick={() => update({ mcpServers: [...form.mcpServers, server] })}
                      >
                        <div className="niuu:font-mono niuu:text-text-primary">{server.name}</div>
                        <div className="niuu:text-text-faint">{server.type}</div>
                      </button>
                    ))}
                  </div>
                ) : null}
                <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                  <button
                    type="button"
                    className={SECONDARY_BUTTON_CLASS}
                    onClick={() => setShowCustomMcp((value) => !value)}
                  >
                    {showCustomMcp ? 'cancel custom server' : 'add custom server'}
                  </button>
                </div>
                {showCustomMcp ? (
                  <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
                    <Field label="Name">
                      <Input
                        value={customMcpName}
                        onChange={(e) => setCustomMcpName(e.target.value)}
                        placeholder="filesystem"
                      />
                    </Field>
                    <Field label="Type">
                      <WizardSelect
                        options={[
                          { value: 'stdio', label: 'stdio' },
                          { value: 'sse', label: 'sse' },
                          { value: 'http', label: 'http' },
                        ]}
                        value={customMcpType}
                        onChange={(value) => setCustomMcpType(value as McpServerConfig['type'])}
                      />
                    </Field>
                    {customMcpType === 'stdio' ? (
                      <>
                        <Field label="Command">
                          <Input
                            value={customMcpCommand}
                            onChange={(e) => setCustomMcpCommand(e.target.value)}
                            placeholder="uvx"
                          />
                        </Field>
                        <Field label="Args">
                          <Input
                            value={customMcpArgs}
                            onChange={(e) => setCustomMcpArgs(e.target.value)}
                            placeholder="mcp-filesystem /workspace"
                          />
                        </Field>
                      </>
                    ) : (
                      <Field label="URL">
                        <Input
                          value={customMcpUrl}
                          onChange={(e) => setCustomMcpUrl(e.target.value)}
                          placeholder="http://localhost:3000/sse"
                        />
                      </Field>
                    )}
                    <div className="niuu:col-span-2 niuu:flex niuu:flex-col niuu:gap-2">
                      <span className="niuu:text-xs niuu:text-text-faint">Custom environment</span>
                      {Object.entries(customMcpEnv).map(([key, value]) => (
                        <div
                          key={key}
                          className="niuu:grid niuu:grid-cols-[1fr_1fr_auto] niuu:gap-2"
                        >
                          <Input value={key} readOnly />
                          <Input value={value} readOnly />
                          <button
                            type="button"
                            className={MUTED_BUTTON_CLASS}
                            onClick={() => {
                              const next = { ...customMcpEnv };
                              delete next[key];
                              setCustomMcpEnv(next);
                            }}
                          >
                            remove
                          </button>
                        </div>
                      ))}
                      <div className="niuu:grid niuu:grid-cols-[1fr_1fr_auto] niuu:gap-2">
                        <Input
                          value={customMcpEnvKey}
                          onChange={(e) => setCustomMcpEnvKey(e.target.value)}
                          placeholder="KEY"
                        />
                        <Input
                          value={customMcpEnvValue}
                          onChange={(e) => setCustomMcpEnvValue(e.target.value)}
                          placeholder="value"
                        />
                        <button
                          type="button"
                          className={MUTED_BUTTON_CLASS}
                          onClick={() => {
                            if (!customMcpEnvKey.trim()) return;
                            setCustomMcpEnv((current) => ({
                              ...current,
                              [customMcpEnvKey.trim()]: customMcpEnvValue,
                            }));
                            setCustomMcpEnvKey('');
                            setCustomMcpEnvValue('');
                          }}
                        >
                          add
                        </button>
                      </div>
                      <div className="niuu:flex niuu:gap-2">
                        <button
                          type="button"
                          className={MUTED_BUTTON_CLASS}
                          onClick={handleAddCustomMcp}
                          disabled={
                            !customMcpName.trim() ||
                            (customMcpType === 'stdio'
                              ? !customMcpCommand.trim()
                              : !customMcpUrl.trim())
                          }
                        >
                          add server
                        </button>
                        <button
                          type="button"
                          className={MUTED_BUTTON_CLASS}
                          onClick={resetCustomMcp}
                        >
                          reset
                        </button>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </RuntimePanel>

            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-6">
              <RuntimePanel
                title="Environment variables"
                description="Inline env overrides for the launched session."
              >
                {form.envVars.length === 0 ? (
                  <div className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3 niuu:text-xs niuu:text-text-faint">
                    No environment variables yet. Add one below.
                  </div>
                ) : null}
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {form.envVars.map((entry, index) => (
                    <div
                      key={`${entry.key}-${index}`}
                      className="niuu:grid niuu:grid-cols-[1fr_1fr_auto] niuu:gap-2"
                    >
                      <Input
                        value={entry.key}
                        onChange={(e) =>
                          update({
                            envVars: form.envVars.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, key: e.target.value } : item,
                            ),
                          })
                        }
                        placeholder="KEY"
                      />
                      <Input
                        value={entry.value}
                        onChange={(e) =>
                          update({
                            envVars: form.envVars.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, value: e.target.value } : item,
                            ),
                          })
                        }
                        placeholder="value"
                      />
                      <button
                        type="button"
                        className={SECONDARY_BUTTON_CLASS}
                        onClick={() =>
                          update({
                            envVars: form.envVars.filter((_, itemIndex) => itemIndex !== index),
                          })
                        }
                      >
                        remove
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
                  onClick={() => update({ envVars: [...form.envVars, { key: '', value: '' }] })}
                >
                  add env var
                </button>
              </RuntimePanel>
              <RuntimePanel
                title="Setup scripts"
                description="Commands to run before the first prompt hits the pod."
              >
                {form.setupScripts.length === 0 ? (
                  <div className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3 niuu:text-xs niuu:text-text-faint">
                    No setup scripts yet. Add one below.
                  </div>
                ) : null}
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {form.setupScripts.map((script, index) => (
                    <div
                      key={`${index}-${script}`}
                      className="niuu:grid niuu:grid-cols-[1fr_auto] niuu:gap-2"
                    >
                      <Input
                        value={script}
                        onChange={(e) =>
                          update({
                            setupScripts: form.setupScripts.map((item, itemIndex) =>
                              itemIndex === index ? e.target.value : item,
                            ),
                          })
                        }
                        placeholder="pnpm install"
                      />
                      <button
                        type="button"
                        className={SECONDARY_BUTTON_CLASS}
                        onClick={() =>
                          update({
                            setupScripts: form.setupScripts.filter(
                              (_, itemIndex) => itemIndex !== index,
                            ),
                          })
                        }
                      >
                        remove
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
                  onClick={() => update({ setupScripts: [...form.setupScripts, ''] })}
                >
                  add script
                </button>
              </RuntimePanel>
            </div>
          </div>
        ) : null}
      </SectionCard>
  );
}
