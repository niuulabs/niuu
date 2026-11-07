import { describe, expect, it } from 'vitest';
import { seedComputeHosts } from '../../adapters/computeHosts.mock';
import type { ComputeHost, HostFacts } from '../../domain/computeHosts';
import { connectionLabel, formatMib, formatSize, hostSubline, kernelLabel } from './hostView';

const [hrimfaxi, , gloi, , , ljosi, glad] = seedComputeHosts() as [
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
];
const NOW = Date.parse('2026-11-06T12:00:00Z');

describe('hostView', () => {
  it('formats memory in GiB or TiB', () => {
    expect(formatMib(262_144)).toBe('256 GiB');
    expect(formatMib(1_638_400)).toBe('1.6 TiB');
    expect(formatMib(1_572_864)).toBe('1.5 TiB');
    expect(formatMib(1_024)).toBe('1 GiB');
  });

  it('sizes a host by CPU, memory and GPUs', () => {
    expect(formatSize(hrimfaxi.facts)).toBe('64 CPU · 256 GiB');
    expect(formatSize(gloi.facts)).toBe('24 CPU · 96 GiB · 1 GPU');
  });

  it('shows a dash before the host is measured', () => {
    const empty: HostFacts = {
      cpus: null,
      memoryMib: null,
      diskFreeMib: null,
      kernel: null,
      arch: null,
      cgroupV2: null,
      gpus: null,
      os: null,
    };
    expect(formatSize(empty)).toBe('—');
    expect(kernelLabel(empty)).toBe('—');
    expect(kernelLabel({ ...empty, kernel: '6.8.0', cgroupV2: true })).toBe('6.8.0 · cgroup v2');
    expect(kernelLabel({ ...empty, kernel: '6.8.0' })).toBe('6.8.0');
  });

  it('labels a connected host by connector and address, without the default port', () => {
    expect(connectionLabel(hrimfaxi.connection, NOW)).toBe('SSH · 192.168.90.31');
    expect(connectionLabel({ ...hrimfaxi.connection, target: '192.168.90.31:2222' }, NOW)).toBe(
      'SSH · 192.168.90.31:2222',
    );
    expect(connectionLabel({ ...hrimfaxi.connection, target: '' }, NOW)).toBe('SSH');
  });

  it('says how long a dropped connection has been down', () => {
    expect(connectionLabel(ljosi.connection, NOW)).toBe('Outbound tunnel · down 9m');
    expect(connectionLabel({ ...ljosi.connection, since: null }, NOW)).toBe(
      'Outbound tunnel · down',
    );
  });

  it('names an unknown connector kind in words', () => {
    expect(connectionLabel({ ...hrimfaxi.connection, kind: 'wire_guard' }, NOW)).toBe(
      'wire guard · 192.168.90.31',
    );
  });

  it('describes the host under its name', () => {
    expect(hostSubline(hrimfaxi, NOW)).toBe('Host · checked 2m ago');
    expect(hostSubline(gloi, NOW)).toBe('Host · exclusive');
    expect(hostSubline(glad, NOW)).toBe('joined 3m ago');
  });
});
