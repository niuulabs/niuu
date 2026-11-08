/**
 * Secure secret requests: an agent asks the person for a secret, the person types it into a
 * masked card, and the value goes straight to Forge. The value is never part of the transcript,
 * so nothing here carries one except the single `fulfill` input.
 */

/** The conversation tool part the Skuld broker appends for a request. */
export const SECRET_REQUEST_TOOL_NAME = 'secret_request';

export const SECRET_REQUEST_STATUSES = [
  'pending',
  'fulfilled',
  'delivered',
  'declined',
  'cancelled',
] as const;
export type SecretRequestStatus = (typeof SECRET_REQUEST_STATUSES)[number];

/** How long the value lives: only for this session, or in the person's credentials. */
export const SECRET_KEEP_CHOICES = ['session', 'saved'] as const;
export type SecretKeep = (typeof SECRET_KEEP_CHOICES)[number];

/** What the `secret_request` tool part carries: identity and wording, never a value. */
export interface SecretRequestInput {
  requestId: string;
  name: string;
  purpose: string;
  target: string | null;
}

/** A request as Forge reports it. */
export interface SecretRequest {
  id: string;
  sessionId: string;
  name: string;
  purpose: string;
  target: string | null;
  status: SecretRequestStatus;
  keep: SecretKeep | null;
  /** Set only for `keep: 'saved'`. */
  credentialName: string | null;
}

export interface SecretFulfillment {
  value: string;
  keep: SecretKeep;
}

function text(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

/** The request a `secret_request` tool part describes, or null when the part is unusable. */
export function parseSecretRequestInput(input: unknown): SecretRequestInput | null {
  if (typeof input !== 'object' || input === null) return null;
  const record = input as Record<string, unknown>;
  const requestId = text(record.request_id);
  const name = text(record.name);
  if (!requestId || !name) return null;
  return {
    requestId,
    name,
    purpose: text(record.purpose) ?? '',
    target: text(record.target),
  };
}

/** `GITLAB_TOKEN` becomes "Gitlab token", for the card title. */
export function secretTitle(name: string): string {
  const words = name.toLowerCase().split('_').filter(Boolean).join(' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}
