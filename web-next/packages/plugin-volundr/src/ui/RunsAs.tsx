import {
  GRANT_LABELS,
  RUNTIME_LABELS,
  readinessLine,
  type SessionGrant,
  type SessionRuntimeOffer,
} from '../domain/sessionRuntimes';
import './RunsAs.css';

/**
 * "Runs as": what the host runs every session as, read-only, and the extra access its
 * runtime can give. The runtime is the server's, so nothing here picks it. Renders
 * nothing for a host that declares none.
 */
export function RunsAs({
  offer,
  grants,
  onGrantsChange,
}: {
  offer: SessionRuntimeOffer | undefined;
  grants: readonly SessionGrant[];
  onGrantsChange: (grants: SessionGrant[]) => void;
}) {
  if (!offer) return null;
  const label = RUNTIME_LABELS[offer.runtime];
  return (
    <div className="vol-runs-as" data-testid="runs-as">
      <p className="vol-runs-as__line" title={label.summary}>
        <span className="vol-runs-as__title">Runs as:</span> <strong>{label.name}</strong>
      </p>
      {offer.grants.length > 0 && (
        <fieldset className="vol-runs-as__grants">
          <legend>{label.name}: extra access</legend>
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

/** How soon a session on this host starts, for the launch button's side. */
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
