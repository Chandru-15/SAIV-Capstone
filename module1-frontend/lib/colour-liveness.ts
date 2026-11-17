import { ClientDataError } from './api-error';
import type { ActiveLivenessChallenge } from '../types/api';

/**
 * Timing for the colour-reflection challenge.
 *
 * The screen is the light source here, so when a frame is taken matters as
 * much as what it contains. Each colour is held for `holdMs`; the first frame
 * of a step is taken `settleMs` after the change, because an LCD needs tens
 * of milliseconds to finish the transition and the camera begins compensating
 * for the new illuminant immediately. A frame grabbed at the instant of the
 * change can still show the previous colour, which would be scored against
 * the new one and look like a face that failed to respond.
 */
export interface ColourCapturePlan {
  sequence: string[];
  framesPerStep: number;
  holdMs: number;
  settleMs: number;
  /** Gap between successive frames inside one colour step. */
  gapMs: number;
  /** Idle time after the last frame of a step, before the next colour. */
  tailMs: number;
  /** Exactly what the backend expects: sequence.length * framesPerStep. */
  totalFrames: number;
  totalMs: number;
}

const HEX = /^#[0-9a-f]{6}$/i;

export function isColourChallenge(
  challenge: Pick<ActiveLivenessChallenge, 'challenge_type'>,
): boolean {
  return challenge.challenge_type === 'colour_reflection';
}

/**
 * Validate the colour-specific half of a challenge.
 *
 * Everything here is a bound on what the server told us, not a preference of
 * ours: a malformed challenge means we would capture frames nobody can score,
 * and the student would do the whole capture for nothing.
 */
export function validateColourChallenge(challenge: ActiveLivenessChallenge): ColourCapturePlan {
  const sequence = challenge.colour_sequence ?? [];
  const framesPerStep = challenge.frames_per_step ?? 0;
  const holdMs = challenge.hold_ms ?? 0;
  const settleMs = challenge.settle_ms ?? 0;

  if (sequence.length < 3 || sequence.length > 12 ||
      !sequence.every((colour) => HEX.test(colour)) ||
      !Number.isInteger(framesPerStep) || framesPerStep < 1 || framesPerStep > 4 ||
      !Number.isInteger(holdMs) || holdMs < 250 || holdMs > 2000 ||
      !Number.isInteger(settleMs) || settleMs < 0 || settleMs >= holdMs) {
    throw new ClientDataError('The liveness challenge is unavailable. Request a new challenge.');
  }

  const totalFrames = sequence.length * framesPerStep;
  if (totalFrames !== challenge.min_frames || totalFrames > challenge.max_frames) {
    // The backend requires EXACTLY this many frames and derives each frame's
    // colour from its position, so a mismatch here is unanswerable rather
    // than merely suboptimal.
    throw new ClientDataError('The liveness challenge is unavailable. Request a new challenge.');
  }

  const window = holdMs - settleMs;
  const gapMs = Math.floor(window / framesPerStep);
  return {
    sequence, framesPerStep, holdMs, settleMs, gapMs,
    tailMs: window - gapMs * (framesPerStep - 1),
    totalFrames,
    totalMs: sequence.length * holdMs,
  };
}

/**
 * Rate of colour change, in changes per second.
 *
 * Large-area flashing above roughly three per second is a photosensitive
 * seizure risk (WCAG 2.3.1), so this is asserted rather than assumed. The
 * server picks the timing; if a future change to it crossed the line we would
 * rather refuse the challenge than drive a student's screen at that rate.
 */
export function changesPerSecond(plan: ColourCapturePlan): number {
  return plan.holdMs > 0 ? 1000 / plan.holdMs : Infinity;
}

export const MAX_CHANGES_PER_SECOND = 3;

/**
 * Keep the screen awake for the duration of the capture.
 *
 * There is no web API to SET screen brightness -- no browser exposes one --
 * so the most the page can do about light output is fill the viewport with
 * the colour and stop the system dimming the panel part-way through. That
 * second part matters more than it sounds: an auto-dim in the middle of the
 * sequence changes the illuminant mid-measurement, which reads as a face that
 * stopped responding.
 *
 * Best effort by design. Wake Lock is unsupported in several browsers and is
 * refused outside a secure context, and a student with no wake lock should
 * still be able to check in -- so every failure is swallowed and the caller
 * gets a release function that is always safe to call.
 */
export interface WakeLockCapable {
  wakeLock?: { request(kind: 'screen'): Promise<{ release: () => Promise<void> }> };
}

export async function holdScreenAwake(
  // Injected rather than read straight off the global, because `navigator` is
  // a getter with no setter in Node, so a test cannot assign over it.
  nav: WakeLockCapable | undefined = globalThis.navigator as WakeLockCapable | undefined,
): Promise<() => void> {
  const lock = nav?.wakeLock;
  if (!lock) return () => {};
  try {
    const sentinel = await lock.request('screen');
    let released = false;
    return () => {
      if (released) return;
      released = true;
      void sentinel.release().catch(() => {});
    };
  } catch {
    return () => {};
  }
}
