import { useAuth } from '@niuulabs/auth';
import { Tooltip } from '@niuulabs/ui';
import { LogIn, Power, UserRound } from 'lucide-react';
import './AccountControls.css';

export function AccountControls({ onDisconnect }: { onDisconnect: () => void }) {
  const { enabled, authenticated, loading, user, login } = useAuth();
  const name =
    user?.profile.name || user?.profile.preferred_username || user?.profile.email || 'Account';
  const signedOut = enabled && !authenticated;
  return (
    <div className="niuu-account-controls" aria-label="Account">
      {enabled && authenticated && (
        <span className="niuu-account-badge" title={name}>
          <UserRound size={18} />
          <span>{name}</span>
        </span>
      )}
      <Tooltip content={signedOut ? 'Sign in' : 'Disconnect'} side="bottom" delayMs={100}>
        <button
          type="button"
          disabled={loading && enabled}
          onClick={signedOut ? login : onDisconnect}
          aria-label={signedOut ? 'Sign in' : 'Disconnect'}
        >
          {signedOut ? (
            <LogIn size={20} aria-hidden="true" />
          ) : (
            <Power size={20} aria-hidden="true" />
          )}
          {signedOut && <span>Sign in</span>}
        </button>
      </Tooltip>
    </div>
  );
}
