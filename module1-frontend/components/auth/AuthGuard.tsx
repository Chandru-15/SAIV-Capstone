'use client';

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import axios from 'axios';
import { clearAuthSession, getAccessToken, getSessionVersion, isCurrentSession, subscribeAuthSession } from '../../lib/auth';
import { parseApiError } from '../../lib/api-error';
import { getCurrentUser, logoutUser } from '../../lib/auth-api';
import { frontendAccess, isValidatedProfile } from '../../lib/access';
import type { User } from '../../types/api';
import StaffLanding from './StaffLanding';

interface AuthGuardProps {
  children: (user: User) => ReactNode;
}

type ValidationState =
  | { status: 'checking' | 'error' | 'denied' }
  | { status: 'validated'; user: User };

export default function AuthGuard({ children }: AuthGuardProps) {
  const router = useRouter();
  const [state, setState] = useState<ValidationState>({ status: 'checking' });
  const [attempt, setAttempt] = useState(0);
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: 'checking' });
    const version = getSessionVersion();
    let redirected = false;
    const leave = () => {
      controller.abort();
      setState({ status: 'checking' });
      if (!redirected) { redirected = true; router.replace('/login'); }
    };
    const unsubscribe = subscribeAuthSession(leave);
    const hide = () => setState({ status: 'checking' });
    const resume = (event: PageTransitionEvent) => { if (event.persisted) setAttempt((value) => value + 1); };
    window.addEventListener?.('pagehide', hide);
    window.addEventListener?.('pageshow', resume);

    async function validate() {
      try {
        const token = getAccessToken();
        if (!token) {
          router.replace('/login');
          return;
        }
        const user: unknown = await getCurrentUser(controller.signal);
        if (controller.signal.aborted) return;
        // Never mount a response belonging to a session replaced during validation.
        if (!isCurrentSession(version)) {
          router.replace('/login');
          return;
        }
        if (!isValidatedProfile(user)) {
          setState({ status: 'denied' });
          return;
        }
        setState({ status: 'validated', user });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (axios.isAxiosError(error) && [401, 403].includes(error.response?.status ?? 0)) {
          clearAuthSession();
          return;
        }
        const failure = parseApiError(error);
        if (failure.kind === 'cancelled') return;
        setErrorMessage(failure.message);
        setState({ status: 'error' });
      }
    }

    void validate();
    return () => {
      controller.abort();
      unsubscribe();
      window.removeEventListener?.('pagehide', hide);
      window.removeEventListener?.('pageshow', resume);
    };
  }, [router, attempt]);

  function signOut() {
    void logoutUser();
  }

  if (state.status === 'validated') {
    const access = frontendAccess(state.user.role);
    if (access === 'student') return <>{children(state.user)}</>;
    if (access === 'staff') return <StaffLanding user={state.user} onSignOut={signOut} />;
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-sm">
        {state.status === 'checking' ? (
          <p role="status" className="text-gray-600">Checking your session...</p>
        ) : state.status === 'error' ? (
          <>
            <h1 className="text-xl font-semibold text-gray-900">Unable to verify your account</h1>
            <p role="alert" className="mt-3 text-gray-600">
              {errorMessage} Attendance features are unavailable until your account is verified.
            </p>
            <button type="button" onClick={() => setAttempt((value) => value + 1)}
              className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-white">Try again</button>
          </>
        ) : (
          <>
            <h1 className="text-xl font-semibold text-gray-900">Access unavailable</h1>
            <p role="alert" className="mt-3 text-gray-600">
              Your account does not have a supported active role. Contact your course administrator.
            </p>
          </>
        )}
        {state.status !== 'checking' && (
          <button type="button" onClick={signOut}
            className="mt-4 block text-blue-700 underline">Sign out</button>
        )}
      </div>
    </main>
  );
}
