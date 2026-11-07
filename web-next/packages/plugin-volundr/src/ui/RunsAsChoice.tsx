import {
  GRANT_LABELS,
  RUNTIME_LABELS,
  readinessLine,
  type SessionGrant,
  type SessionRuntimeOffer,
} from '../domain/sessionRuntimes';
import './RunsAsChoice.css';

/**
 * "Runs as": the runtimes the host offers, as cards, and the extra access the chosen one
 * can give. Renders nothing for a host that offers no choice.
 */
export function RunsAsChoice({
  offers,
  offer,
  grants,
  onSelect,
  onGrantsChange,
}: {
  offers: readonly SessionRuntimeOffer[];
  offer: SessionRuntimeOffer | undefined;
  grants: readonly SessionGrant[];
  onSelect: (offer: SessionRuntimeOffer) => void;
  onGrantsChange: (grants: SessionGrant[]) => void;
}) {
  if (!offers.length || !offer) return null;
  return (
    <div className="vol-runs-as" data-testid="runs-as">
      <span className="vol-runs-as__title" id="runs-as-title">
        Runs as
      </span>
      <div className="vol-runs-as__cards" role="group" aria-labelledby="runs-as-title">
        {offers.map((candidate) => {
          const label = RUNTIME_LABELS[candidate.runtime];
          return (
            <button
              key={candidate.executionProfile}
              type="button"
              className="vol-runs-as__card"
              aria-pressed={candidate.executionProfile === offer.executionProfile}
              onClick={() => onSelect(candidate)}
            >
              <strong>{label.name}</strong>
              <span>{label.summary}</span>
            </button>
          );
        })}
      </div>
      {offer.grants.length > 0 && (
        <fieldset className="vol-runs-as__grants">
          <legend>{RUNTIME_LABELS[offer.runtime].name}: extra access</legend>
          <div className="vol-runs-as__grant-row">
            {offer.grants.map((grant) => (
              <label key={grant} className="vol-runs-as__grant">
                <input
                  type="checkbox"
                  checked={grants.includes(grant)}
                  onChange={(e) =>
                    onGrantsChange(
                      e.target.checked
                        ? offer.grants.filter((g) => g === grant || grants.includes(g))
                        : grants.filter((g) => g !== grant),
                    )
                  }
                />
                {GRANT_LABELS[grant]}
              </label>
            ))}
          </div>
          <small className="vol-runs-as__warning">
            Either one gives this session a VM to itself.
          </small>
        </fieldset>
      )}
    </div>
  );
}

/** How soon a session with the chosen runtime starts, for the launch button's side. */
export function RunsAsReadiness({ offer }: { offer: SessionRuntimeOffer | undefined }) {
  if (!offer) return null;
  return (
    <span
      className={`vol-runs-as__readiness vol-runs-as__readiness--${offer.readiness.state}`}
      data-testid="runs-as-readiness"
      role="status"
    >
      {readinessLine(offer.readiness)}
    </span>
  );
}
