import type { FaceReEnrollRequestRecord } from '../types/api';

// What the student can do about their face template right now.
//
//   'none'      nothing outstanding — they may ask
//   'pending'   asked, waiting on an instructor or admin
//   'approved'  granted and not yet spent — they may capture a replacement
//   'denied'    turned down; the note says why, and they may ask again
export type FaceReEnrollStage = 'none' | 'pending' | 'approved' | 'denied';

export interface FaceReEnrollState {
  stage: FaceReEnrollStage;
  record: FaceReEnrollRequestRecord | null;
}

const NOTHING: FaceReEnrollState = { stage: 'none', record: null };

/**
 * Reduce the student's request history to the one thing the UI must decide.
 *
 * Only the most recent request matters: the backend refuses a second one while
 * any is pending or any approval is unspent, so at most one can be live. The
 * list arrives newest first, but this sorts by `created_at` rather than
 * trusting that — a reordered response should not be able to resurrect a spent
 * approval as a usable one.
 *
 * A spent approval reads as 'none': `used_at` is set, the student has had
 * their replacement, and they are free to ask again. Mirrors
 * FaceService.reenroll, which looks for exactly `approved AND used_at IS NULL`.
 */
export function faceReenrollState(
  requests?: FaceReEnrollRequestRecord[] | null,
): FaceReEnrollState {
  if (!Array.isArray(requests) || requests.length === 0) return NOTHING;

  const dated = requests.filter(
    (entry): entry is FaceReEnrollRequestRecord =>
      !!entry && typeof entry.id === 'string' && typeof entry.status === 'string',
  );
  if (dated.length === 0) return NOTHING;

  const latest = dated.reduce((newest, entry) => {
    const a = Date.parse(entry.created_at ?? '');
    const b = Date.parse(newest.created_at ?? '');
    if (!Number.isFinite(a)) return newest;
    if (!Number.isFinite(b)) return entry;
    return a > b ? entry : newest;
  }, dated[0]);

  if (latest.status === 'pending') return { stage: 'pending', record: latest };
  if (latest.status === 'denied') return { stage: 'denied', record: latest };
  if (latest.status === 'approved' && !latest.used_at) {
    return { stage: 'approved', record: latest };
  }
  // Approved and already used: nothing outstanding.
  return NOTHING;
}

/** The button's label for each stage. */
export function faceReenrollLabel(stage: FaceReEnrollStage, enrolled: boolean): string {
  if (!enrolled) return 'Enroll Face';
  switch (stage) {
    case 'pending':
      return 'Awaiting approval';
    case 'approved':
      return 'Re-enroll face';
    case 'denied':
      return 'Request re-enrollment again';
    default:
      return 'Request re-enrollment';
  }
}
