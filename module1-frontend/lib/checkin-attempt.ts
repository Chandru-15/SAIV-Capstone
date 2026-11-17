import { getStoredUser } from './auth';

// Only an uncertainty marker is persisted, never a QR secret, location or image.
// Retain it across modal closure/reload so an interrupted POST is not replayed.
const memory = new Set<string>();
function markerKey(sessionId: string): string {
  return `saiv_pending_checkin:${getStoredUser()?.id ?? 'current'}:${sessionId}`;
}
export function pendingCheckin(sessionId: string): boolean {
  const key = markerKey(sessionId);
  try { return sessionStorage.getItem(key) === 'pending' || memory.has(key); }
  catch { return memory.has(key); }
}
export function markCheckin(sessionId: string, pending: boolean): void {
  const key = markerKey(sessionId);
  if (pending) memory.add(key); else memory.delete(key);
  try {
    if (pending) sessionStorage.setItem(key, 'pending');
    else sessionStorage.removeItem(key);
  } catch { /* Keep the in-memory marker if browser storage is unavailable. */ }
}
