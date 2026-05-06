import type { PodSpec } from './pod';

/** Reusable pod template stored in Völundr. Templates can be versioned and cloned. */
export interface Template {
  id: string;
  name: string;
  /** Short human-readable description of this template. */
  description?: string;
  version: number;
  spec: PodSpec;
  createdAt: string;
  updatedAt: string;
  /** Number of times this template has been used to launch a session. */
  usageCount?: number;
}
