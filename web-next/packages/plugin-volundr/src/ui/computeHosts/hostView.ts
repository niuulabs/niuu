/**
 * How a host reads on screen: size, connection and the line under its name. Pure, with
 * the clock passed in so a row's wording is a function of what Forge reported.
 */
import type { ComputeHost, HostConnection, HostFacts } from '../../domain/computeHosts';
import { compactDuration } from '../sessions/timeFact';

const MIB_PER_GIB = 1_024;
const MIB_PER_TIB = MIB_PER_GIB * 1_024;
const TENTHS = 10;

function trimNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** "256 GiB" or "1.6 TiB". */
export function formatMib(mib: number): string {
  if (mib >= MIB_PER_TIB) {
    return `${trimNumber(Math.round((mib / MIB_PER_TIB) * TENTHS) / TENTHS)} TiB`;
  }
  return `${trimNumber(Math.round((mib / MIB_PER_GIB) * TENTHS) / TENTHS)} GiB`;
}

/** "64 CPU · 256 GiB · 1 GPU"; "—" before the first check has measured the host. */
export function formatSize(facts: HostFacts): string {
  const parts: string[] = [];
  if (facts.cpus !== null) parts.push(`${facts.cpus} CPU`);
  if (facts.memoryMib !== null) parts.push(formatMib(facts.memoryMib));
  if (facts.gpus !== null && facts.gpus > 0) parts.push(`${facts.gpus} GPU`);
  return parts.length ? parts.join(' · ') : '—';
}

const CONNECTION_LABELS: Record<string, string> = {
  direct_ssh: 'SSH',
  reverse_ssh: 'Outbound tunnel',
};

function connectionKindLabel(kind: string): string {
  return CONNECTION_LABELS[kind] ?? kind.replace(/_/g, ' ');
}

/** "SSH · 192.168.90.31" while connected; "Outbound tunnel · down 3m" once it drops. */
export function connectionLabel(connection: HostConnection, now: number): string {
  const kind = connectionKindLabel(connection.kind);
  if (connection.connected) {
    return connection.target ? `${kind} · ${connection.target.replace(/:22$/, '')}` : kind;
  }
  const since = connection.since ? Date.parse(connection.since) : Number.NaN;
  if (!Number.isFinite(since)) return `${kind} · down`;
  return `${kind} · down ${compactDuration(now - since)}`;
}

/** "Host · checked 2m ago", "Host · exclusive", or "joined 1m ago" for a host not yet checked. */
export function hostSubline(
  host: Pick<ComputeHost, 'runtime' | 'slots' | 'lastCheckedAt' | 'registeredAt'>,
  now: number,
): string {
  const runtime = host.runtime.charAt(0).toUpperCase() + host.runtime.slice(1);
  if (host.slots === 1) return `${runtime} · exclusive`;
  if (host.lastCheckedAt) {
    return `${runtime} · checked ${compactDuration(now - Date.parse(host.lastCheckedAt))} ago`;
  }
  return `joined ${compactDuration(now - Date.parse(host.registeredAt))} ago`;
}

/** The kernel line of the facts grid: "6.8.0 · cgroup v2". */
export function kernelLabel(facts: HostFacts): string {
  const parts: string[] = [];
  if (facts.kernel) parts.push(facts.kernel);
  if (facts.cgroupV2) parts.push('cgroup v2');
  return parts.length ? parts.join(' · ') : '—';
}
