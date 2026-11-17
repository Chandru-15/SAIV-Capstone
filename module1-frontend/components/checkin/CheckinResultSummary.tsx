import type { CheckInResponse } from '../../types/api';
import StudentIcon from '../ui/StudentIcon';
import { riskSignalCodes, riskSignalExplanation } from '../../lib/verification-messages';

const outcomes = {
  approved: ['Check-in Approved', 'Attendance recorded as approved.'],
  rejected: ['Check-in Rejected', 'Your submission was recorded; attendance was not approved.'],
  flagged: ['Check-in Flagged', 'Recorded for staff review; attendance is not yet approved.'],
  pending: ['Check-in Pending', 'Recorded and awaiting a decision.'],
  appealed: ['Check-in Appealed', 'Your appeal is awaiting a staff decision.'],
} as const;

export default function CheckinResultSummary({ result, activeChallenge }: { result: CheckInResponse; activeChallenge: boolean }) {
  const [title, explanation] = outcomes[result.status];
  const verdict = (passed: boolean | null | undefined) => passed == null ? 'Not verified' : passed ? 'Passed' : 'Not passed';
  return <>
    <div className={`result-status result-status-${result.status}`} role="status">
      <StudentIcon name={result.status === 'approved' ? 'check' : result.status === 'rejected' ? 'close' : 'info'} />
      <div><h3>{title}</h3><p>{explanation}</p></div>
    </div>
    <dl className="result-metrics">
      <div><StudentIcon name="face" /><div><dt>Face recognition</dt><dd>{result.face_match_score == null ? '—' : result.face_match_score.toFixed(2)}<small>{verdict(result.face_match_passed)}</small></dd></div></div>
      <div><StudentIcon name="activity" /><div><dt>Effective liveness score</dt><dd>{result.liveness_score == null ? '—' : result.liveness_score.toFixed(2)}<small>{verdict(result.liveness_passed)}</small></dd></div></div>
      <div><StudentIcon name="shield" /><div><dt>Overall risk</dt><dd>{result.risk_score.toFixed(3)}</dd></div></div>
      <div><StudentIcon name="location" /><div><dt>Distance from venue</dt><dd>{result.distance_from_venue_meters == null ? '—' : `${Math.round(result.distance_from_venue_meters)} m`}<small>Backend calculated</small></dd></div></div>
    </dl>
    {result.browser_key_verified === true && <p className="notice mb-3">Browser key verified for this attempt.</p>}
    {activeChallenge && result.liveness_passed === false && result.liveness_score === 0 && <p className="notice mb-3">Active challenge not verified. The backend records an effective score of 0.00 on challenge failure.</p>}
  </>;
}
