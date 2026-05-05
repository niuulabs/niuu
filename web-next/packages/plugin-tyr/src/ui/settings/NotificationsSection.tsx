import { useState, type FormEvent } from 'react';
import {
  Field,
  Input,
  Select,
  ValidationSummary,
  StateDot,
  type ValidationError,
} from '@niuulabs/ui';
import type { NotificationChannel, NotificationSettings } from '../../ports';
import { useNotificationSettings, useUpdateNotificationSettings } from './useSettings';

const CHANNEL_OPTIONS: { value: NotificationChannel; label: string }[] = [
  { value: 'none', label: 'None (disabled)' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'email', label: 'Email' },
  { value: 'webhook', label: 'Webhook' },
];

function validate(channel: NotificationChannel, webhookUrl: string): ValidationError[] {
  const errors: ValidationError[] = [];

  if (channel === 'webhook' && !webhookUrl.trim()) {
    errors.push({
      id: 'notif-webhook-url',
      label: 'Webhook URL',
      message: 'Required when channel is Webhook',
    });
  }

  return errors;
}

interface ToggleRowProps {
  id: string;
  name: string;
  label: string;
  defaultChecked?: boolean;
}

function ToggleRow({ id, name, label, defaultChecked }: ToggleRowProps) {
  return (
    <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-py-2 niuu-border-b niuu-border-border-subtle">
      <input
        type="checkbox"
        id={id}
        name={name}
        value="true"
        defaultChecked={defaultChecked}
        className="niuu-accent-brand niuu-shrink-0"
      />
      <label
        htmlFor={id}
        className="niuu-text-sm niuu-text-text-primary niuu-select-none niuu-flex-1"
      >
        {label}
      </label>
    </div>
  );
}

export function NotificationsSection() {
  const { data: settings, isLoading, isError, error } = useNotificationSettings();
  const { mutateAsync: update, isPending: isSaving } = useUpdateNotificationSettings();
  const [channelOverride, setChannelOverride] = useState<NotificationChannel | null>(null);
  const effectiveChannel = channelOverride ?? settings?.channel ?? 'telegram';
  const [errors, setErrors] = useState<ValidationError[]>([]);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);

    const selectedChannel = effectiveChannel;
    const webhookUrl = String(data.get('webhookUrl') ?? '');

    const validationErrors = validate(selectedChannel, webhookUrl);
    setErrors(validationErrors);
    if (validationErrors.length > 0) return;

    const patch: Partial<Omit<NotificationSettings, 'updatedAt'>> = {
      channel: selectedChannel,
      onRaidPendingApproval: data.get('onRaidPendingApproval') === 'true',
      onRaidMerged: data.get('onRaidMerged') === 'true',
      onRaidFailed: data.get('onRaidFailed') === 'true',
      onSagaComplete: data.get('onSagaComplete') === 'true',
      onDispatcherError: data.get('onDispatcherError') === 'true',
      webhookUrl: selectedChannel === 'webhook' ? webhookUrl : null,
    };

    await update(patch);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  if (isLoading) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2" role="status">
        <StateDot state="processing" pulse />
        <span className="niuu-text-sm niuu-text-text-secondary">
          loading notification settings…
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2" role="alert">
        <StateDot state="failed" />
        <span className="niuu-text-sm niuu-text-critical">
          {error instanceof Error ? error.message : 'failed to load'}
        </span>
      </div>
    );
  }

  return (
    <section aria-label="Notification settings">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        Notifications
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        Configure how and when Tyr sends notifications for raid and dispatcher events.
      </p>

      <form
        onSubmit={(e) => void handleSubmit(e)}
        noValidate
        className="niuu-flex niuu-flex-col niuu-gap-4 niuu-max-w-lg"
        aria-label="Notification settings form"
      >
        {errors.length > 0 && <ValidationSummary errors={errors} />}

        <Field id="notif-channel" label="Notification channel">
          <Select
            options={CHANNEL_OPTIONS}
            defaultValue={effectiveChannel}
            onValueChange={(val) => setChannelOverride(val as NotificationChannel)}
            name="channel"
          />
        </Field>

        {effectiveChannel === 'webhook' && (
          <Field id="notif-webhook-url" label="Webhook URL" required>
            <Input
              name="webhookUrl"
              type="url"
              defaultValue={settings?.webhookUrl ?? ''}
              placeholder="https://example.com/hooks/tyr"
            />
          </Field>
        )}

        <div className="niuu-border niuu-border-border niuu-rounded-md niuu-px-3 niuu-py-2">
          <h4 className="niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-mb-2">
            Event triggers
          </h4>

          <ToggleRow
            id="notif-pending-approval"
            name="onRaidPendingApproval"
            label="Raid awaiting approval"
            defaultChecked={settings?.onRaidPendingApproval}
          />
          <ToggleRow
            id="notif-raid-merged"
            name="onRaidMerged"
            label="Raid merged"
            defaultChecked={settings?.onRaidMerged}
          />
          <ToggleRow
            id="notif-raid-failed"
            name="onRaidFailed"
            label="Raid failed"
            defaultChecked={settings?.onRaidFailed}
          />
          <ToggleRow
            id="notif-saga-complete"
            name="onSagaComplete"
            label="Saga complete"
            defaultChecked={settings?.onSagaComplete}
          />
          <ToggleRow
            id="notif-dispatcher-error"
            name="onDispatcherError"
            label="Dispatcher error"
            defaultChecked={settings?.onDispatcherError}
          />
        </div>

        <div className="niuu-flex niuu-items-center niuu-gap-3">
          <button
            type="submit"
            disabled={isSaving}
            className="niuu-px-4 niuu-py-2 niuu-bg-brand niuu-text-white niuu-rounded-md niuu-text-sm niuu-font-medium niuu-transition-opacity disabled:niuu-opacity-50"
          >
            {isSaving ? 'Saving…' : 'Save'}
          </button>
          {saved && (
            <span className="niuu-text-sm niuu-text-accent-emerald" aria-live="polite">
              Saved
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
