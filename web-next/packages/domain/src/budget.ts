import { z } from 'zod';

/**
 * Daily USD budget state for a single raven.
 *
 * **Canonical owner:** `plugin-ravn` (BudgetBar, BudgetRunwayBar, and the
 * Budget view all live in Ravn and read from `BudgetStream`).
 *
 * **Consumed by:**
 * - `plugin-tyr` — dispatch feasibility check refuses to dispatch when
 *   `spentUsd >= capUsd`.
 * - `plugin-observatory` — topology canvas shows a budget health dot on
 *   raven nodes that exceed `warnAt`.
 */
export const budgetStateSchema = z.object({
  /** USD spent today (resets at midnight UTC). */
  spentUsd: z.number().min(0),
  /** Daily hard cap in USD. 0 means unlimited. */
  capUsd: z.number().min(0),
  /**
   * Fraction of `capUsd` at which the BudgetBar turns amber (0–1).
   * E.g. 0.8 means warn at 80 % usage.
   */
  warnAt: z.number().min(0).max(1),
});

export type BudgetState = z.infer<typeof budgetStateSchema>;
