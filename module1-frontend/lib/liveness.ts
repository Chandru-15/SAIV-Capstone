import { ClientDataError } from './api-error';
import {
  MAX_CHANGES_PER_SECOND, changesPerSecond, holdScreenAwake, isColourChallenge,
  validateColourChallenge,
} from './colour-liveness';
import { parseUtcTimestamp } from './student-policy';
import type { ActiveLivenessChallenge } from '../types/api';

const MIN_FRAMES_BY_TYPE: Record<string, number> = {
  blink: 3, head_turn: 2, colour_reflection: 3,
};

export function validateChallenge(value: unknown): ActiveLivenessChallenge {
  const item = value as ActiveLivenessChallenge | null;
  if (!item || typeof item.challenge_id !== 'string' || !item.challenge_id.trim() ||
      !['blink', 'head_turn', 'colour_reflection'].includes(item.challenge_type) ||
      typeof item.instruction !== 'string' || !item.instruction.trim() || item.instruction.length > 400 ||
      !Number.isInteger(item.min_frames) || !Number.isInteger(item.recommended_frames) ||
      !Number.isInteger(item.max_frames) ||
      item.min_frames < (MIN_FRAMES_BY_TYPE[item.challenge_type] ?? 3) ||
      item.recommended_frames < item.min_frames || item.max_frames < item.recommended_frames ||
      item.max_frames > 30 || parseUtcTimestamp(item.expires_at) === null) {
    throw new ClientDataError('The liveness challenge is unavailable. Request a new challenge.');
  }
  if (isColourChallenge(item)) {
    const plan = validateColourChallenge(item);
    // Refuse to drive the screen faster than the photosensitivity guidance,
    // whatever the server asked for. The server already keeps itself under
    // this, so reaching here means a bug on one side or the other -- and the
    // safe failure is "request a new challenge", not "flash the screen".
    if (changesPerSecond(plan) > MAX_CHANGES_PER_SECOND) {
      throw new ClientDataError('The liveness challenge is unavailable. Request a new challenge.');
    }
  }
  return item;
}

export function challengeRemaining(challenge: ActiveLivenessChallenge, now = Date.now()): number {
  return Math.max(0, (parseUtcTimestamp(challenge.expires_at) ?? 0) - now);
}

export function capturePlan(challenge: ActiveLivenessChallenge): { count: number; interval: number } {
  if (isColourChallenge(challenge)) {
    // Fixed by the server, not chosen here. The count must be exact -- the
    // backend reads each frame's colour from its position in the list -- and
    // the interval is the colour hold time rather than a sampling rate.
    const plan = validateColourChallenge(challenge);
    return { count: plan.totalFrames, interval: plan.holdMs };
  }
  // Sample over at least 3.5 seconds. 24 frames at ~150 ms gives a brief
  // blink several chances to appear, while allowing a slow head turn.
  // Smaller server caps extend the interval; never exceed server limits.
  const count = Math.min(challenge.max_frames, Math.max(24, challenge.recommended_frames));
  return { count, interval: Math.max(150, Math.ceil(3500 / (count - 1))) };
}

export function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Capture cancelled.', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, milliseconds);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
  });
}

/**
 * Drive the screen through the colour sequence while sampling the camera.
 *
 * The ordering is the whole point and it is not interchangeable with the
 * gesture loop. For each step: show the colour, wait for the panel to finish
 * the transition, take this step's frames, then idle out the rest of the hold
 * so the next colour starts on schedule. A frame taken before the settle
 * delay can still carry the previous colour, which the backend would score
 * against the new one and read as a face that did not respond.
 *
 * `show` paints the colour. It is passed in rather than done here because
 * this module must stay free of React -- but it means the caller has to have
 * actually rendered the colour before the settle delay elapses, which the
 * settle delay is long enough to allow for several times over.
 */
export async function captureColourFrames(
  challenge: ActiveLivenessChallenge,
  capture: () => string,
  show: (colour: string | null) => void,
  signal: AbortSignal,
  progress: (count: number, total: number) => void,
  wait = abortableDelay,
  lock = holdScreenAwake,
): Promise<string[]> {
  const plan = validateColourChallenge(validateChallenge(challenge));
  const frames: string[] = [];
  const release = await lock();
  try {
    for (const colour of plan.sequence) {
      if (signal.aborted) throw new DOMException('Capture cancelled.', 'AbortError');
      if (challengeRemaining(challenge) <= 0) {
        throw new ClientDataError('Challenge expired. Request a new challenge.');
      }
      show(colour);
      await wait(plan.settleMs, signal);
      for (let index = 0; index < plan.framesPerStep; index++) {
        const frame = capture();
        // A repeated frame means the camera stopped delivering new images, so
        // the later colours were never actually recorded. Capture validation,
        // never a liveness verdict.
        if (!frame || frames.includes(frame)) {
          throw new ClientDataError('The camera image did not update. Use good lighting, keep the camera visible and retry.');
        }
        frames.push(frame);
        progress(frames.length, plan.totalFrames);
        if (index + 1 < plan.framesPerStep) await wait(plan.gapMs, signal);
      }
      await wait(plan.tailMs, signal);
    }
    if (frames.length !== plan.totalFrames) {
      // Unreachable by construction, and asserted anyway: a short list would
      // be scored against the wrong colours frame for frame.
      throw new ClientDataError('The colour sequence did not complete. Request a new challenge.');
    }
    return frames;
  } catch (error) {
    frames.length = 0;
    throw error;
  } finally {
    show(null);
    release();
  }
}

export async function captureLivenessFrames(
  challenge: ActiveLivenessChallenge,
  capture: () => string,
  signal: AbortSignal,
  progress: (count: number, total: number) => void,
  wait = abortableDelay,
): Promise<string[]> {
  const frames: string[] = [];
  const plan = capturePlan(validateChallenge(challenge));
  try {
    // Give the student time to face forward with eyes open before frame 1,
    // which the backend uses for identity verification.
    await wait(1000, signal);
    for (let index = 0; index < plan.count; index++) {
      if (signal.aborted) throw new DOMException('Capture cancelled.', 'AbortError');
      if (challengeRemaining(challenge) <= 0) throw new ClientDataError('Challenge expired. Request a new challenge.');
      const frame = capture();
      // Reject a frozen/reused image sequence rather than padding it with
      // duplicate frames. This is capture validation, never a liveness verdict.
      if (!frame || frames.includes(frame)) throw new ClientDataError('The camera image did not update. Use good lighting, keep the camera visible and retry.');
      frames.push(frame);
      progress(frames.length, plan.count);
      if (index + 1 < plan.count) await wait(plan.interval, signal);
    }
    return frames;
  } catch (error) {
    frames.length = 0;
    throw error;
  }
}
