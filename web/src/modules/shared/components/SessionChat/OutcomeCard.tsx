/* eslint-disable react-refresh/only-export-components -- regexes and parser are tightly coupled to this component */
import { useState } from 'react';
import styles from './OutcomeCard.module.css';

/* ------------------------------------------------------------------ */
/*  Regexes — exported so callers can split/extract outcome blocks     */
/* ------------------------------------------------------------------ */

export const OUTCOME_RE = /(---outcome---[\s\S]*?(?:---end---|(?:^|\n)---(?:\s*$|\n)))/gim;

export const OUTCOME_EXTRACT_RE =
  /---outcome---\s*([\s\S]*?)(?:---end---|(?:^|\n)---(?:\s*$|\n))/im;

/* ------------------------------------------------------------------ */
/*  Known verdicts                                                      */
/* ------------------------------------------------------------------ */

const KNOWN_VERDICTS = new Set(['approve', 'pass', 'retry', 'escalate', 'fail']);

/* ------------------------------------------------------------------ */
/*  parseOutcomeFields                                                  */
/* ------------------------------------------------------------------ */

export function parseOutcomeFields(raw: string): Record<string, string> {
  const fields: Record<string, string> = {};

  const lines = raw
    .split('\n')
    .map(l => l.trim())
    .filter(l => l && !l.startsWith('#'));

  if (lines.length > 1) {
    for (const line of lines) {
      const idx = line.indexOf(':');
      if (idx < 1) continue;
      fields[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    return fields;
  }

  const text = lines[0] ?? raw.trim();
  const pattern = /(\w+):\s*/g;
  const matches = [...text.matchAll(pattern)];
  for (let i = 0; i < matches.length; i++) {
    const key = matches[i][1];
    const start = matches[i].index! + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index! : text.length;
    fields[key] = text.slice(start, end).trim();
  }
  return fields;
}

/* ------------------------------------------------------------------ */
/*  OutcomeCard                                                         */
/* ------------------------------------------------------------------ */

interface OutcomeCardProps {
  yaml: string;
}

export function OutcomeCard({ yaml }: OutcomeCardProps) {
  const [showRaw, setShowRaw] = useState(false);
  const fields = parseOutcomeFields(yaml);
  const verdict = fields['verdict'] ?? '';
  const knownVerdict = KNOWN_VERDICTS.has(verdict) ? verdict : 'unknown';

  return (
    <div className={styles.outcomeCard}>
      <div className={styles.outcomeHeader}>
        <span className={styles.outcomeLabel}>Outcome</span>
        <div className={styles.outcomeHeaderRight}>
          {verdict && (
            <span className={styles.outcomeBadge} data-verdict={knownVerdict}>
              {verdict}
            </span>
          )}
          <button
            type="button"
            className={styles.outcomeRawToggle}
            onClick={() => setShowRaw(prev => !prev)}
            aria-expanded={showRaw}
          >
            {showRaw ? 'Hide raw' : 'Show raw'}
          </button>
        </div>
      </div>
      <div className={styles.outcomeFields}>
        {Object.entries(fields)
          .filter(([k]) => k !== 'verdict')
          .map(([key, value]) => (
            <div key={key} className={styles.outcomeField}>
              <span className={styles.outcomeKey}>{key}</span>
              <span className={styles.outcomeValue}>{value}</span>
            </div>
          ))}
      </div>
      {showRaw && (
        <div className={styles.outcomeRaw}>
          <pre className={styles.outcomeRawYaml}>{yaml.trim()}</pre>
        </div>
      )}
    </div>
  );
}
