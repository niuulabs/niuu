import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listPersonas } from '../api/client';
import { MOCK_PERSONAS } from '../api/mockData';
import type { PersonaSummary, PersonaFilter } from '../api/types';
import { PersonaCard } from '../components/PersonaCard';
import { cn } from '@/modules/shared/utils/classnames';
import styles from './PersonasView.module.css';

const FILTERS: { key: PersonaFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'builtin', label: 'Built-in' },
  { key: 'custom', label: 'Custom' },
];

function filterMockPersonas(filter: PersonaFilter): PersonaSummary[] {
  if (filter === 'builtin') return MOCK_PERSONAS.filter(p => p.isBuiltin);
  if (filter === 'custom') return MOCK_PERSONAS.filter(p => !p.isBuiltin);
  return MOCK_PERSONAS;
}

export function PersonasView() {
  const navigate = useNavigate();
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [loadedFilter, setLoadedFilter] = useState<PersonaFilter | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<PersonaFilter>('all');
  const [usingMock, setUsingMock] = useState(false);

  const loading = loadedFilter !== filter;

  useEffect(() => {
    listPersonas(filter)
      .then(data => {
        if (data.length > 0) {
          setPersonas(data);
          setUsingMock(false);
        } else {
          setPersonas(filterMockPersonas(filter));
          setUsingMock(true);
        }
        setError(null);
        setLoadedFilter(filter);
      })
      .catch(() => {
        setPersonas(filterMockPersonas(filter));
        setUsingMock(true);
        setError(null);
        setLoadedFilter(filter);
      });
  }, [filter]);

  return (
    <div className={styles.container}>
      <div className={styles.toolbar}>
        <div className={styles.filterBar}>
          {FILTERS.map(f => (
            <button
              key={f.key}
              className={cn(styles.filterButton, filter === f.key && styles.filterButtonActive)}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <button className={styles.newButton} onClick={() => navigate('/ravn/personas/~new')}>
          New Persona
        </button>
      </div>

      {usingMock && <div className={styles.demoBanner}>Demo data — backend not connected</div>}

      {loading && <div className={styles.status}>Loading personas…</div>}

      {!loading && error && <div className={styles.error}>{error}</div>}

      {!loading && !error && personas.length === 0 && (
        <div className={styles.status}>No personas found.</div>
      )}

      {!loading && !error && personas.length > 0 && (
        <div className={styles.grid}>
          {personas.map(persona => (
            <PersonaCard key={persona.name} persona={persona} />
          ))}
        </div>
      )}
    </div>
  );
}
