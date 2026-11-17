'use client';

import ModalFrame from '../ui/ModalFrame';

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getSessionVersion, isCurrentSession, subscribeAuthSession } from '../../lib/auth';
import { parseApiError } from '../../lib/api-error';
import { DeviceRegistrationFailure, prepareBrowserRegistration } from '../../lib/device-registration';
import type { BrowserRegistration, DeviceStage } from '../../lib/device-registration';
import type { User } from '../../types/api';
import type { BrowserKey } from '../../lib/browser-key';
import BrowserKeyGate from './BrowserKeyGate';

type State = { stage: DeviceStage } | { stage: 'ready'; registration: BrowserRegistration } |
  { stage: 'error'; message: string; rejected: boolean };

export default function DeviceRegistrationGate({ user, onClose, children }: {
  user: User; onClose: () => void;
  children: (identifier: string, invalidate: (error: unknown) => void, key?: BrowserKey) => ReactNode;
}) {
  const [state, setState] = useState<State>({ stage: 'checking' });
  const [attempt, setAttempt] = useState(0);
  const invalidate = useCallback((error: unknown) => {
    const failure = parseApiError(error);
    setState({ stage: 'error', message: failure.message || 'Your account changed. Sign in again.',
      rejected: error instanceof DeviceRegistrationFailure && error.rejected });
  }, []);
  useEffect(() => {
    let mounted = true;
    const version = getSessionVersion();
    setState({ stage: 'checking' });
    const unsubscribe = subscribeAuthSession(() => {
      if (mounted) setState({ stage: 'error', rejected: true, message: 'Your account changed. Sign in again.' });
    });
    void prepareBrowserRegistration({ id: user.id, role: user.role, is_active: user.is_active },
      (stage) => { if (mounted && isCurrentSession(version)) setState({ stage }); }).then((registration) => {
        if (mounted && isCurrentSession(version)) setState({ stage: 'ready', registration });
      }).catch((error: unknown) => {
        if (mounted && isCurrentSession(version)) invalidate(error);
      });
    return () => { mounted = false; unsubscribe(); };
  }, [user.id, user.role, user.is_active, attempt, invalidate]);

  if (state.stage === 'ready' && isCurrentSession(state.registration.version) && state.registration.userId === user.id) {
    const registration = state.registration;
    if (user.browser_verification_required) {
      return <BrowserKeyGate registration={registration} onClose={onClose} onRecheck={() => setAttempt(value => value + 1)}>
        {(key) => children(registration.identifier, invalidate, key)}
      </BrowserKeyGate>;
    }
    return <>{children(registration.identifier, invalidate)}</>;
  }
  return <ModalFrame label="Browser registration" onClose={onClose}>
      <h2 className="text-xl font-semibold text-gray-900">Prepare this browser for check-in</h2>
      {state.stage === 'error' ? <>
        <p role="alert" className="mt-4 text-sm text-red-700">{state.message}</p>
        {state.rejected && <p className="mt-3 text-sm text-gray-600">If staff restores access, sign out and sign in again before retrying.</p>}
        {!state.rejected && <button type="button" onClick={() => setAttempt((value) => value + 1)}
          className="btn-secondary mt-4">Check registration again</button>}
      </> : <p role="status" className="mt-4 text-sm text-gray-600">{state.stage === 'registering' ? 'Registering this browser…' : 'Checking device registration…'}</p>}
      <p className="mt-3 text-xs text-gray-500">This uses an application-scoped browser identifier, not hardware attestation. Camera and location capture remain off until registration is confirmed.</p>
      <button type="button" onClick={onClose} className="mt-4 w-full rounded-lg border border-gray-300 px-4 py-2 text-gray-700">Cancel</button>
  </ModalFrame>;
}
