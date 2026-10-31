import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { emptyRuleDraft, type NotificationSinkOption } from '../../domain/notifications';
import { RuleEditor, validateRuleDraft } from './RuleEditor';

const sinks: NotificationSinkOption[] = [
  { name: 'telegram', label: 'Telegram', requiresIntegration: true },
  { name: 'push', label: 'Push', requiresIntegration: false },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe('validateRuleDraft', () => {
  it('accepts a complete draft and explains every missing piece', () => {
    expect(validateRuleDraft({ ...emptyRuleDraft('push'), name: 'All' }, sinks)).toEqual({});
    expect(validateRuleDraft(emptyRuleDraft('nowhere'), sinks)).toEqual({
      name: 'Give the rule a name.',
      sink: 'Choose where to deliver.',
    });
    expect(
      validateRuleDraft(
        {
          ...emptyRuleDraft('telegram'),
          name: 'x',
          quietHours: {
            start: '25:00',
            end: '07:00',
            timezone: 'UTC',
            allowMinSeverity: 'critical',
          },
        },
        sinks,
      ),
    ).toEqual({
      connection: 'Telegram needs a messaging connection.',
      quietHours: 'Quiet hours use 24-hour HH:MM times.',
    });
  });
});

describe('RuleEditor', () => {
  it('explains a missing messaging connection and shows saving progress', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    const { rerender } = render(
      <RuleEditor
        initial={{ ...emptyRuleDraft('telegram'), name: 'Night' }}
        sinks={sinks}
        connections={[]}
        saving={false}
        error={null}
        isNew
        onSave={onSave}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByText('No messaging connections yet — add one under Settings → Integrations.'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create rule' }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Messaging connection')).toHaveAttribute('aria-invalid', 'true');

    // Switching to a sink without an integration drops the stale connection on save.
    await user.selectOptions(screen.getByLabelText('Deliver to'), 'push');
    await user.click(screen.getByRole('button', { name: 'Create rule' }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ sink: 'push', integrationConnectionId: null }),
    );

    rerender(
      <RuleEditor
        initial={{ ...emptyRuleDraft('push'), name: 'Night' }}
        sinks={sinks}
        connections={[]}
        saving
        error="Could not save the rule: nope"
        isNew={false}
        onSave={onSave}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('nope');
  });

  it('falls back to UTC when the browser cannot name its timezone', async () => {
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => {
      throw new Error('no Intl');
    });
    const user = userEvent.setup();
    render(
      <RuleEditor
        initial={emptyRuleDraft('push')}
        sinks={sinks}
        connections={[]}
        saving={false}
        error={null}
        isNew
        onSave={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('checkbox', { name: 'Hold messages during quiet hours' }));
    expect(screen.getByLabelText('Timezone')).toHaveValue('UTC');
  });
});
