export function humanizeObservatoryText(value: unknown): string {
  const text =
    typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value);
  return text
    .replace(/Týr/g, 'Work')
    .replace(/\bTyr\b/g, 'Work')
    .replace(/\bTYR\b/g, 'WORK')
    .replace(/\btyr\b/g, 'work')
    .replace(/ᛃ/g, '✦')
    .replace(/\bRaids\b/g, 'Runs')
    .replace(/\braids\b/g, 'runs')
    .replace(/\bRaid\b/g, 'Run')
    .replace(/\braid\b/g, 'run');
}

export function humanizeObservatoryEventType(value: unknown): string {
  const text = typeof value === 'string' ? value : '';
  if (text === 'TYR') return 'WORK';
  if (text === 'RAID') return 'RUN';
  if (text === 'TING') return 'WORK';
  if (text === 'RAVN') return 'AGENT';
  if (text === 'MIMIR') return 'MEMORY';
  if (text === 'BIFROST') return 'MODELS';
  if (text === 'RUN') return 'RUN';
  return text;
}
