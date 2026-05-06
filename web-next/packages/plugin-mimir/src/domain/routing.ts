/**
 * Mímir write-routing domain.
 *
 * Write-routing rules direct page writes to specific mounts using
 * prefix-based, first-match semantics. The rule with the lowest priority
 * number whose prefix is a prefix of the target path wins.
 */

export interface WriteRoutingRule {
  id: string;
  /** Path prefix — matches any page whose path starts with this string. */
  prefix: string;
  /** Name of the mount to route writes to. */
  mountName: string;
  /**
   * Routing priority — lower numbers win.
   * Rules with the same priority are evaluated in unspecified order.
   */
  priority: number;
  /** Whether this rule is currently enforced. */
  active: boolean;
  /** Human-readable description. */
  desc?: string;
}

export interface RouteTestResult {
  /** The path that was tested. */
  path: string;
  /** Winning rule, or null if no rule matched. */
  matchedRule: WriteRoutingRule | null;
  /** Target mount name, or null if no rule matched. */
  mountName: string | null;
  /** Human-readable explanation of the routing decision. */
  reason: string;
}

/**
 * Resolve a page path against a set of routing rules using first-match
 * prefix semantics.
 *
 * Rules are sorted by ascending priority; the first active rule whose prefix
 * is a prefix of the path wins. Returns null mountName when no rule matches.
 */
export function resolveRoute(rules: WriteRoutingRule[], path: string): RouteTestResult {
  const active = rules.filter((r) => r.active).sort((a, b) => a.priority - b.priority);

  for (const rule of active) {
    if (path.startsWith(rule.prefix)) {
      return {
        path,
        matchedRule: rule,
        mountName: rule.mountName,
        reason: `matched rule "${rule.prefix}" → ${rule.mountName} (priority ${rule.priority})`,
      };
    }
  }

  return {
    path,
    matchedRule: null,
    mountName: null,
    reason: 'no active rule matched this path',
  };
}
