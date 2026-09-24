import { useMemo, useState } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import type { Ravn } from '../../domain/ravn';
import { ravnKey } from '../../domain/residentActions';
import {
  RAVN_FILTERS,
  RAVN_GROUPINGS,
  defaultRavn,
  type RavnFilter,
  type RavnGrouping,
} from '../../application/ravnWorkbench';
import { useRavens } from '../hooks/useRavens';
import { loadStorage, saveStorage } from '../storage';
import { ResidentFlockDeployDialog } from '../ResidentFlockDeployDialog';
import { RavnList } from './RavnList';
import { RavnPane, isRavnTab, type RavnTab } from './RavnPane';
import { DeployRavnDialog } from './DeployRavnDialog';
import { errorText } from './errorText';
import './workbench.css';

const FILTER_KEY = 'ravn.workbench.filter';
const GROUPING_KEY = 'ravn.workbench.grouping';

/** The workbench's place in the URL: which ravn, which tab, which conversation. */
export interface RavnWorkbenchSearch {
  ravn?: string;
  instance_id?: string;
  tab?: RavnTab;
  session?: string;
  /** Open the deploy dialog with this persona picked (the persona library links here). */
  deploy?: string;
}

function readSearch(raw: Record<string, unknown>): RavnWorkbenchSearch {
  const text = (value: unknown) => (typeof value === 'string' && value ? value : undefined);
  return {
    ravn: text(raw.ravn),
    instance_id: text(raw.instance_id),
    tab: isRavnTab(raw.tab) ? raw.tab : undefined,
    session: text(raw.session),
    deploy: text(raw.deploy),
  };
}

function findRavn(ravens: Ravn[], search: RavnWorkbenchSearch): Ravn | null {
  if (!search.ravn) return null;
  return (
    ravens.find(
      (ravn) =>
        ravn.id === search.ravn && (!search.instance_id || ravn.instanceId === search.instance_id),
    ) ?? null
  );
}

function storedChoice<T extends string>(key: string, allowed: Array<{ id: T }>, initial: T): T {
  const stored = loadStorage<string>(key, initial);
  return allowed.some((option) => option.id === stored) ? (stored as T) : initial;
}

export function RavnWorkbench() {
  const ravens = useRavens();
  const navigate = useNavigate();
  const search = readSearch(useSearch({ strict: false }) as Record<string, unknown>);
  const [filter, setFilter] = useState<RavnFilter>(() =>
    storedChoice(FILTER_KEY, RAVN_FILTERS, 'all'),
  );
  const [grouping, setGrouping] = useState<RavnGrouping>(() =>
    storedChoice(GROUPING_KEY, RAVN_GROUPINGS, 'state'),
  );
  const [query, setQuery] = useState('');
  const [deployOpen, setDeployOpen] = useState(false);
  const [flockOpen, setFlockOpen] = useState(false);

  const list = useMemo(() => ravens.data ?? [], [ravens.data]);
  const requested = findRavn(list, search);
  const selected = requested ?? defaultRavn(list);
  const tab: RavnTab = search.tab ?? 'chat';

  function go(next: RavnWorkbenchSearch, replace = false) {
    void navigate({ to: '/ravn' as never, search: next as never, replace });
  }

  function select(ravn: Ravn) {
    go({ ravn: ravn.id, instance_id: ravn.instanceId, tab });
  }

  if (ravens.isLoading) {
    return (
      <div className="rw-state-screen" data-testid="ravn-workbench-loading">
        <LoadingState label="Loading ravens…" />
      </div>
    );
  }

  if (ravens.isError) {
    return (
      <div className="rw-state-screen" data-testid="ravn-workbench-error">
        <ErrorState
          title="Ravens could not be loaded"
          message={errorText(ravens.error, 'The Ravn API did not answer')}
          action={
            <button type="button" className="rw-btn" onClick={() => void ravens.refetch()}>
              Try again
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="rw" data-detail-open={Boolean(requested)} data-testid="ravn-workbench">
      <RavnList
        ravens={list}
        selectedKey={selected ? ravnKey(selected) : null}
        onSelect={select}
        filter={filter}
        onFilterChange={(next) => {
          setFilter(next);
          saveStorage(FILTER_KEY, next);
        }}
        grouping={grouping}
        onGroupingChange={(next) => {
          setGrouping(next);
          saveStorage(GROUPING_KEY, next);
        }}
        query={query}
        onQueryChange={setQuery}
        onDeploy={() => setDeployOpen(true)}
      />

      {selected ? (
        <RavnPane
          key={ravnKey(selected)}
          ravn={selected}
          tab={tab}
          onTabChange={(nextTab) =>
            go(
              {
                ravn: selected.id,
                instance_id: selected.instanceId,
                tab: nextTab,
                session: search.session,
              },
              true,
            )
          }
          sessionId={search.session ?? null}
          onSessionChange={(sessionId) =>
            go(
              {
                ravn: selected.id,
                instance_id: selected.instanceId,
                tab: 'chat',
                ...(sessionId && { session: sessionId }),
              },
              true,
            )
          }
          onOpenPersona={(name) =>
            void navigate({ to: '/ravn/personas' as never, search: { persona: name } as never })
          }
          onBack={() => go({})}
          onDeleted={() => go({}, true)}
        />
      ) : (
        <section className="rw-detail" data-testid="ravn-workbench-empty">
          <div className="rw-empty">
            <div className="rw-empty__inner">
              <h3 className="rw-empty__title">No ravens yet</h3>
              <p className="rw-empty__text">
                A ravn is a model and runtime running as one of your personas. Deploy one to talk
                with it here.
              </p>
              <div className="rw-empty__acts">
                <button
                  type="button"
                  className="rw-btn rw-btn--primary"
                  onClick={() => setDeployOpen(true)}
                >
                  Deploy a ravn
                </button>
              </div>
            </div>
          </div>
        </section>
      )}

      <DeployRavnDialog
        key={search.deploy ?? ''}
        open={deployOpen || Boolean(search.deploy)}
        initialPersona={search.deploy}
        onOpenChange={(open) => {
          setDeployOpen(open);
          if (!open && search.deploy) go({ ...search, deploy: undefined }, true);
        }}
        onDeployed={(ravn) => go({ ravn: ravn.id, instance_id: ravn.instanceId, tab: 'activity' })}
        onDeployFlock={() => setFlockOpen(true)}
      />
      <ResidentFlockDeployDialog
        open={flockOpen}
        onOpenChange={setFlockOpen}
        onDeployed={(deployed) => {
          const first = deployed[0];
          if (first) go({ ravn: first.id, instance_id: first.instanceId, tab: 'activity' });
        }}
      />
    </div>
  );
}
