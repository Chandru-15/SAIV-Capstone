import { parseUtcTimestamp } from './student-policy';

/** Display only. All eligibility/freshness calculations remain in their existing modules. */
export function formatJoinedDate(value?: string): string {
  const stamp = parseUtcTimestamp(value);
  return stamp === null ? 'Date unavailable' : new Date(stamp).toLocaleDateString('en-SG', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Singapore',
  });
}

export function formatSessionRange(start: string, end: string): string {
  const from = parseUtcTimestamp(start), to = parseUtcTimestamp(end);
  if (from === null || to === null) return 'Time unavailable';
  const date = (stamp: number) => new Date(stamp).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Singapore' });
  const time = (stamp: number) => new Date(stamp).toLocaleTimeString('en-SG', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Singapore' });
  return date(from) === date(to) ? `${date(from)} · ${time(from)} – ${time(to)}` : `${date(from)}, ${time(from)} – ${date(to)}, ${time(to)}`;
}
