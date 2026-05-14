export function humanizeObservatoryText(value: string): string {
  return value
    .replace(/Ting/g, 'Ting')
    .replace(/TING/g, 'TING')
    .replace(/Ting/g, 'Ting')
    .replace(/ting/g, 'ting')
    .replace(/Runs/g, 'Runs')
    .replace(/runs/g, 'runs')
    .replace(/Run/g, 'Run')
    .replace(/run/g, 'run');
}

export function humanizeObservatoryEventType(value: string): string {
  if (value === 'TING') return 'TING';
  if (value === 'RUN') return 'RUN';
  return value;
}
