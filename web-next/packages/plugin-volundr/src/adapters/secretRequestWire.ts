/**
 * Wire shape of a Forge secret request, in one place.
 *
 * Source of truth: Forge `/api/v1/forge/sessions/{session_id}/secret-requests`. The response
 * never carries the secret value; this module has no field for one.
 */
import {
  SECRET_KEEP_CHOICES,
  SECRET_REQUEST_STATUSES,
  type SecretKeep,
  type SecretRequest,
  type SecretRequestStatus,
} from '@niuulabs/domain';

export interface SecretRequestPayload {
  id: string;
  session_id: string;
  name: string;
  purpose: string;
  target?: string | null;
  status: string;
  keep?: string | null;
  credential_name?: string | null;
}

function status(value: string): SecretRequestStatus {
  const known = SECRET_REQUEST_STATUSES.find((candidate) => candidate === value);
  if (!known) throw new Error(`Forge returned an unknown secret request status: ${value}`);
  return known;
}

function keep(value: string | null | undefined): SecretKeep | null {
  if (value === null || value === undefined) return null;
  const known = SECRET_KEEP_CHOICES.find((candidate) => candidate === value);
  if (!known) throw new Error(`Forge returned an unknown secret keep choice: ${value}`);
  return known;
}

export function normalizeSecretRequest(payload: SecretRequestPayload): SecretRequest {
  return {
    id: payload.id,
    sessionId: payload.session_id,
    name: payload.name,
    purpose: payload.purpose,
    target: payload.target ?? null,
    status: status(payload.status),
    keep: keep(payload.keep),
    credentialName: payload.credential_name ?? null,
  };
}
