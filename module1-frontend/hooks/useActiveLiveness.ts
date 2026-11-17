'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { requestLivenessChallenge } from '../lib/student-api';
import { ClientDataError, parseApiError } from '../lib/api-error';
import { isColourChallenge } from '../lib/colour-liveness';
import {
  captureColourFrames, captureLivenessFrames, capturePlan, challengeRemaining,
} from '../lib/liveness';
import type { ActiveLivenessChallenge } from '../types/api';

type Phase = 'idle' | 'requesting' | 'ready' | 'capturing' | 'captured';

export function useActiveLiveness(sessionId: string, captureFrame: () => string, onUnauthorized: () => void) {
  const [challenge, setChallenge] = useState<ActiveLivenessChallenge | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [progress, setProgress] = useState({ count: 0, total: 0 });
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState('');
  // The colour the screen must be showing right now, or null when the screen
  // is not acting as a light source. Driven by the capture loop rather than
  // derived from progress: the loop knows when a colour has been painted and
  // settled, and that timing is what the measurement rests on.
  const [colour, setColour] = useState<string | null>(null);
  const evidence = useRef<{ sessionId: string; challenge: ActiveLivenessChallenge; frames: string[] } | null>(null);
  const operation = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const busy = useRef(false);
  const captureRef = useRef(captureFrame);
  const unauthorizedRef = useRef(onUnauthorized);
  captureRef.current = captureFrame;
  unauthorizedRef.current = onUnauthorized;

  const reset = useCallback((message = '') => {
    generation.current++;
    operation.current?.abort(); operation.current = null; busy.current = false;
    if (evidence.current) evidence.current.frames.length = 0;
    evidence.current = null;
    setChallenge(null); setPhase('idle'); setProgress({ count: 0, total: 0 }); setRemaining(0); setError(message);
    // Always clear the emitter. A reset can arrive mid-sequence -- an expiry,
    // a cancel, the tab being hidden -- and a full-screen colour left behind
    // would cover the page with no way to dismiss it.
    setColour(null);
  }, []);

  useEffect(() => {
    return () => {
      generation.current++; operation.current?.abort(); busy.current = false;
      if (evidence.current) evidence.current.frames.length = 0;
      evidence.current = null;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!challenge) return;
    const tick = () => {
      const milliseconds = challengeRemaining(challenge);
      setRemaining(Math.ceil(milliseconds / 1000));
      if (milliseconds <= 0) reset('Challenge expired. Request a new challenge; previous frames were discarded.');
    };
    tick();
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
  }, [challenge, reset]);

  async function request() {
    if (busy.current) return;
    reset(); busy.current = true;
    const version = generation.current;
    const controller = new AbortController(); operation.current = controller;
    setPhase('requesting');
    try {
      const issued = await requestLivenessChallenge(sessionId, controller.signal);
      if (version !== generation.current || controller.signal.aborted) return;
      if (challengeRemaining(issued) <= 0) throw new ClientDataError('Challenge expired. Check your device clock and request a new challenge.');
      evidence.current = { sessionId, challenge: issued, frames: [] };
      setChallenge(issued); setRemaining(Math.ceil(challengeRemaining(issued) / 1000)); setPhase('ready');
    } catch (failure) {
      if (version !== generation.current) return;
      const parsed = parseApiError(failure);
      reset(parsed.message);
      if (parsed.status === 401) unauthorizedRef.current();
    } finally {
      if (version === generation.current) busy.current = false;
    }
  }

  async function capture() {
    const current = evidence.current;
    if (busy.current || !current || current.sessionId !== sessionId) return;
    busy.current = true;
    const version = generation.current;
    const controller = new AbortController(); operation.current = controller;
    setPhase('capturing'); setError(''); setProgress({ count: 0, total: capturePlan(current.challenge).count });
    try {
      const report = (count: number, total: number) => {
        if (version === generation.current) setProgress({ count, total });
      };
      const paint = (next: string | null) => {
        if (version === generation.current) setColour(next);
      };
      const frames = isColourChallenge(current.challenge)
        ? await captureColourFrames(current.challenge, () => captureRef.current(),
            paint, controller.signal, report)
        : await captureLivenessFrames(current.challenge, () => captureRef.current(),
            controller.signal, report);
      setColour(null);
      if (version !== generation.current || controller.signal.aborted) { frames.length = 0; return; }
      current.frames = frames;
      setPhase('captured');
    } catch (failure) {
      if (version === generation.current) reset(parseApiError(failure).message);
    } finally {
      if (version === generation.current) busy.current = false;
    }
  }

  function takeEvidence(): { challenge_id: string; challenge_frames: string[] } {
    const current = evidence.current;
    const count = current?.frames.length ?? 0;
    const countWrong = current == null ? true
      : isColourChallenge(current.challenge)
        // Exact: the server works out each frame's colour from its position,
        // so a frame short is unanswerable rather than merely weaker.
        ? count !== current.challenge.min_frames
        : count < current.challenge.min_frames || count > current.challenge.max_frames;
    if (!current || current.sessionId !== sessionId || phase !== 'captured' ||
        challengeRemaining(current.challenge) <= 0 || countWrong) {
      reset('Capture a new liveness challenge before submitting.');
      throw new ClientDataError('Capture a new liveness challenge before submitting.');
    }
    const payload = { challenge_id: current.challenge.challenge_id, challenge_frames: current.frames.slice() };
    reset(); // Single attempt only, including all server errors and timeouts.
    return payload;
  }

  return { challenge, phase, progress, remaining, error, colour, request, capture, reset, takeEvidence };
}
