import { useRef, useState } from 'react';
import type { Workflow } from '../domain/workflow';
import type { TingPersonaSummary, WorkflowImportSource } from '../ports';
import type { WorkflowRegistryMount } from './WorkflowBuilder/mimirRegistry';
import { useApplyWorkflowImport, usePreviewWorkflowImport } from './useWorkflows';
import { readWorkflowFile } from './workflowFiles';

export interface WorkflowImportDialogProps {
  open: boolean;
  workflows: Workflow[];
  personas: TingPersonaSummary[];
  registryMounts: WorkflowRegistryMount[];
  onClose(): void;
  onImported(workflow: Workflow): void;
}

const INPUT =
  'niuu:w-full niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-2.5 niuu:py-2 niuu:text-xs niuu:text-text-primary niuu:font-sans';
const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-secondary niuu:cursor-pointer niuu:hover:text-text-primary niuu:disabled:opacity-50';

export function WorkflowImportDialog({
  open,
  workflows,
  personas,
  registryMounts,
  onClose,
  onImported,
}: WorkflowImportDialogProps) {
  const previewMutation = usePreviewWorkflowImport();
  const applyMutation = useApplyWorkflowImport();
  const [filename, setFilename] = useState('');
  const [content, setContent] = useState('');
  const [mappings, setMappings] = useState<Record<string, string>>({});
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<'copy' | 'update'>('copy');
  const [readError, setReadError] = useState<string | null>(null);
  const [previewedRequestKey, setPreviewedRequestKey] = useState<string | null>(null);
  const fileSelection = useRef(0);
  const preview = previewMutation.data;
  const existing = workflows.find((workflow) => workflow.id === preview?.workflow.id) ?? null;

  if (!open) return null;

  function requestFor(nextPreviewDigest?: string): WorkflowImportSource {
    return {
      content,
      filename,
      mappings,
      bindings,
      mode,
      workflowId: mode === 'update' ? existing?.id : undefined,
      expectedRevision: mode === 'update' ? (existing?.revision ?? undefined) : undefined,
      previewDigest: nextPreviewDigest,
    };
  }

  function requestKey(request: WorkflowImportSource): string {
    return JSON.stringify({ ...request, previewDigest: undefined });
  }

  const currentRequestKey = requestKey(requestFor());
  const previewIsCurrent = previewedRequestKey === currentRequestKey;

  async function selectFile(file: File | undefined) {
    const selection = ++fileSelection.current;
    previewMutation.reset();
    applyMutation.reset();
    setPreviewedRequestKey(null);
    setReadError(null);
    setMappings({});
    setBindings({});
    if (!file) {
      setFilename('');
      setContent('');
      return;
    }
    setFilename(file.name);
    setContent('');
    try {
      const nextContent = await readWorkflowFile(file);
      if (selection !== fileSelection.current) return;
      setContent(nextContent);
    } catch (error) {
      if (selection !== fileSelection.current) return;
      setContent('');
      setReadError(error instanceof Error ? error.message : 'Could not read workflow file.');
    }
  }

  async function previewImport() {
    const request = requestFor();
    const key = requestKey(request);
    setPreviewedRequestKey(null);
    applyMutation.reset();
    try {
      await previewMutation.mutateAsync(request);
      if (key === requestKey(requestFor())) setPreviewedRequestKey(key);
    } catch {
      // The mutation state renders the backend's actionable preview error.
    }
  }

  async function applyImport() {
    if (!preview || !previewIsCurrent || !preview.canApply) return;
    try {
      const imported = await applyMutation.mutateAsync(requestFor(preview.previewDigest));
      onImported(imported);
      onClose();
    } catch {
      // The mutation state renders the backend's actionable import error.
    }
  }

  const unresolvedRequirements = preview?.requirements.filter((requirement) => {
    if (requirement.resolved) return false;
    return !(bindings[requirement.id] ?? requirement.binding);
  });

  return (
    <div
      data-testid="workflow-import-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="Import workflow"
      className="niuu:fixed niuu:inset-0 niuu:z-50 niuu:flex niuu:items-center niuu:justify-center niuu:bg-bg-primary/80 niuu:p-6"
    >
      <div className="niuu:flex niuu:max-h-[90vh] niuu:w-full niuu:max-w-[760px] niuu:flex-col niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-secondary niuu:shadow-xl">
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:border-b niuu:border-border niuu:px-5 niuu:py-4">
          <div>
            <h2 className="niuu:m-0 niuu:text-base niuu:font-semibold niuu:text-text-primary">
              Import workflow
            </h2>
            <p className="niuu:mt-1 niuu:mb-0 niuu:text-xs niuu:text-text-muted">
              Preview persona pins and local bindings before anything is written.
            </p>
          </div>
          <button type="button" className={BUTTON} onClick={onClose}>
            Close
          </button>
        </div>

        <div className="niuu:flex-1 niuu:overflow-y-auto niuu:p-5 niuu:flex niuu:flex-col niuu:gap-4">
          <label className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:text-xs niuu:text-text-secondary">
            Workflow YAML or bundle ZIP
            <input
              data-testid="workflow-import-file"
              type="file"
              accept=".yaml,.yml,.zip,application/yaml,application/zip"
              className={INPUT}
              onChange={(event) => void selectFile(event.target.files?.[0])}
            />
          </label>
          {readError ? (
            <p className="niuu:m-0 niuu:text-xs niuu:text-critical">{readError}</p>
          ) : null}

          <button
            type="button"
            data-testid="preview-workflow-import"
            className={BUTTON}
            disabled={!content || previewMutation.isPending}
            onClick={() => void previewImport()}
          >
            {previewMutation.isPending ? 'Checking…' : 'Preview import'}
          </button>

          {preview ? (
            <div
              className="niuu:flex niuu:flex-col niuu:gap-4"
              data-testid="workflow-import-preview"
            >
              <section className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3">
                <div className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {preview.workflow.name}
                </div>
                <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                  {preview.workflow.description || 'No description'} · v
                  {preview.workflow.version ?? 'draft'}
                </div>
                {existing ? (
                  <label className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-1 niuu:text-xs niuu:text-text-secondary">
                    This workflow identity already exists
                    <select
                      data-testid="workflow-import-mode"
                      className={INPUT}
                      value={mode}
                      onChange={(event) => {
                        applyMutation.reset();
                        setMode(event.target.value as 'copy' | 'update');
                      }}
                    >
                      <option value="copy">Import as a new workflow</option>
                      <option value="update" disabled={existing.readOnly}>
                        Update existing workflow
                      </option>
                    </select>
                  </label>
                ) : null}
              </section>

              {!!preview.workflows?.length && (
                <section aria-label="Child workflow dependencies">
                  <h3 className="niuu:text-xs niuu:text-text-muted">Child workflow dependencies</h3>
                  <ul>
                    {preview.workflows.map((child) => (
                      <li key={child.alias} className="niuu:text-xs niuu:text-text-secondary">
                        {child.alias} · {child.status} · {child.revision ?? child.id}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              <section>
                <h3 className="niuu:mt-0 niuu:mb-2 niuu:text-xs niuu:uppercase niuu:tracking-wide niuu:text-text-muted">
                  Persona dependencies
                </h3>
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {preview.personas.length === 0 ? (
                    <p className="niuu:m-0 niuu:text-xs niuu:text-text-faint">
                      No persona dependencies.
                    </p>
                  ) : null}
                  {preview.personas.map((persona) => {
                    const needsMapping = ['missing', 'conflict'].includes(persona.status);
                    return (
                      <div
                        key={persona.alias}
                        className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3"
                      >
                        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                          <span className="niuu:text-xs niuu:font-semibold niuu:text-text-primary">
                            {persona.alias}
                          </span>
                          <span className="niuu:text-[10px] niuu:font-mono niuu:uppercase niuu:text-text-muted">
                            {persona.status}
                          </span>
                        </div>
                        <div className="niuu:mt-1 niuu:text-[11px] niuu:font-mono niuu:text-text-faint">
                          {persona.id}@{persona.revision} · {persona.digest}
                        </div>
                        <p className="niuu:my-2 niuu:text-xs niuu:text-text-secondary">
                          {persona.message}
                        </p>
                        {persona.definition ? (
                          <details className="niuu:my-2 niuu:text-xs niuu:text-text-secondary">
                            <summary className="niuu:cursor-pointer niuu:text-brand">
                              Review portable persona behavior
                            </summary>
                            <div className="niuu:mt-2 niuu:flex niuu:flex-col niuu:gap-2">
                              <div>
                                Permission mode:{' '}
                                <span className="niuu:font-mono">
                                  {String(persona.definition.permission_mode ?? 'default')}
                                </span>
                              </div>
                              <div>
                                Requested tools:{' '}
                                <span className="niuu:font-mono">
                                  {Array.isArray(persona.definition.allowed_tools)
                                    ? persona.definition.allowed_tools.join(', ') || 'none'
                                    : 'none'}
                                </span>
                              </div>
                              {typeof persona.definition.system_prompt_template === 'string' ? (
                                <pre className="niuu:m-0 niuu:max-h-48 niuu:overflow-auto niuu:whitespace-pre-wrap niuu:rounded niuu:bg-bg-secondary niuu:p-2 niuu:font-mono niuu:text-[11px]">
                                  {persona.definition.system_prompt_template}
                                </pre>
                              ) : null}
                            </div>
                          </details>
                        ) : null}
                        {needsMapping ? (
                          <select
                            data-testid={`persona-mapping-${persona.alias}`}
                            className={INPUT}
                            value={mappings[persona.alias] ?? ''}
                            onChange={(event) => {
                              applyMutation.reset();
                              setMappings((current) => {
                                const next = { ...current };
                                if (event.target.value) next[persona.alias] = event.target.value;
                                else delete next[persona.alias];
                                return next;
                              });
                            }}
                          >
                            <option value="">Leave unresolved</option>
                            {personas.map((available) => (
                              <option key={available.name} value={available.name}>
                                {available.name}
                              </option>
                            ))}
                          </select>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </section>

              <section>
                <h3 className="niuu:mt-0 niuu:mb-2 niuu:text-xs niuu:uppercase niuu:tracking-wide niuu:text-text-muted">
                  Local requirements
                </h3>
                {preview.requirements.length === 0 ? (
                  <p className="niuu:m-0 niuu:text-xs niuu:text-text-faint">
                    No local bindings required.
                  </p>
                ) : null}
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {preview.requirements.map((requirement) => (
                    <label
                      key={requirement.id}
                      className="niuu:flex niuu:flex-col niuu:gap-1 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-secondary"
                    >
                      <span>
                        {requirement.message}{' '}
                        {requirement.resolved ? (
                          <span className="niuu:text-success">Resolved</span>
                        ) : (
                          <span className="niuu:text-warning">Unresolved</span>
                        )}
                      </span>
                      {requirement.kind === 'mimir' ? (
                        <select
                          data-testid={`requirement-binding-${requirement.id}`}
                          className={INPUT}
                          value={bindings[requirement.id] ?? requirement.binding ?? ''}
                          onChange={(event) => {
                            applyMutation.reset();
                            setBindings((current) => {
                              const next = { ...current };
                              if (event.target.value) next[requirement.id] = event.target.value;
                              else delete next[requirement.id];
                              return next;
                            });
                          }}
                        >
                          <option value="">Leave unresolved</option>
                          {registryMounts
                            .filter((mount) => mount.lifecycle === 'registered' && mount.enabled)
                            .map((mount) => (
                              <option key={mount.id} value={mount.id}>
                                {mount.name}
                              </option>
                            ))}
                        </select>
                      ) : (
                        <input
                          className={INPUT}
                          value={bindings[requirement.id] ?? requirement.binding ?? ''}
                          placeholder="Local binding"
                          onChange={(event) => {
                            applyMutation.reset();
                            setBindings((current) => {
                              const next = { ...current };
                              if (event.target.value) next[requirement.id] = event.target.value;
                              else delete next[requirement.id];
                              return next;
                            });
                          }}
                        />
                      )}
                    </label>
                  ))}
                </div>
              </section>

              {preview.errors.length > 0 ? (
                <ul className="niuu:m-0 niuu:pl-5 niuu:text-xs niuu:text-critical">
                  {preview.errors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              ) : null}
              {!previewIsCurrent ? (
                <p className="niuu:m-0 niuu:text-xs niuu:text-warning">
                  Preview the current mappings and bindings before importing.
                </p>
              ) : null}
              {previewMutation.error || applyMutation.error ? (
                <p className="niuu:m-0 niuu:text-xs niuu:text-critical">
                  {(previewMutation.error ?? applyMutation.error)?.message}
                </p>
              ) : null}

              <button
                type="button"
                data-testid="apply-workflow-import"
                className={BUTTON}
                disabled={
                  !preview.canApply ||
                  !previewIsCurrent ||
                  applyMutation.isPending ||
                  previewMutation.isPending
                }
                onClick={() => void applyImport()}
              >
                {applyMutation.isPending
                  ? 'Importing…'
                  : (unresolvedRequirements?.length ?? 0) > 0
                    ? 'Import unresolved draft'
                    : 'Import workflow'}
              </button>
            </div>
          ) : null}

          {previewMutation.error && !preview ? (
            <p className="niuu:m-0 niuu:text-xs niuu:text-critical">
              {previewMutation.error.message}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
