import { z } from 'zod';

/**
 * Roles a Mímir mount may serve in a deployment.
 *
 * - `local`  — operator's own private mount.
 * - `shared` — realm-wide shared knowledge base.
 * - `domain` — prefix-scoped domain knowledge.
 *
 * Owner: **Mimir** (`plugin-mimir`).
 * Consumed by: **Ravn** (binding UI, write-routing chips).
 */
export const mountRoleSchema = z.enum(['local', 'shared', 'domain']);

export type MountRole = z.infer<typeof mountRoleSchema>;

/**
 * Health status reported by a Mímir mount.
 *
 * Owner: **Mimir**. Consumed by: **Observatory** (topology health dots).
 */
export const mountStatusSchema = z.enum(['healthy', 'degraded', 'down']);

export type MountStatus = z.infer<typeof mountStatusSchema>;

/**
 * A Mímir mount — a standalone knowledge-base instance with its own
 * storage, embedding model, write-routing priority, and health signal.
 *
 * **Canonical owner:** `plugin-mimir` (manages all mounts; renders the
 * Overview, Routing, and Ravns screens).
 *
 * **Consumed by:**
 * - `plugin-ravn` — persona form shows `mimirWriteRouting` dropdown
 *   derived from available mounts; MountChip renders mount name + role.
 * - `plugin-observatory` — topology canvas shows Mímir node + mount count.
 * - `plugin-tyr` — dispatch feasibility check reads mount health.
 */
export const mountSchema = z.object({
  /** Unique short name used in write-routing rules and RavnBindings. */
  name: z.string().min(1),
  /** Role this mount plays in the deployment. */
  role: mountRoleSchema,
  /** Hostname or IP of the host running this mount. */
  host: z.string().min(1),
  /** Base URL of the mount's HTTP API. */
  url: z.string().min(1),
  /**
   * Write-routing priority. Lower values win on prefix matches.
   * Must be a non-negative integer.
   */
  priority: z.number().int().min(0),
  /**
   * Category allowlist. `null` means the mount accepts all categories.
   * A non-empty array restricts ingest to the listed categories.
   */
  categories: z.array(z.string()).nullable(),
  /** Health status reported by the mount's health endpoint. */
  status: mountStatusSchema,
  /** Total compiled pages stored on this mount. */
  pages: z.number().int().min(0),
  /** Total raw source records stored on this mount. */
  sources: z.number().int().min(0),
  /** Open lint issues on this mount. */
  lintIssues: z.number().int().min(0),
  /** ISO-8601 timestamp of the most recent write to this mount. */
  lastWrite: z.string().min(1),
  /** Embedding model id used by this mount's vector index. */
  embedding: z.string().min(1),
  /** Total size of compiled pages on disk in kilobytes. */
  sizeKb: z.number().min(0),
  /** Human-readable description shown in the Overview card. */
  desc: z.string(),
});

export type Mount = z.infer<typeof mountSchema>;
