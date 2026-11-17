'use client';

import { useState } from 'react';
import AuthGuard from '../../components/auth/AuthGuard';
import StudentHeader from '../../components/ui/StudentHeader';
import AvailableSessions from '../../components/student/AvailableSessions';
import AttendanceHistory from '../../components/student/AttendanceHistory';
import EnrolledCourses from '../../components/student/EnrolledCourses';
import AttendancePortalAction from '../../components/student/AttendancePortalAction';
import CheckInModal from '../../components/checkin/CheckInModal';
import { useStudentData } from '../../hooks/useStudentData';
import { clearAuthSession } from '../../lib/auth';
import { logoutUser } from '../../lib/auth-api';
import type { ActiveSession, FacePolicy, User } from '../../types/api';

export default function DashboardPage() {
  return <AuthGuard>{(user) => <DashboardContent key={user.id} initialUser={user} />}</AuthGuard>;
}

function DashboardContent({ initialUser }: { initialUser: User }) {
  const data = useStudentData();
  const user = initialUser;
  const [selectedCheckinSession, setSelectedCheckinSession] = useState<{ session: ActiveSession; policy: FacePolicy; retry: boolean } | null>(null);
  return <div className="student-app">
    <StudentHeader current="dashboard" onSignOut={() => { setSelectedCheckinSession(null); void logoutUser(); }} />
    <main id="main-content" className="page-width page-content space-y-6">
      <section className="welcome"><div><h1>Welcome back, {user.full_name}</h1><p>Find your session and check in for attendance.</p></div><AttendancePortalAction /></section>
      <AvailableSessions sessions={data.sessions} courses={data.courses} attempts={data.attempts}
        loading={data.sessionsLoading} error={data.sessionsError} historyReady={!data.historyLoading && !data.historyError}
        user={user} records={data.history.items} onRefresh={() => void data.refreshSessions()} findExistingAttempt={data.findExistingAttempt}
        onBegin={(session, policy, retry) => setSelectedCheckinSession({ session, policy, retry })} />
      <EnrolledCourses courses={data.courses} loading={data.sessionsLoading} error={data.sessionsError} />
      <AttendanceHistory items={data.history.items} total={data.history.total} complete={data.history.complete}
        loading={data.historyLoading} error={data.historyError} labels={data.labels}
        onLoadMore={() => void data.loadMore().catch(() => {})} onRefresh={() => void data.refreshHistory().catch(() => {})} />
    </main>
    {selectedCheckinSession && <CheckInModal user={user} session={selectedCheckinSession.session} policy={selectedCheckinSession.policy}
      priorAttempt={selectedCheckinSession.retry} onClose={() => setSelectedCheckinSession(null)}
      onCompleted={(record) => data.refreshHistory(record)} onUnauthorized={() => clearAuthSession()} />}
  </div>;
}
