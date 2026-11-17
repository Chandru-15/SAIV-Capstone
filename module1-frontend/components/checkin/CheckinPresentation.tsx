import { forwardRef } from 'react';
import StudentIcon, { type StudentIconName } from '../ui/StudentIcon';
import type { ActiveLivenessChallenge, ActiveLivenessChallengeType } from '../../types/api';

const CHALLENGE_TITLES: Record<ActiveLivenessChallengeType, string> = {
  blink: 'Blink challenge',
  head_turn: 'Head-turn challenge',
  colour_reflection: 'Screen-light challenge',
};

/** Presentation only: the caller owns permissions, streams and challenge evidence. */
export function CheckinStepHeading({ number, icon, title, description, status, complete = false }: {
  number: number; icon: StudentIconName; title: string; description: string;
  status: string; complete?: boolean;
}) {
  return <div className="checkin-step-heading">
    <span className="checkin-step-icon"><StudentIcon name={icon} /></span>
    <h3>{number}. {title}</h3>
    <span className={`checkin-step-status${complete ? ' is-complete' : ''}`}>{status}</span>
    <p className="checkin-step-description">{description}</p>
  </div>;
}

/** Display the raw camera feed; no visual reflection or image-data transform. */
export const CheckinCameraPreview = forwardRef<HTMLVideoElement, { ready: boolean }>(function CheckinCameraPreview({ ready }, ref) {
  return <video ref={ref} autoPlay muted playsInline aria-label="Live camera preview"
    className={ready ? 'checkin-camera-preview' : 'hidden'}
    style={{ transform: 'none', scale: 'none', rotate: 'none' }} />;
});

export function CheckinChallengeStatus({ challenge, phase, remaining, progress }: {
  challenge: ActiveLivenessChallenge | null;
  phase: 'idle' | 'requesting' | 'ready' | 'capturing' | 'captured';
  remaining: number; progress: { count: number; total: number };
}) {
  return <div className="checkin-challenge-status">
    {challenge && <>
      <div className="checkin-challenge-heading"><h4><StudentIcon name="activity" />
        {CHALLENGE_TITLES[challenge.challenge_type] ?? 'Liveness challenge'}</h4>
        <span role="timer" aria-label="Challenge time remaining">Expires in {remaining}s</span>
      </div>
      {/* Retain the server's instruction, including any required direction. */}
      <p className="checkin-challenge-instruction">{challenge.instruction}</p>
      {challenge.challenge_type === 'colour_reflection' && <p className="checkin-challenge-instruction">
        {/* The page cannot raise the panel's brightness -- no browser exposes
            an API for it -- and the backend needs the screen's light to
            actually reach the face, so this has to be asked for. It also
            warns before the screen fills with colour, which matters for
            anyone who would rather look away. */}
        Turn your screen brightness up first. The screen will fill with
        colour for about {Math.round((challenge.colour_sequence?.length ?? 0)
          * (challenge.hold_ms ?? 0) / 1000)} seconds.
      </p>}
    </>}
    {phase === 'capturing' && <div role="status" aria-live="polite" className="checkin-capture-progress">
      <p>{challenge?.challenge_type === 'colour_reflection'
        ? 'Hold still and keep looking at the screen.'
        : progress.count === 0 ? 'Look ahead with your eyes open. Preparing first frame…'
        : challenge?.challenge_type === 'blink'
          ? (progress.count >= progress.total - 2 ? 'Open your eyes again and look at the camera.' : 'Blink once, then open your eyes.')
          : 'Slowly turn your head as instructed. Keep your face visible.'}</p>
      <div><progress aria-label="Liveness capture progress" value={progress.count} max={progress.total} />
        <span>{progress.count}/{progress.total} frames</span></div>
    </div>}
    {phase === 'captured' && <p role="status" className="checkin-captured">Frames captured. Submit promptly for server verification.</p>}
    {phase === 'requesting' && <p role="status">Requesting a server challenge…</p>}
  </div>;
}
