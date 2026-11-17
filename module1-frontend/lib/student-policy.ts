import type { ActiveSession, CheckinHistoryItem, FacePolicy, StudentCourse } from '../types/api';

/** Whether a session's existing check-in still accepts another attempt. */
export type CheckinAttemptState = 'settled' | 'retryable';

/** Backend timestamps without an offset represent UTC, not the browser's local time. */
export function parseUtcTimestamp(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$/i.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second, fraction, zone] = match;
  const calendar = new Date(Date.UTC(+year, +month - 1, +day));
  if (calendar.getUTCFullYear() !== +year || calendar.getUTCMonth() !== +month - 1 ||
      calendar.getUTCDate() !== +day || +hour > 23 || +minute > 59 || +second > 59) return null;
  const stamp = Date.parse(`${year}-${month}-${day}T${hour}:${minute}:${second}${fraction ? `.${fraction.slice(0, 3)}` : ''}${zone ?? 'Z'}`);
  return Number.isFinite(stamp) ? stamp : null;
}

export function formatDateTime(value: string): string {
  const stamp = parseUtcTimestamp(value);
  return stamp === null ? 'Time unavailable' : new Date(stamp).toLocaleString('en-SG', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Singapore',
  });
}

export function deriveFacePolicy(session: ActiveSession, course?: StudentCourse): FacePolicy | null {
  if (!course || course.id !== session.course_id || typeof course.require_face_recognition !== 'boolean' ||
      typeof session.require_face_match !== 'boolean' || typeof session.require_liveness_check !== 'boolean') return null;
  const faceMatchRequired = session.require_face_match || course.require_face_recognition;
  return { faceMatchRequired, imageRequired: faceMatchRequired || session.require_liveness_check };
}

export function checkinWindowReason(session: ActiveSession, now = Date.now()): string | null {
  if (session.status !== 'active') return 'Session is not active';
  const opens = parseUtcTimestamp(session.checkin_opens_at);
  const closes = parseUtcTimestamp(session.checkin_closes_at);
  if (opens === null || closes === null || closes < opens || !Number.isFinite(now)) return 'Check-in time unavailable';
  if (now < opens) return 'Check-in has not opened';
  if (now > closes) return 'Check-in window closed';
  return null;
}

/**
 * Mirrors CheckInService._is_retryable on the backend.
 *
 * What settles a check-in is a person, which reviewed_by_id records — not the
 * 'rejected' status by itself. The risk engine rejects automatically on a
 * failed face match or a high score, and that ordinary failure is exactly what
 * a student should be able to try again.
 *
 * An approved check-in is finished, and an appeal has asked for human review,
 * so neither is retried. The backend is authoritative; this only decides what
 * to offer.
 */
export function checkinAttemptState(
  item: Pick<CheckinHistoryItem, 'status'> & { reviewed_by_id?: string | null }
): CheckinAttemptState {
  if (item.reviewed_by_id) return 'settled';
  return item.status === 'approved' || item.status === 'appealed' ? 'settled' : 'retryable';
}

export function sessionEligibility(
  session: ActiveSession, courses: StudentCourse[] | null,
  attempts: ReadonlyMap<string, CheckinAttemptState>, now = Date.now()
): { eligible: boolean; reason: string; policy: FacePolicy | null; retry: boolean } {
  const course = courses?.find((item) => item.id === session.course_id);
  const policy = deriveFacePolicy(session, course);
  const attempt = attempts.get(session.id);
  // Only a settled check-in closes the session off. A flagged or pending one
  // may be attempted again, subject to the backend's own cooldown.
  if (attempt === 'settled') return { eligible: false, reason: 'Already Checked In', policy, retry: false };
  const retry = attempt === 'retryable';
  if (!courses || !course) return { eligible: false, reason: 'Course enrollment unavailable', policy, retry };
  if (!policy) return { eligible: false, reason: 'Verification policy unavailable', policy, retry };
  // A retry is still bound by the check-in window.
  const reason = checkinWindowReason(session, now);
  if (reason) return { eligible: false, reason, policy, retry };
  return { eligible: true, reason: retry ? 'Try Check-in Again' : 'Check In', policy, retry };
}
