import { hostChips, hostFlavor, type HostFacts } from '../domain/setup';
import { ArrowIcon, NiuuMark } from './icons';

export interface WelcomeStepProps {
  facts: HostFacts | null;
  loading: boolean;
  onBegin: () => void;
}

const PLAN = [
  { title: 'Check the system', desc: 'Docker, GPU, disk, database.' },
  { title: 'Connect AI providers', desc: 'Claude, OpenAI and any other API key.' },
  { title: 'Connect your tools', desc: 'Git hosting and your issue tracker.' },
  { title: 'Finish', desc: 'Then run your first session.' },
];

export function WelcomeStep({ facts, loading, onBegin }: WelcomeStepProps) {
  const chips = hostChips(facts);
  return (
    <div className="setup-hero" data-testid="setup-welcome">
      <div className="setup-hero__kicker">níu · first launch</div>
      <div className="setup-hero__mark">
        <NiuuMark size={72} />
      </div>
      <h1 className="setup-hero__title">Let&apos;s set up Niuu on {hostFlavor(facts)}.</h1>
      <p className="setup-lede">
        A few minutes. Everything you enter stays on this machine, encrypted, and can be changed
        later in Settings.
      </p>
      {loading ? (
        <div className="setup-note" data-testid="setup-welcome-loading">
          Reading host facts…
        </div>
      ) : chips.length > 0 ? (
        <div className="setup-chips" data-testid="setup-host-chips">
          {chips.map((chip) => (
            <span
              key={chip.label}
              className={`setup-chip ${chip.tone === 'neutral' ? '' : `setup-chip--${chip.tone}`}`}
            >
              {chip.label}
            </span>
          ))}
        </div>
      ) : null}
      <div className="setup-grid">
        {PLAN.map((item, index) => (
          <div key={item.title} className="setup-tile">
            <span className="setup-tile__index">0{index + 1}</span>
            <span className="setup-tile__title">{item.title}</span>
            <span className="setup-tile__desc">{item.desc}</span>
          </div>
        ))}
      </div>
      <button
        type="button"
        className="setup-btn setup-btn--primary"
        onClick={onBegin}
        data-testid="setup-begin"
      >
        Begin setup <ArrowIcon />
      </button>
    </div>
  );
}
