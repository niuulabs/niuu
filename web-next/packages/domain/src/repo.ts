import { z } from 'zod';

export const repoRecordSchema = z.object({
  provider: z.string().min(1),
  org: z.string().min(1),
  name: z.string().min(1),
  cloneUrl: z.string().min(1),
  url: z.string().min(1).optional(),
  defaultBranch: z.string().min(1),
  branches: z.array(z.string()),
  /** The Git account (a connection's credential name) that listed this repository. */
  account: z.string().min(1).optional(),
});

export type RepoRecord = z.infer<typeof repoRecordSchema>;

export const sharedRepoPayloadSchema = z.object({
  provider: z.string().min(1),
  org: z.string().min(1),
  name: z.string().min(1),
  url: z.string().min(1),
  clone_url: z.string().min(1).optional(),
  default_branch: z.string().min(1).optional(),
  branches: z.array(z.string()).optional(),
});

export type SharedRepoPayload = z.infer<typeof sharedRepoPayloadSchema>;

export const sharedRepoCatalogResponseSchema = z.record(
  z.string().min(1),
  z.array(sharedRepoPayloadSchema),
);

export type SharedRepoCatalogResponse = z.infer<typeof sharedRepoCatalogResponseSchema>;

function normalizeRepo(payload: SharedRepoPayload): RepoRecord {
  return {
    provider: payload.provider,
    org: payload.org,
    name: payload.name,
    cloneUrl: payload.clone_url ?? `${payload.url}.git`,
    url: payload.url,
    defaultBranch: payload.default_branch ?? 'main',
    branches: payload.branches ?? [],
  };
}

export function normalizeRepoCatalogResponse(
  payload: SharedRepoCatalogResponse | SharedRepoPayload[] | RepoRecord[],
): RepoRecord[] {
  if (Array.isArray(payload)) {
    return payload.map((repo) => ('cloneUrl' in repo ? repo : normalizeRepo(repo)));
  }

  // The platform groups repositories by the account that listed them; keeping
  // that lets a launch clone with the same account's credential.
  return Object.entries(payload).flatMap(([account, repos]) =>
    repos.map((repo) => ({ ...normalizeRepo(repo), account })),
  );
}
