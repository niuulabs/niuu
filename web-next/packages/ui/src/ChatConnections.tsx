import { createContext, useContext } from 'react';

/** The host supplies account recovery without coupling chat to a provider service. */
export const ChatConnectionsContext = createContext<(() => void) | undefined>(undefined);

export function ChatConnectionsButton() {
  const open = useContext(ChatConnectionsContext);
  if (!open) return null;
  return (
    <button
      type="button"
      onClick={open}
      className="niuu:rounded-md niuu:border niuu:border-border niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-secondary"
    >
      Reconnect account
    </button>
  );
}
