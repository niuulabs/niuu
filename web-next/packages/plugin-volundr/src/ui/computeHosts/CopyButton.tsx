import { useState } from 'react';
import { errorText } from '../errorText';

/**
 * Copies `text` to the clipboard and says so. A clipboard that refuses (no permission, an
 * insecure page) is shown as the error it is, never as a copy that did not happen.
 */
export function CopyButton({
  text,
  label = 'Copy',
  primary = false,
}: {
  text: string;
  label?: string;
  primary?: boolean;
}) {
  const [state, setState] = useState<{ copied: boolean; error: string | null }>({
    copied: false,
    error: null,
  });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState({ copied: true, error: null });
    } catch (error) {
      setState({ copied: false, error: errorText(error, 'The clipboard is not available') });
    }
  };

  return (
    <span className="niuu:inline-flex niuu:items-center niuu:gap-2">
      <button
        type="button"
        onClick={() => void copy()}
        className={
          primary
            ? 'niuu:rounded-md niuu:bg-brand niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-semibold niuu:text-bg-primary'
            : 'niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary'
        }
      >
        {state.copied ? 'Copied' : label}
      </button>
      {state.error ? (
        <span role="alert" className="niuu:text-xs niuu:text-critical-fg">
          Could not copy: {state.error}
        </span>
      ) : null}
    </span>
  );
}
