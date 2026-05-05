/**
 * Budget attention classification.
 *
 * Classifies a ravn's spend state into one of three attention buckets used
 * by the Budget view's attention columns.
 */

import type { BudgetState } from '@niuulabs/domain';

/** Ratio at which a ravn is classified as "burning fast" (critical). */
const BURNING_THRESHOLD = 0.9;

/** Ratio below which a ravn is classified as "idle". */
const IDLE_THRESHOLD = 0.1;

/** Full budget window in hours (used for runway projection). */
const BUDGET_WINDOW_HOURS = 24;

export type BudgetAttention = 'over-cap' | 'burning-fast' | 'near-cap' | 'idle' | 'normal';
export type BurnTrend = 'accelerating' | 'steady' | 'decelerating';

/**
 * Classify a single ravn's budget into an attention bucket.
 *
 * - `over-cap`     — spending ratio > 100 % (exceeded cap)
 * - `burning-fast` — spending ratio ≥ 90 % (critical)
 * - `near-cap`     — spending ratio ≥ warnAt (warn threshold) but < 90 %
 * - `idle`         — spending ratio < 10 %
 * - `normal`       — everything else
 */
export function classifyBudget(budget: BudgetState): BudgetAttention {
  if (budget.capUsd === 0) return 'idle';
  const ratio = budget.spentUsd / budget.capUsd;
  if (ratio > 1) return 'over-cap';
  if (ratio >= BURNING_THRESHOLD) return 'burning-fast';
  if (ratio >= budget.warnAt) return 'near-cap';
  if (ratio < IDLE_THRESHOLD) return 'idle';
  return 'normal';
}

/**
 * Returns the runway: how many USD remain before hitting the cap.
 */
export function budgetRunway(budget: BudgetState): number {
  return Math.max(0, budget.capUsd - budget.spentUsd);
}

/**
 * Returns spending ratio (0–1 clamped), or 0 when cap is unlimited.
 */
export function budgetRatio(budget: BudgetState): number {
  if (budget.capUsd === 0) return 0;
  return Math.min(1, budget.spentUsd / budget.capUsd);
}

/**
 * Returns the burn rate in $/hour given elapsed hours.
 * Returns 0 if elapsedHours is 0 or negative.
 */
export function burnRate(budget: BudgetState, elapsedHours: number): number {
  if (elapsedHours <= 0) return 0;
  return budget.spentUsd / elapsedHours;
}

/**
 * Returns the projected hours until the cap is breached at the given rate.
 * Returns Infinity if rate is 0 (will never breach).
 * Returns 0 if already over cap.
 */
export function projectedDepletion(budget: BudgetState, rate: number): number {
  if (budget.spentUsd >= budget.capUsd) return 0;
  if (rate <= 0) return Infinity;
  return (budget.capUsd - budget.spentUsd) / rate;
}

/**
 * Compares current and previous burn rates and returns a trend label.
 * A change of more than 10% is considered accelerating or decelerating.
 */
export function burnTrend(currentRate: number, previousRate: number): BurnTrend {
  if (previousRate <= 0) return 'steady';
  const change = (currentRate - previousRate) / previousRate;
  if (change > 0.1) return 'accelerating';
  if (change < -0.1) return 'decelerating';
  return 'steady';
}

/**
 * Returns runway as a fraction of the full budget window (0–1).
 * Used to render the time-based runway bar.
 */
export function runwayFraction(
  budget: BudgetState,
  elapsedHours = BUDGET_WINDOW_HOURS / 2,
): number {
  const rate = burnRate(budget, elapsedHours);
  const hoursLeft = projectedDepletion(budget, rate);
  if (hoursLeft === Infinity) return 1;
  return Math.min(1, Math.max(0, hoursLeft / BUDGET_WINDOW_HOURS));
}
