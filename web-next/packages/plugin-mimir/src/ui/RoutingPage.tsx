import { useState, Fragment } from 'react';
import { Link } from '@tanstack/react-router';
import { StateDot, Chip } from '@niuulabs/ui';
import { useRouting } from '../application/useRouting';
import type { WriteRoutingRule } from '../domain/routing';

const INPUT_BASE =
  'niuu-flex-1 niuu-py-2 niuu-px-3 niuu-bg-bg-primary niuu-border niuu-border-solid niuu-border-border ' +
  'niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-sm niuu-outline-none niuu-box-border ' +
  'focus:niuu-border-brand';

const BTN_BASE =
  'niuu-py-2 niuu-px-4 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border ' +
  'niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-sm niuu-cursor-pointer ' +
  'disabled:niuu-opacity-50 disabled:niuu-cursor-not-allowed';

const BTN_PRIMARY = `${BTN_BASE} niuu-bg-brand niuu-border-brand niuu-text-bg-primary niuu-font-medium`;

const ACTION_BTN_BASE =
  'niuu-bg-transparent niuu-border niuu-border-solid niuu-border-border-subtle niuu-rounded-sm ' +
  'niuu-text-text-secondary niuu-font-sans niuu-text-xs niuu-py-[2px] niuu-px-2 niuu-cursor-pointer ' +
  'hover:niuu-border-border hover:niuu-text-text-primary';

const ACTION_BTN_DANGER =
  `${ACTION_BTN_BASE} niuu-text-critical niuu-border-[color-mix(in_srgb,var(--color-critical)_30%,transparent)] ` +
  'disabled:niuu-opacity-50 disabled:niuu-cursor-not-allowed';

const TH_CLS =
  'niuu-text-left niuu-py-2 niuu-px-3 niuu-text-text-muted niuu-font-medium niuu-text-xs ' +
  'niuu-border-0 niuu-border-b niuu-border-solid niuu-border-border';

const TD_BASE = 'niuu-py-2 niuu-px-3 niuu-align-middle';

const LABEL_CLS = 'niuu-text-xs niuu-text-text-muted niuu-min-w-[5rem] niuu-shrink-0';

const STATUS_CLS = 'niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary';

const EDITOR_CLS =
  'niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border niuu-rounded-md niuu-mb-4';

function newRule(): WriteRoutingRule {
  return {
    id: `route-${Date.now()}`,
    prefix: '/',
    mountName: '',
    priority: 50,
    active: true,
    desc: '',
  };
}

interface RuleFormProps {
  rule: WriteRoutingRule;
  onSave: (rule: WriteRoutingRule) => void;
  onCancel: () => void;
  isSaving: boolean;
}

function RuleForm({ rule, onSave, onCancel, isSaving }: RuleFormProps) {
  const [draft, setDraft] = useState(rule);

  function set<K extends keyof WriteRoutingRule>(key: K, value: WriteRoutingRule[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSave(draft);
  }

  return (
    <form
      className="niuu-flex niuu-flex-col niuu-gap-3"
      onSubmit={handleSubmit}
      aria-label="Routing rule editor"
    >
      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <label className={LABEL_CLS} htmlFor="rule-prefix">
          Prefix
        </label>
        <input
          id="rule-prefix"
          className={INPUT_BASE}
          type="text"
          placeholder="/infra"
          value={draft.prefix}
          onChange={(e) => set('prefix', e.target.value)}
          required
          aria-label="Path prefix"
        />
      </div>

      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <label className={LABEL_CLS} htmlFor="rule-mount">
          Mount
        </label>
        <input
          id="rule-mount"
          className={INPUT_BASE}
          type="text"
          placeholder="local"
          value={draft.mountName}
          onChange={(e) => set('mountName', e.target.value)}
          required
          aria-label="Target mount"
        />
      </div>

      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <label className={LABEL_CLS} htmlFor="rule-priority">
          Priority
        </label>
        <input
          id="rule-priority"
          className={`${INPUT_BASE} niuu-flex-none niuu-w-24`}
          type="number"
          min={0}
          value={draft.priority}
          onChange={(e) => set('priority', Number(e.target.value))}
          required
          aria-label="Priority"
        />
      </div>

      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <label className={LABEL_CLS} htmlFor="rule-desc">
          Description
        </label>
        <input
          id="rule-desc"
          className={INPUT_BASE}
          type="text"
          placeholder="optional description"
          value={draft.desc ?? ''}
          onChange={(e) => set('desc', e.target.value)}
          aria-label="Description"
        />
      </div>

      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <label className={LABEL_CLS} htmlFor="rule-active">
          Active
        </label>
        <input
          id="rule-active"
          type="checkbox"
          checked={draft.active}
          onChange={(e) => set('active', e.target.checked)}
          aria-label="Active"
        />
      </div>

      <div className="niuu-flex niuu-gap-2 niuu-mt-2">
        <button type="submit" className={BTN_PRIMARY} disabled={isSaving} aria-label="Save rule">
          {isSaving ? 'saving…' : 'Save rule'}
        </button>
        <button type="button" className={BTN_BASE} onClick={onCancel} aria-label="Cancel">
          Cancel
        </button>
      </div>
    </form>
  );
}

export function RoutingPage() {
  const {
    rules,
    isLoading,
    isError,
    error,
    testPath,
    setTestPath,
    testResult,
    upsertRule,
    deleteRule,
    isSaving,
    isDeleting,
  } = useRouting();

  const [editingRule, setEditingRule] = useState<WriteRoutingRule | null>(null);
  const [isAdding, setIsAdding] = useState(false);

  function startAdd() {
    setEditingRule(null);
    setIsAdding(true);
  }

  function startEdit(rule: WriteRoutingRule) {
    setIsAdding(false);
    setEditingRule(rule);
  }

  function handleSave(rule: WriteRoutingRule) {
    upsertRule(rule);
    setEditingRule(null);
    setIsAdding(false);
  }

  function handleCancel() {
    setEditingRule(null);
    setIsAdding(false);
  }

  return (
    <div className="niuu-p-6 niuu-max-w-[960px]">
      <h2 className="niuu-text-xl niuu-font-semibold niuu-m-0 niuu-mb-2">Write Routing</h2>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-m-0 niuu-mb-1">
        Prefix-based routing rules — first match wins (lowest priority number).
      </p>
      <p className="niuu-text-xs niuu-text-text-muted niuu-m-0 niuu-mb-5">
        To ingest content (URL fetch or file upload), go to the{' '}
        <Link to="/mimir" className="niuu-text-brand hover:niuu-underline">
          Sources
        </Link>{' '}
        page.
      </p>

      {isLoading && (
        <div className={STATUS_CLS}>
          <StateDot state="processing" pulse />
          <span>loading rules…</span>
        </div>
      )}

      {isError && (
        <div className={STATUS_CLS}>
          <StateDot state="failed" />
          <span>{error instanceof Error ? error.message : 'routing load failed'}</span>
        </div>
      )}

      {!isLoading && !isError && (
        <>
          <div className="niuu-mb-4">
            <button
              className={BTN_PRIMARY}
              onClick={startAdd}
              aria-label="Add rule"
              disabled={isAdding}
            >
              + Add rule
            </button>
          </div>

          {isAdding && (
            <div className={EDITOR_CLS} data-testid="rule-editor">
              <h3 className="niuu-text-sm niuu-font-semibold niuu-m-0 niuu-mb-4">New rule</h3>
              <RuleForm
                rule={newRule()}
                onSave={handleSave}
                onCancel={handleCancel}
                isSaving={isSaving}
              />
            </div>
          )}

          {rules.length === 0 && !isAdding && (
            <p className="niuu-text-sm niuu-text-text-muted niuu-mb-6">
              No routing rules configured. All writes go to the default mount.
            </p>
          )}

          {rules.length > 0 && (
            <table
              className="niuu-w-full niuu-border-collapse niuu-mb-8 niuu-text-sm"
              aria-label="Routing rules"
            >
              <thead>
                <tr>
                  <th className={TH_CLS}>Priority</th>
                  <th className={TH_CLS}>Prefix</th>
                  <th className={TH_CLS}>Mount</th>
                  <th className={TH_CLS}>Active</th>
                  <th className={TH_CLS}>Description</th>
                  <th className={TH_CLS}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <Fragment key={rule.id}>
                    <tr
                      className="niuu-border-0 niuu-border-b niuu-border-solid niuu-border-border-subtle hover:niuu-bg-bg-secondary"
                      data-testid="routing-rule-row"
                    >
                      <td
                        className={`${TD_BASE} niuu-font-mono niuu-text-xs niuu-text-text-muted niuu-w-16`}
                      >
                        {rule.priority}
                      </td>
                      <td className={TD_BASE}>
                        <code className="niuu-font-mono niuu-text-xs niuu-bg-bg-tertiary niuu-py-[2px] niuu-px-2 niuu-rounded-sm">
                          {rule.prefix}
                        </code>
                      </td>
                      <td className={TD_BASE}>
                        <Chip tone="muted">{rule.mountName}</Chip>
                      </td>
                      <td className={TD_BASE}>
                        <span
                          className={`niuu-font-mono niuu-text-xs ${rule.active ? 'niuu-text-brand-200' : 'niuu-text-text-muted'}`}
                        >
                          {rule.active ? 'yes' : 'no'}
                        </span>
                      </td>
                      <td className={`${TD_BASE} niuu-text-text-secondary niuu-max-w-[20rem]`}>
                        {rule.desc ?? '—'}
                      </td>
                      <td className={`${TD_BASE} niuu-flex niuu-gap-2 niuu-items-center`}>
                        <button
                          className={ACTION_BTN_BASE}
                          onClick={() => startEdit(rule)}
                          aria-label={`Edit rule ${rule.prefix}`}
                        >
                          Edit
                        </button>
                        <button
                          className={ACTION_BTN_DANGER}
                          onClick={() => deleteRule(rule.id)}
                          disabled={isDeleting}
                          aria-label={`Delete rule ${rule.prefix}`}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                    {editingRule?.id === rule.id && (
                      <tr key={`${rule.id}-edit`}>
                        <td colSpan={6} className="niuu-p-0">
                          <div className={EDITOR_CLS} data-testid="rule-editor">
                            <h3 className="niuu-text-sm niuu-font-semibold niuu-m-0 niuu-mb-4">
                              Edit rule
                            </h3>
                            <RuleForm
                              rule={editingRule}
                              onSave={handleSave}
                              onCancel={handleCancel}
                              isSaving={isSaving}
                            />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}

          {/* Test pane */}
          <section
            className="niuu-border-t niuu-border-solid niuu-border-border niuu-pt-6"
            aria-label="Route test pane"
          >
            <h3 className="niuu-text-base niuu-font-semibold niuu-m-0 niuu-mb-2">Test a path</h3>
            <p className="niuu-text-sm niuu-text-text-secondary niuu-m-0 niuu-mb-3">
              Enter a page path to see which rule and mount it would be routed to.
            </p>
            <div className="niuu-mb-3">
              <input
                className={`${INPUT_BASE} niuu-flex-none niuu-w-full`}
                type="text"
                placeholder="/infra/k8s"
                value={testPath}
                onChange={(e) => setTestPath(e.target.value)}
                aria-label="Test path"
                data-testid="test-path-input"
              />
            </div>
            {testResult && (
              <div
                className={[
                  'niuu-flex niuu-items-center niuu-gap-2 niuu-p-3 niuu-rounded-md niuu-text-sm niuu-border niuu-border-solid',
                  testResult.mountName
                    ? 'niuu-bg-[color-mix(in_srgb,var(--brand-500)_10%,transparent)] niuu-border-[color-mix(in_srgb,var(--brand-500)_25%,transparent)]'
                    : 'niuu-bg-bg-secondary niuu-border-border-subtle',
                ].join(' ')}
                data-testid="test-result"
              >
                {testResult.mountName ? (
                  <>
                    <Chip tone="brand">{testResult.mountName}</Chip>
                    <span className="niuu-text-text-secondary niuu-font-mono niuu-text-xs">
                      {testResult.reason}
                    </span>
                  </>
                ) : (
                  <span className="niuu-text-text-muted niuu-font-mono niuu-text-xs">
                    {testResult.reason}
                  </span>
                )}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
