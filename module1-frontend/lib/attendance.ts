import type { CheckinHistoryItem } from '../types/api';

export function mergeCheckins(existing: CheckinHistoryItem[], incoming: CheckinHistoryItem[]): CheckinHistoryItem[] {
  const records = new Map(existing.map((item) => [item.id, item]));
  incoming.forEach((item) => records.set(item.id, item));
  return Array.from(records.values());
}
