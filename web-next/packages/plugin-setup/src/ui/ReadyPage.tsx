import { useIntegrations, useSetupState } from './hooks';
import { NiuuMark } from './icons';
import { summarizeConnections } from './FinishStep';
import './SetupPage.css';

export interface Walkthrough {
  href: string;
  title: string;
  desc: string;
  /** What the user will do once there, in order. */
  steps: string[];
}

export const WALKTHROUGHS: readonly Walkthrough[] = [
  {
    href: '/volundr',
    title: 'Run your first session',
    desc: 'Open a repository and hand Claude or Codex a task in an isolated sandbox.',
    steps: [
      'Forge → New session; pick a repository or start from an empty workspace.',
      'Choose the runtime (Claude Code or Codex) and the provider you connected.',
      'Write the task; watch the chat, files and diff update as it works.',
    ],
  },
  {
    href: '/ting',
    title: 'Turn an issue into a workflow',
    desc: 'Pick an issue from your tracker; Ting plans it into a saga with gates you approve.',
    steps: [
      'Ting → Sagas → Plan from issue; pick one from the tracker you connected.',
      'Review the plan; gates are where you approve before it continues.',
      'Dispatch; each step runs as a session you can open from the saga.',
    ],
  },
  {
    href: '/ravn',
    title: 'Meet the residents',
    desc: 'Long-lived Ravns that watch your environment and ask before they act.',
    steps: [
      'Ravn → Personas shows what a resident can be given to steward.',
      'Start one against this host; it observes first and asks before acting.',
      'Talk to it in its room; every action it proposes waits for your answer.',
    ],
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
          {WALKTHROUGHS.map((item) => (
            <a key={item.href} className="setup-link" href={item.href}>
              <span className="setup-link__title">{item.title}</span>
              <span className="setup-link__desc">{item.desc}</span>
              <ol className="setup-link__steps">
                {item.steps.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ol>
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
