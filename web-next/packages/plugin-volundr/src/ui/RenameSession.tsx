import { useId, useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { Popover, PopoverContent, PopoverTrigger } from '@niuulabs/ui';
import { Pencil } from 'lucide-react';
import type { IVolundrService } from '../ports/IVolundrService';
import type { Session } from '../domain/session';
import { validateSessionName } from './launchWizardModel';
import './RenameSession.css';

export function RenameSession({
  sessionId,
  name,
  disabled = false,
}: {
  sessionId: string;
  name: string;
  disabled?: boolean;
}) {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const trimmed = draft.trim();
  const validation = trimmed ? validateSessionName(trimmed) : 'Enter a session name';
  const changed = trimmed !== name;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (saving || disabled || validation || !changed) return;
    setSaving(true);
    setError('');
    try {
      const updated = await volundr.updateSession(sessionId, { name: trimmed });
      const keys = [
        ['volundr', 'raw-session', sessionId],
        ['volundr', 'domain-session', sessionId],
        ['volundr', 'domain-sessions'],
      ] as const;
      await Promise.all(keys.map((queryKey) => queryClient.cancelQueries({ queryKey })));
      queryClient.setQueryData(keys[0], updated);
      queryClient.setQueryData<Session>(keys[1], (session) =>
        session ? { ...session, name: updated.name } : session,
      );
      queryClient.setQueriesData<Session[]>({ queryKey: keys[2] }, (sessions) =>
        sessions?.map((session) =>
          session.id === sessionId ? { ...session, name: updated.name } : session,
        ),
      );
      setOpen(false);
      await Promise.all(
        [...keys, ['volundr', 'history'], ['volundr', 'session-list']].map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not rename session. Try again.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (saving) return;
        if (next) {
          setDraft(name);
          setError('');
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className="forge-session-rename"
          title="Rename session"
          aria-label={`Rename ${name}`}
          disabled={disabled || saving}
        >
          <Pencil size={15} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="forge-session-rename-popover">
        <form onSubmit={(event) => void save(event)} aria-label="Rename session">
          <label htmlFor={inputId}>Session name</label>
          <input
            id={inputId}
            value={draft}
            disabled={saving}
            onFocus={(event) => event.target.select()}
            onChange={(event) => {
              setDraft(event.target.value);
              setError('');
            }}
            aria-describedby={`${inputId}-hint`}
            aria-invalid={changed && Boolean(validation)}
            autoComplete="off"
            spellCheck={false}
          />
          <p id={`${inputId}-hint`}>Lowercase letters, numbers and hyphens. Up to 63 characters.</p>
          {(error || (changed && validation)) && <p role="alert">{error || validation}</p>}
          <footer>
            <button type="button" disabled={saving} onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="submit" disabled={saving || !changed || Boolean(validation)}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </footer>
        </form>
      </PopoverContent>
    </Popover>
  );
}
