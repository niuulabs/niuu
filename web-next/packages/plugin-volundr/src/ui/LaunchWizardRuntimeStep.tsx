import { useMemo, useState } from 'react';
import { Field, Input, SegmentedFilter, Textarea } from '@niuulabs/ui';
import type {
  ClusterResourceInfo,
  IntegrationConnection,
  McpServerConfig,
  SessionDefinition,
  StoredCredential,
  VolundrLaunchSpec,
  VolundrTarget,
  VolundrWorkspace,
} from '../models/volundr.model';
import {
  aggregateResourceCapacity,
  filterModelsForDefinition,
  findSessionDefinition,
  formatIntegrationLabel,
  formatIntegrationMeta,
  formatModelOption,
  formatResourceValue,
  getDefinitionRune,
  getMatchingTargets,
  getResourceErrors,
  getTargetTagOptions,
  launchSpecLabel,
  launchSpecRef,
  normalizeRepoUrl,
  pickDefaultModelForDefinition,
  workspaceLabel,
  type RuntimeModelDescriptor,
  type WizardForm,
} from './launchWizardModel';
import {
  NEW_WORKSPACE_VALUE,
  NO_PRESET_VALUE,
  RuntimePanel,
  SECONDARY_BUTTON_CLASS,
  SectionCard,
  WizardSelect,
} from './LaunchWizardPrimitives';
import { AdvancedRuntimeSection } from './LaunchWizardAdvancedRuntime';
import './LaunchWizard.css';

export function RuntimeStep({
  form,
  update,
  models,
  workspaces,
  targets,
  credentials,
  integrations,
  clusterResources,
  presets,
  selectedPreset,
  availableMcpServers,
  sessionDefinitions,
  onApplyPreset,
  onSavePreset,
}: {
  form: WizardForm;
  update: (patch: Partial<WizardForm>) => void;
  models: Record<string, RuntimeModelDescriptor>;
  workspaces: VolundrWorkspace[];
  targets: VolundrTarget[];
  credentials: StoredCredential[];
  integrations: IntegrationConnection[];
  clusterResources: ClusterResourceInfo | null;
  presets: VolundrLaunchSpec[];
  selectedPreset: VolundrLaunchSpec | null;
  availableMcpServers: McpServerConfig[];
  sessionDefinitions: SessionDefinition[];
  onApplyPreset: (launchSpecRef: string) => void;
  onSavePreset: (name: string) => Promise<void>;
}) {
  const compatibleModels = filterModelsForDefinition(models, form.definition, sessionDefinitions);
  const modelOptions = Object.entries(compatibleModels).map(([id, model]) => ({
    value: id,
    label: formatModelOption(id, model),
  }));
  const selectedDefinition =
    findSessionDefinition(form.definition, sessionDefinitions)?.displayName ?? form.definition;
  const totalModelCount = Object.keys(models).length;
  const targetTagOptions = getTargetTagOptions(targets);
  const matchingTagTargets = getMatchingTargets(targets, form.targetTags, form.targetMatch);
  const filteredWorkspaces = workspaces.filter((workspace) => {
    if (form.sourcetype !== 'git' || !form.repo.trim() || !workspace.sourceUrl) return true;
    return normalizeRepoUrl(workspace.sourceUrl) === normalizeRepoUrl(form.repo);
  });
  const workspaceOptions = [
    { value: NEW_WORKSPACE_VALUE, label: 'New workspace' },
    ...filteredWorkspaces.map((workspace) => ({
      value: workspace.id,
      label: workspaceLabel(workspace),
    })),
  ];
  const resourceCapacities = useMemo(
    () => aggregateResourceCapacity(clusterResources),
    [clusterResources],
  );
  const resourceErrors = useMemo(
    () => getResourceErrors(form, clusterResources),
    [form, clusterResources],
  );
  const [presetName, setPresetName] = useState('');

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-runtime-content">
      {presets.length > 0 ? (
        <SectionCard
          title="Launch spec"
          description="Load catalog specs or save reusable forge configurations."
        >
          <div className="niuu:grid niuu:grid-cols-[2fr_1fr] niuu:gap-4">
            <Field label="Load launch spec">
              <WizardSelect
                options={[
                  { value: NO_PRESET_VALUE, label: 'Custom launch' },
                  ...presets.map((preset) => ({
                    value: launchSpecRef(preset),
                    label: launchSpecLabel(preset),
                  })),
                ]}
                value={form.presetId || NO_PRESET_VALUE}
                onChange={(value) => onApplyPreset(value === NO_PRESET_VALUE ? '' : value)}
              />
            </Field>
            <div className="niuu:flex niuu:items-end niuu:gap-2">
              <Input
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="save as launch spec"
              />
              <button
                type="button"
                className={SECONDARY_BUTTON_CLASS}
                onClick={() => {
                  if (!presetName.trim()) return;
                  void onSavePreset(presetName.trim()).then(() => setPresetName(''));
                }}
              >
                save
              </button>
            </div>
          </div>
          {selectedPreset ? (
            <div className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-faint">
              loaded{' '}
              <span className="niuu:font-mono niuu:text-text-primary">{selectedPreset.name}</span>
              <span> · {selectedPreset.scope === 'system' ? 'catalog' : 'saved'}</span>
              {selectedPreset.description ? ` · ${selectedPreset.description}` : ''}
            </div>
          ) : (
            <div className="niuu:rounded-lg niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-faint">
              No launch spec loaded. Advanced runtime values will be materialized into a saved
              launch spec at launch if needed.
            </div>
          )}
        </SectionCard>
      ) : null}

      <div className="niuu:grid niuu:grid-cols-[1.2fr_0.8fr] niuu:gap-6">
        <SectionCard
          title="Runtime"
          description="Choose the CLI agent, model, workspace, and launch prompts."
        >
          <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
            {sessionDefinitions.map((def) => (
              <button
                key={def.key}
                className={`niuu:flex niuu:items-center niuu:gap-1.5 niuu:rounded-md niuu:border niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary ${
                  form.definition === def.key
                    ? 'niuu:border-brand niuu:bg-bg-tertiary'
                    : 'niuu:border-border-subtle niuu:bg-bg-primary niuu:hover:border-brand niuu:hover:bg-bg-tertiary'
                }`}
                onClick={() => {
                  const patch: Partial<WizardForm> = { definition: def.key };
                  const defaultModel = pickDefaultModelForDefinition(
                    models,
                    def.key,
                    sessionDefinitions,
                  );
                  if (defaultModel) {
                    patch.model = defaultModel;
                  }
                  update(patch);
                }}
                data-testid={`runtime-option-${def.key}`}
                title={def.description || undefined}
              >
                <span className="niuu:font-mono niuu:text-base">{getDefinitionRune(def.key)}</span>
                <span className="niuu:font-mono">{def.displayName}</span>
              </button>
            ))}
          </div>
          <div className="niuu:grid niuu:grid-cols-1 niuu:gap-4">
            <Field label="Model">
              {modelOptions.length > 0 ? (
                <WizardSelect
                  options={modelOptions}
                  value={form.model}
                  onChange={(value) => update({ model: value })}
                  placeholder="Select model"
                  testId="model-select"
                />
              ) : (
                <Input
                  value={form.model}
                  onChange={(e) => update({ model: e.target.value })}
                  placeholder="sonnet-primary"
                />
              )}
              {totalModelCount > 0 ? (
                <div className="niuu:text-xs niuu:text-text-faint">
                  {modelOptions.length === totalModelCount
                    ? `Showing all ${totalModelCount} Bifrost models for ${selectedDefinition}.`
                    : `Showing ${modelOptions.length} of ${totalModelCount} Bifrost models compatible with ${selectedDefinition}.`}
                </div>
              ) : null}
            </Field>
          </div>
          {workspaceOptions.length > 1 ? (
            <Field label="Workspace reuse">
              <WizardSelect
                options={workspaceOptions}
                value={form.workspaceId || NEW_WORKSPACE_VALUE}
                onChange={(value) =>
                  update({ workspaceId: value === NEW_WORKSPACE_VALUE ? '' : value })
                }
                testId="workspace-select"
              />
            </Field>
          ) : null}
          {targets.length > 0 ? (
            <Field label="Forge">
              <div className="niuu:space-y-3">
                <SegmentedFilter<WizardForm['targetMode']>
                  aria-label="Forge routing mode"
                  value={form.targetMode}
                  onChange={(targetMode) => update({ targetMode })}
                  options={[
                    { value: 'instance', label: 'Specific Forge' },
                    {
                      value: 'tags',
                      label: 'Match tags',
                      count: targetTagOptions.length,
                      disabled: targetTagOptions.length === 0,
                    },
                  ]}
                />
                {form.targetMode === 'tags' ? (
                  <div className="niuu:space-y-3 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
                    <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                      {targetTagOptions.map((tag) => {
                        const selected = form.targetTags.includes(tag);
                        return (
                          <button
                            key={tag}
                            type="button"
                            className={`niuu:rounded-full niuu:border niuu:px-3 niuu:py-1 niuu:text-xs niuu:font-mono ${
                              selected
                                ? 'niuu:border-brand niuu:bg-bg-tertiary niuu:text-text-primary'
                                : 'niuu:border-border-subtle niuu:bg-bg-secondary niuu:text-text-faint niuu:hover:border-brand'
                            }`}
                            onClick={() =>
                              update({
                                targetTags: selected
                                  ? form.targetTags.filter((item) => item !== tag)
                                  : [...form.targetTags, tag],
                              })
                            }
                          >
                            {tag}
                          </button>
                        );
                      })}
                    </div>
                    <WizardSelect
                      options={[
                        { value: 'all', label: 'Match all selected tags' },
                        { value: 'any', label: 'Match any selected tag' },
                      ]}
                      value={form.targetMatch}
                      onChange={(value) => update({ targetMatch: value === 'any' ? 'any' : 'all' })}
                      placeholder="Tag match"
                      testId="forge-target-match-select"
                    />
                    <div className="niuu:text-xs niuu:text-text-faint">
                      {form.targetTags.length === 0
                        ? 'No tags selected; Guild will route to the default enabled Forge.'
                        : `${matchingTagTargets.length} matching Forge${matchingTagTargets.length === 1 ? '' : 's'}: ${
                            matchingTagTargets.map((target) => target.name).join(', ') || 'none'
                          }`}
                    </div>
                  </div>
                ) : (
                  <WizardSelect
                    options={targets.map((target) => ({
                      value: target.id,
                      label: `${target.name}${target.isDefault ? ' (default)' : ''}${
                        target.tags.length ? ` · ${target.tags.join(', ')}` : ''
                      }`,
                    }))}
                    value={form.instanceId}
                    onChange={(value) => update({ instanceId: value })}
                    placeholder="Select forge"
                    testId="forge-target-select"
                  />
                )}
              </div>
            </Field>
          ) : null}
          <RuntimePanel
            title="Prompting"
            description="Carry system instructions and an initial request into the new session."
          >
            <Field label="Initial prompt (optional)">
              <Textarea
                value={form.initialPrompt}
                onChange={(e) => update({ initialPrompt: e.target.value })}
                rows={3}
                placeholder="Kick off the session with a concrete request"
              />
            </Field>
          </RuntimePanel>
        </SectionCard>

        <SectionCard
          title="Resources"
          description="Request runtime capacity with live guardrails from Forge."
        >
          <Field label="CPU (cores)">
            <Input
              value={form.cpu}
              onChange={(e) => update({ cpu: e.target.value })}
              placeholder="2"
            />
            {resourceCapacities.get('cpu') ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">
                available {formatResourceValue(resourceCapacities.get('cpu')!.total, 'cores')}
              </div>
            ) : null}
            {resourceErrors.cpu ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-danger">{resourceErrors.cpu}</div>
            ) : null}
          </Field>
          <Field label="Memory">
            <Input
              value={form.mem}
              onChange={(e) => update({ mem: e.target.value })}
              placeholder="8Gi"
            />
            {resourceCapacities.get('memory') ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">
                available {formatResourceValue(resourceCapacities.get('memory')!.total, 'bytes')}
              </div>
            ) : null}
            {resourceErrors.memory ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-danger">{resourceErrors.memory}</div>
            ) : null}
          </Field>
          <Field label="GPU">
            <Input
              value={form.gpu}
              onChange={(e) => update({ gpu: e.target.value })}
              placeholder="0"
            />
            {resourceCapacities.get('gpu') ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">
                available {formatResourceValue(resourceCapacities.get('gpu')!.total, 'count')}
              </div>
            ) : null}
            {resourceErrors.gpu ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-danger">{resourceErrors.gpu}</div>
            ) : null}
          </Field>
          {/* TODO(niu-758): bring cluster selection back once the canonical forge cluster surface is finalized. */}
        </SectionCard>
      </div>

      <SectionCard
        title="Access"
        description="Attach credentials and enabled integrations to the session."
      >
        <div className="niuu:grid niuu:grid-cols-2 niuu:gap-6">
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-secondary">
              Credentials
            </span>
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
              {credentials.map((credential) => (
                <label
                  key={credential.name}
                  className={`vol-launch-wizard__access-option ${
                    form.selectedCredentials.includes(credential.name)
                      ? 'vol-launch-wizard__access-option--checked'
                      : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    className="vol-launch-wizard__access-option-input"
                    checked={form.selectedCredentials.includes(credential.name)}
                    onChange={(event) =>
                      update({
                        selectedCredentials: event.target.checked
                          ? [...form.selectedCredentials, credential.name]
                          : form.selectedCredentials.filter((name) => name !== credential.name),
                      })
                    }
                    aria-label={credential.name}
                  />
                  <span className="vol-launch-wizard__access-option-box" aria-hidden="true">
                    {form.selectedCredentials.includes(credential.name) ? '✓' : ''}
                  </span>
                  <span className="niuu:font-mono">{credential.name}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-secondary">
              Integrations
            </span>
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
              {integrations.map((integration) => (
                <label
                  key={integration.id}
                  className={`vol-launch-wizard__access-option vol-launch-wizard__access-option--stacked ${
                    form.selectedIntegrations.includes(integration.id)
                      ? 'vol-launch-wizard__access-option--checked'
                      : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    className="vol-launch-wizard__access-option-input"
                    checked={form.selectedIntegrations.includes(integration.id)}
                    onChange={(event) =>
                      update({
                        selectedIntegrations: event.target.checked
                          ? [...form.selectedIntegrations, integration.id]
                          : form.selectedIntegrations.filter((id) => id !== integration.id),
                      })
                    }
                    aria-label={formatIntegrationLabel(integration)}
                  />
                  <span className="vol-launch-wizard__access-option-box" aria-hidden="true">
                    {form.selectedIntegrations.includes(integration.id) ? '✓' : ''}
                  </span>
                  <span className="niuu:flex niuu:flex-col">
                    <span>{formatIntegrationLabel(integration)}</span>
                    {formatIntegrationMeta(integration) ? (
                      <span className="niuu:text-[11px] niuu:text-text-faint">
                        {formatIntegrationMeta(integration)}
                      </span>
                    ) : null}
                  </span>
                </label>
              ))}
            </div>
          </div>
        </div>
      </SectionCard>

      <AdvancedRuntimeSection
        form={form}
        update={update}
        availableMcpServers={availableMcpServers}
      />
    </div>
  );
}
