import { useIntegrations, useSetupState } from './hooks';
import { NiuuMark } from './icons';
import { summarizeConnections } from './FinishStep';
import './SetupPage.css';

const DESTINATIONS = [
  {
    href: '/volundr',
    title: 'Run your first session',
    desc: 'Open a repository and hand Claude or Codex a task in an isolated sandbox.',
  },
  {
    href: '/ting',
    title: 'Turn an issue into a workflow',
    desc: 'Pick an issue from your tracker; Ting plans it into a saga with gates you approve.',
  },
  {
    href: '/ravn',
    title: 'Meet the residents',
    desc: 'Long-lived Ravns that watch your environment and ask before they act.',
  },
];

export function ReadyPage() {
  const stateQuery = useSetupState();
  const integrationsQuery = useIntegrations();
  const rows = summarizeConnections(integrationsQuery.data);
  return (
    <div className="setup-page setup-page--hero" data-testid="ready-page">
      <div className="setup-hero">
        <div className="setup-hero__kicker">níu · ready</div>
        <div className="setup-hero__mark">
          <NiuuMark size={56} />
        </div>
        <h1 className="setup-hero__title">Niuu is running.</h1>
        <p className="setup-lede">
          {stateQuery.data?.mode ? `${stateQuery.data.mode} mode. ` : ''}
          Here are three good first things to do.
        </p>
        <div className="setup-chips" data-testid="ready-summary">
          {rows.map((row) => (
            <span
              key={row.label}
              className={`setup-chip ${row.value === 'none' ? '' : 'setup-chip--ok'}`}
            >
              {row.label}: {row.value}
            </span>
          ))}
        </div>
        <div className="setup-links">
          {DESTINATIONS.map((item) => (
            <a key={item.href} className="setup-link" href={item.href}>
              <span className="setup-link__title">{item.title}</span>
              <span className="setup-link__desc">{item.desc}</span>
            </a>
          ))}
        </div>
        <a href="/" className="setup-btn setup-btn--ghost" data-testid="ready-dashboard">
          Open the dashboard
        </a>
      </div>
    </div>
  );
}
