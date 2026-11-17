'use client';

import { useEffect, useRef, useState } from 'react';
import type { ActiveSession, CheckinHistoryItem, FacePolicy, StudentCourse, User } from '../../types/api';
import { sessionEligibility } from '../../lib/student-policy';
import type { CheckinAttemptState } from '../../lib/student-policy';
import Link from 'next/link';
import { formatSessionRange } from '../../lib/student-display';
import StudentIcon from '../ui/StudentIcon';
import StudentSectionHeading, { RefreshButton } from '../ui/StudentSectionHeading';
import { StatusBadge, EmptyState, Notice } from '../ui/Feedback';
import { parseApiError } from '../../lib/api-error';

export default function AvailableSessions({ sessions, courses, attempts, loading, historyReady, error, user, onRefresh, findExistingAttempt, onBegin, records = [] }: {
  records?: CheckinHistoryItem[]; sessions: ActiveSession[]; courses: StudentCourse[] | null;
  attempts: ReadonlyMap<string, CheckinAttemptState>;
  loading: boolean; historyReady: boolean; error: string; user: User;
  onRefresh: () => void; findExistingAttempt: (id: string) => Promise<CheckinAttemptState | null>;
  onBegin: (session: ActiveSession, policy: FacePolicy, retry: boolean) => void;
}) {
  const [now, setNow] = useState(Date.now());
  const [checking, setChecking] = useState<string | null>(null);
  const [attemptError, setAttemptError] = useState('');
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => { mounted.current = false; clearInterval(interval); };
  }, []);

  async function begin(session: ActiveSession) {
    if (checking) return;
    setAttemptError('');
    setChecking(session.id);
    try {
      const initial = sessionEligibility(session, courses, attempts, Date.now());
      if (!initial.eligible || !historyReady) { setAttemptError(initial.reason); return; }
      if (!user.camera_consent || !user.geolocation_consent || (initial.policy?.faceMatchRequired && !user.face_enrolled)) {
        setAttemptError('Complete the required consent and face enrollment before checking in.');
        return;
      }
      // Only a settled check-in blocks the attempt. A flagged or pending one is
      // carried into the modal as a retry, so the first submit is sent as one
      // rather than being refused as a duplicate.
      const existing = await findExistingAttempt(session.id);
      if (!mounted.current) return;
      if (existing === 'settled') { setAttemptError('You have already checked in to this session.'); return; }
      const latest = sessionEligibility(session, courses, attempts, Date.now());
      if (!latest.eligible || !latest.policy) { setAttemptError(latest.reason); return; }
      if (!user.camera_consent || !user.geolocation_consent || (latest.policy.faceMatchRequired && !user.face_enrolled)) return;
      onBegin(session, latest.policy, latest.retry || existing === 'retryable');
    } catch (error) {
      const failure = parseApiError(error);
      if (mounted.current && failure.kind !== 'cancelled') setAttemptError(failure.message);
    } finally { if (mounted.current) setChecking(null); }
  }

  return (
    <section className="panel" aria-labelledby="sessions-title">
      <StudentSectionHeading id="sessions-title" title="Active Sessions" description="Sessions available for check-in · Singapore time." icon="calendar">
        <RefreshButton label="Refresh sessions" onClick={onRefresh} disabled={loading || !!checking} />
      </StudentSectionHeading>
      {loading && <p role="status" className="py-6 muted">Loading active sessions...</p>}
      {error && <Notice error>{error}</Notice>}
      {attemptError && <div className="mb-4"><Notice error>{attemptError}</Notice></div>}
      {!loading && !error && !sessions.length && <EmptyState>No active sessions available for your enrolled courses.</EmptyState>}
      {!error && !loading && <div className="session-grid">
        {sessions.map((session) => {
          const eligibility = sessionEligibility(session, courses, attempts, now);
          const setupMissing = !user.camera_consent || !user.geolocation_consent || (eligibility.policy?.faceMatchRequired && !user.face_enrolled);
          const course = courses?.find(course => course.id === session.course_id);
          const settled = attempts.get(session.id) === 'settled';
          const record = records.find(item => item.session_id === session.id);
          return (
            <article key={session.id} className="session-card session-summary">
              <div className="session-overview">
                <div className="session-course"><span className="tag tag-role">{course?.code || 'Course unavailable'}</span><span>{course?.name}</span></div>
                <h3>{session.name}</h3>
                <p className="session-venue"><StudentIcon name="location" /><span className="capitalize">{session.session_type}</span><span aria-hidden="true">·</span>{session.venue_name || 'Venue not supplied'}</p>
                <dl className="session-times">
                  <div><dt><StudentIcon name="calendar" /><span className="sr-only">Session time</span></dt><dd>{formatSessionRange(session.scheduled_start, session.scheduled_end)}</dd></div>
                  <div><dt><StudentIcon name="history" /><span className="sr-only">Check-in window</span></dt><dd><span className="muted">Check-in: </span>{formatSessionRange(session.checkin_opens_at, session.checkin_closes_at)}</dd></div>
                </dl>
              </div>
              <div className="session-actions">
                <div className="session-requirements">
                  <span className="tag tag-role"><StudentIcon name="face" />Face match: {eligibility.policy ? eligibility.policy.faceMatchRequired ? 'Required' : 'Not required' : 'Policy unavailable'}</span>
                  <span className="tag tag-role"><StudentIcon name="activity" />Liveness: {session.require_liveness_check ? 'Required' : 'Not required'}</span>
                </div>
                <div className="session-outcome">{record ? <StatusBadge status={record.status} /> : <span className={`tag ${settled ? 'tag-neutral' : eligibility.eligible && historyReady && !setupMissing ? 'tag-approved' : 'tag-pending'}`}>
                  {settled ? 'Submission recorded' : !eligibility.eligible ? 'Unavailable' : setupMissing ? 'Setup needed' : !historyReady ? 'History needed' : 'Check-in open'}</span>}</div>
                <button type="button" onClick={() => void begin(session)} disabled={!eligibility.eligible || !historyReady || !!checking || !!setupMissing} className="btn-primary session-checkin">
                  {checking === session.id ? 'Checking attendance...' : eligibility.reason}
                </button>
              </div>
              {eligibility.retry && eligibility.eligible && <p className="session-guidance">You can retry before the window closes, subject to the server’s cooldown.</p>}
              {setupMissing && <p className="session-guidance text-amber-800">Complete camera and location consent{eligibility.policy?.faceMatchRequired ? ', and face enrollment' : ''}. <Link href="/profile" className="underline">Open settings</Link></p>}
              {!historyReady && <p className="session-guidance text-amber-800">Load attendance history before checking in.</p>}
            </article>
          );
        })}
      </div>}
      <p className="section-footnote">The server confirms eligibility and verification at submission.</p>
    </section>
  );
}
