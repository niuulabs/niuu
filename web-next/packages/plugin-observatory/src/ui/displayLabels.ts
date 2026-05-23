export function humanizeObservatoryText(value: unknown): string {
  const text =
    typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value);
  return text
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

export function humanizeObservatoryEventType(value: unknown): string {
  const text = typeof value === 'string' ? value : '';
  if (text === 'TYR') return 'TING';
  if (text === 'RAID') return 'RUN';
  if (text === 'TING') return 'TING';
  if (text === 'RUN') return 'RUN';
  return text;
}
