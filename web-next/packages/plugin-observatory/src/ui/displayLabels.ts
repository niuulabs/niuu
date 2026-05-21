export function humanizeObservatoryText(value: string): string {
  return value
    .replace(/Týr/g, 'Ting')
    .replace(/\bTyr\b/g, 'Ting')
    .replace(/\bTYR\b/g, 'TING')
    .replace(/\btyr\b/g, 'ting')
    .replace(/ᛃ/g, '✦')
    .replace(/\bRaids\b/g, 'Runs')
    .replace(/\braids\b/g, 'runs')
    .replace(/\bRaid\b/g, 'Run')
    .replace(/\braid\b/g, 'run');
}

export function humanizeObservatoryEventType(value: string): string {
  if (value === 'TYR') return 'TING';
  if (value === 'RAID') return 'RUN';
  if (value === 'TING') return 'TING';
  if (value === 'RUN') return 'RUN';
  return value;
}
