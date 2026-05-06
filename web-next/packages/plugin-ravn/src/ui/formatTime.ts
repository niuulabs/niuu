const TIME_HOUR_START = 11;
const TIME_HOUR_END = 19;

export function formatTime(iso: string): string {
  return iso.slice(TIME_HOUR_START, TIME_HOUR_END);
}
