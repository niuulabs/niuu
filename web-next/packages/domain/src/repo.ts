import { z } from 'zod';

export const repoRecordSchema = z.object({
  provider: z.string().min(1),
  org: z.string().min(1),
  name: z.string().min(1),
  cloneUrl: z.string().min(1),
  url: z.string().min(1).optional(),
  defaultBranch: z.string().min(1),
  branches: z.array(z.string()),
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

  return Object.values(payload).flat().map(normalizeRepo);
}
