'use client';

import { useEffect, useState } from 'react';
import { getPublicConfiguration } from '../../lib/client-config';
import type { User } from '../../types/api';

const roleLabels: Record<string, string> = {
  ta: 'Teaching assistant', instructor: 'Instructor', admin: 'Administrator',
};

export default function StaffLanding({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const [portalUrl, setPortalUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    getPublicConfiguration().then((configuration) => {
      if (active) setPortalUrl(configuration.staffPortalUrl);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <section className="w-full max-w-lg rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-gray-900">SAIV staff access</h1>
        <p className="mt-3 text-gray-700">Welcome, {user.full_name}.</p>
        <p className="mt-1 text-sm text-gray-600">{roleLabels[user.role]}</p>
        <p className="mt-4 text-gray-600">
          Use the existing staff portal for session management, attendance monitoring and reviews.
          Sign in separately with your SAIV account when prompted.
        </p>
        {portalUrl ? (
          <a href={portalUrl} rel="noreferrer" className="mt-5 inline-block rounded-lg bg-blue-600 px-4 py-3 text-white">
            Open staff portal
          </a>
        ) : (
          <p role={failed ? 'alert' : 'status'} className="mt-4 text-gray-600">
            {failed ? 'The staff portal link is unavailable. Please reload or contact your administrator.' : 'Loading staff portal link...'}
          </p>
        )}
        <button type="button" onClick={onSignOut} className="mt-5 block text-blue-700 underline">Sign out</button>
      </section>
    </main>
  );
}
