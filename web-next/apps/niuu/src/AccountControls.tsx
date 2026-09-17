import { useAuth } from '@niuulabs/auth';
import { LogIn, Power, UserRound } from 'lucide-react';
import './AccountControls.css';

export function AccountControls({ onDisconnect }: { onDisconnect: () => void }) {
  const { enabled, authenticated, loading, user, login } = useAuth();
  const name =
    user?.profile.name ||
    user?.profile.preferred_username ||
    user?.profile.email ||
    (enabled ? 'Account' : 'Private connection');
  const signedOut = enabled && !authenticated;
  return (
    <div className="niuu-account-controls" aria-label="Account">
      {!signedOut && (
        <span className="niuu-account-badge" title={name}>
          <UserRound size={18} />
          <span>{name}</span>
        </span>
      )}
      <button
        type="button"
        disabled={loading && enabled}
        onClick={signedOut ? login : onDisconnect}
        aria-label={signedOut ? 'Sign in' : 'Disconnect'}
        title={signedOut ? 'Sign in' : 'Disconnect'}
      >
        {signedOut ? <LogIn size={20} /> : <Power size={20} />}
        <span>{signedOut ? 'Sign in' : 'Disconnect'}</span>
      </button>
    </div>
  );
}
