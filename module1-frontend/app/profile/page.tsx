'use client';

import { useState } from 'react';
import AuthGuard from '../../components/auth/AuthGuard';
import StudentHeader from '../../components/ui/StudentHeader';
import StudentSetup from '../../components/student/StudentSetup';
import BrowserRegistration from '../../components/student/BrowserRegistration';
import { logoutUser } from '../../lib/auth-api';
import { formatJoinedDate } from '../../lib/student-display';
import StudentIcon from '../../components/ui/StudentIcon';
import type { User } from '../../types/api';

export default function ProfilePage() {
  return <AuthGuard>{(user) => <ProfileContent key={user.id} initialUser={user} />}</AuthGuard>;
}

function ProfileContent({ initialUser }: { initialUser: User }) {
  const [user, setUser] = useState(initialUser);
  return <div className="student-app student-profile">
    <StudentHeader current="profile" onSignOut={() => { void logoutUser(); }} />
    <main id="main-content" className="page-width page-content space-y-6">
      <div className="profile-heading"><span className="profile-heading-icon"><StudentIcon name="user" /></span><div><h1>Profile & Settings</h1><p>Manage your account, consents and browser registration.</p></div></div>
      <section className="panel personal-information" aria-labelledby="personal-title">
        <div><h2 id="personal-title" className="section-title">Personal Information</h2><p className="mt-1 text-sm muted">Your account details.</p></div>
        <dl className="detail-list personal-details">
          <div><dt>Full name</dt><dd>{user.full_name}</dd></div>
          <div><dt>Email</dt><dd>{user.email}</dd></div>
          <div><dt>Role</dt><dd><span className="tag tag-role capitalize">{user.role}</span></dd></div>
          <div><dt>Joined</dt><dd className="joined-date"><StudentIcon name="calendar" />{formatJoinedDate(user.created_at)}</dd></div>
        </dl>
      </section>
      <StudentSetup user={user} onUserUpdate={setUser} />
      <BrowserRegistration key={user.id} userId={user.id} />
    </main>
  </div>;
}
