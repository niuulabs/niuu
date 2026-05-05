import { useState } from 'react';
import { LoadingIndicator } from '@/modules/shared';
import { useDispatchQueue } from '../../hooks/useDispatchQueue';
import type { QueueItem } from '../../hooks/useDispatchQueue';
import { FlockToggle } from '../../components/FlockToggle';
import styles from './DispatcherView.module.css';

export function DispatcherView() {
  const { queue, defaults, clusters, loading, error, dispatching, dispatch } = useDispatchQueue();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [modelOverride, setModelOverride] = useState<string | null>(null);
  const [promptOverride, setPromptOverride] = useState<string | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<string>('');
  const [flockEnabled, setFlockEnabled] = useState(false);
  const [selectedPersonas, setSelectedPersonas] = useState<string[]>([]);
  const [submittingItems, setSubmittingItems] = useState<QueueItem[]>([]);
  const [lastResults, setLastResults] = useState<
    { issue_id: string; session_name: string; status: string; cluster_name: string }[] | null
  >(null);

  const activeSubmittingItems =
    submittingItems.length > 0
      ? submittingItems
      : dispatching
        ? queue.filter(item => selected.has(item.issue_id))
        : [];
  const isSubmitting = dispatching || activeSubmittingItems.length > 0;
  const submittingIssueIds = new Set(activeSubmittingItems.map(item => item.issue_id));

  const toggleItem = (issueId: string) => {
    if (isSubmitting) {
      return;
    }

    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(issueId)) {
        next.delete(issueId);
      } else {
        next.add(issueId);
      }
      return next;
    });
  };

  const selectAll = () => {
    if (isSubmitting) {
      return;
    }

    setSelected(new Set(queue.map(q => q.issue_id)));
  };

  const clearSelection = () => {
    if (isSubmitting) {
      return;
    }

    setSelected(new Set());
  };

  const handleDispatch = async () => {
    const selectedQueueItems = queue.filter(q => selected.has(q.issue_id));
    const items = selectedQueueItems.map(q => ({
      saga_id: q.saga_id,
      issue_id: q.issue_id,
      repo: q.repos[0] || '',
    }));

    if (items.length === 0) {
      return;
    }

    const workloadType = flockEnabled ? 'ravn_flock' : undefined;
    const workloadConfig = flockEnabled ? { personas: selectedPersonas } : undefined;
    setSubmittingItems(selectedQueueItems);

    try {
      const results = await dispatch(
        items,
        modelOverride ?? defaults.default_model,
        promptOverride ?? defaults.default_system_prompt,
        selectedCluster || undefined,
        workloadType,
        workloadConfig
      );
      setLastResults(results);
      setSelected(new Set());
    } finally {
      setSubmittingItems([]);
    }
  };

  if (loading) {
    return <LoadingIndicator messages={['Loading dispatch queue...']} />;
  }

  if (error) {
    return <div className={styles.error}>{error}</div>;
  }

  // Group by saga
  const bySaga: Record<string, { sagaName: string; items: QueueItem[] }> = {};
  for (const item of queue) {
    if (!bySaga[item.saga_id]) {
      bySaga[item.saga_id] = { sagaName: item.saga_name, items: [] };
    }
    bySaga[item.saga_id].items.push(item);
  }

  const enabledClusters = clusters.filter(c => c.enabled);

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h2 className={styles.heading}>Dispatch Queue</h2>
        <span className={styles.queueCount}>{queue.length} ready</span>
      </div>

      {lastResults && (
        <div className={styles.results}>
          {lastResults.map(r => (
            <div
              key={r.issue_id}
              className={r.status === 'spawned' ? styles.resultSuccess : styles.resultFailed}
            >
              {r.status === 'spawned' ? '\u2713' : '\u2717'} {r.session_name || r.issue_id} —{' '}
              {r.status}
              {r.cluster_name && <span className={styles.clusterBadge}>{r.cluster_name}</span>}
            </div>
          ))}
          <button
            type="button"
            className={styles.dismissButton}
            onClick={() => setLastResults(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {activeSubmittingItems.length > 0 && (
        <div className={styles.submittingPanel} role="status" aria-live="polite">
          <div className={styles.submittingHeader}>
            <span className={styles.submittingPulse} aria-hidden="true" />
            <div>
              <div className={styles.submittingTitle}>
                Dispatching {activeSubmittingItems.length} item
                {activeSubmittingItems.length === 1 ? '' : 's'}
              </div>
              <div className={styles.submittingDescription}>
                Keeping the batch visible here while Tyr submits it.
              </div>
            </div>
          </div>
          <div className={styles.submittingList}>
            {activeSubmittingItems.map(item => (
              <div key={item.issue_id} className={styles.submittingItem}>
                <span className={styles.submittingIdentifier}>{item.identifier}</span>
                <span className={styles.submittingItemTitle}>{item.title}</span>
                <span className={styles.submittingBadge}>Submitting</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {queue.length > 0 && (
        <div className={styles.controls}>
          <div className={styles.selectionControls}>
            <button
              type="button"
              className={styles.selectButton}
              onClick={selectAll}
              disabled={isSubmitting}
            >
              Select All
            </button>
            <button
              type="button"
              className={styles.selectButton}
              onClick={clearSelection}
              disabled={isSubmitting}
            >
              Clear
            </button>
            <span className={styles.selectedCount}>{selected.size} selected</span>
          </div>
          <div className={styles.dispatchControls}>
            {enabledClusters.length > 1 && (
              <select
                className={styles.clusterSelect}
                value={selectedCluster}
                onChange={e => setSelectedCluster(e.target.value)}
                disabled={isSubmitting}
              >
                <option value="">Auto (default cluster)</option>
                {enabledClusters.map(c => (
                  <option key={c.connection_id} value={c.connection_id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
            <select
              className={styles.modelSelect}
              value={modelOverride ?? defaults.default_model}
              onChange={e => setModelOverride(e.target.value)}
              disabled={isSubmitting}
            >
              {defaults.models.map(m => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className={styles.dispatchButton}
              onClick={handleDispatch}
              disabled={selected.size === 0 || isSubmitting}
            >
              {isSubmitting ? 'Dispatching...' : `Dispatch ${selected.size}`}
            </button>
          </div>
        </div>
      )}
      {queue.length > 0 && defaults.flock_enabled && (
        <FlockToggle
          enabled={flockEnabled}
          onToggle={setFlockEnabled}
          personas={defaults.flock_default_personas}
          selectedPersonas={selectedPersonas}
          onPersonasChange={setSelectedPersonas}
        />
      )}

      {Object.entries(bySaga).map(([sagaId, { sagaName, items }]) => (
        <div key={sagaId} className={styles.sagaGroup}>
          <div className={styles.sagaHeader}>{sagaName}</div>
          {items.map(item => (
            <label
              key={item.issue_id}
              className={
                submittingIssueIds.has(item.issue_id)
                  ? `${styles.queueItem} ${styles.queueItemSubmitting}`
                  : styles.queueItem
              }
            >
              <input
                type="checkbox"
                checked={selected.has(item.issue_id) || submittingIssueIds.has(item.issue_id)}
                onChange={() => toggleItem(item.issue_id)}
                className={styles.checkbox}
                disabled={isSubmitting}
              />
              <span className={styles.itemIdentifier}>{item.identifier}</span>
              <span className={styles.itemTitle}>{item.title}</span>
              <span className={styles.itemPhase}>{item.phase_name}</span>
              {item.priority_label && (
                <span className={styles.itemPriority}>{item.priority_label}</span>
              )}
              {item.estimate != null && (
                <span className={styles.itemEstimate}>{item.estimate}pt</span>
              )}
              {submittingIssueIds.has(item.issue_id) && (
                <span className={styles.itemDispatchState}>Submitting</span>
              )}
            </label>
          ))}
        </div>
      ))}

      {queue.length === 0 && <div className={styles.empty}>No issues ready for dispatch</div>}

      {queue.length > 0 && (
        <div className={styles.promptSection}>
          <label className={styles.promptLabel}>System Prompt (optional)</label>
          <textarea
            className={styles.promptTextarea}
            value={promptOverride ?? defaults.default_system_prompt}
            onChange={e => setPromptOverride(e.target.value)}
            placeholder="Additional instructions for all dispatched sessions..."
            rows={3}
            disabled={isSubmitting}
          />
        </div>
      )}
    </div>
  );
}
