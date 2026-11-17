'use client';

import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { isCurrentSession } from '../../lib/auth';
import { ClientDataError, parseApiError } from '../../lib/api-error';
import { prepareBrowserKey, readBrowserKey, signBrowserChallenge } from '../../lib/browser-key';
import type { BrowserKey } from '../../lib/browser-key';
import { completeBrowserEnrollment, getMyDevices, requestBrowserEnrollment } from '../../lib/device-api';
import type { BrowserRegistration } from '../../lib/device-registration';
import type { RegisteredDevice } from '../../types/api';
import ModalFrame from '../ui/ModalFrame';

type State = { phase: 'loading' | 'unavailable' } | { phase: 'setup'; others: RegisteredDevice[] } |
  { phase: 'ready'; key: BrowserKey };

export default function BrowserKeyGate({ registration, onClose, onRecheck, children }: {
  registration: BrowserRegistration;
  onClose: () => void;
  onRecheck: () => void;
  children: (key: BrowserKey) => ReactNode;
}) {
  const { identifier, userId, version, device } = registration;
  const [state, setState] = useState<State>({ phase: 'loading' });
  const [recoveryId, setRecoveryId] = useState(device.browser_key_id ? device.id : '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const flight = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const current = () => !controller.signal.aborted && isCurrentSession(version);
    void (async () => {
      try {
        const key = await readBrowserKey(userId, identifier);
        if (!current()) return;
        if (key && key.keyId === device.browser_key_id && device.browser_key_usable !== false) {
          setState({ phase: 'ready', key });
          return;
        }
        // If site data was cleared, the browser has a new UUID. Let the
        // student explicitly select the old key to revoke during enrollment.
        const others = device.browser_key_id ? [] : (await getMyDevices(controller.signal)).filter(
          item => item.user_id === userId && item.id !== device.id && item.is_active && item.browser_key_id,
        );
        if (current()) setState({ phase: 'setup', others });
      } catch (failure) {
        if (current()) {
          setState({ phase: 'unavailable' });
          setError(parseApiError(failure).message);
        }
      }
    })();
    return () => { controller.abort(); flight.current?.abort(); };
  }, [device, identifier, userId, version]);

  async function enroll(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (flight.current || !isCurrentSession(version)) return;
    const field = event.currentTarget.elements.namedItem('browser-password') as HTMLInputElement | null;
    const password = field?.value;
    if (!field || !password) return;
    field.value = '';
    const controller = new AbortController();
    const { signal } = controller;
    const current = () => !signal.aborted && isCurrentSession(version);
    flight.current = controller;
    setBusy(true); setError('');
    try {
      const key = await prepareBrowserKey(userId, identifier, signal, device.browser_key_id ?? undefined);
      if (!current()) return;
      const challenge = await requestBrowserEnrollment(device.id, key.publicKey, recoveryId || undefined, signal);
      const signature = await signBrowserChallenge(key, challenge);
      if (!current()) return;
      const enrolled = await completeBrowserEnrollment(challenge.challenge_id, key.publicKey, signature, password, signal);
      if (!current()) return;
      if (enrolled.user_id !== userId || enrolled.id !== device.id || enrolled.device_fingerprint !== identifier ||
          !enrolled.is_active || enrolled.browser_key_usable === false || enrolled.browser_key_id !== key.keyId) {
        throw new ClientDataError('Browser enrollment could not be confirmed. Check browser key status.');
      }
      setState({ phase: 'ready', key });
    } catch (failure) {
      if (current()) setError(parseApiError(failure).message);
    } finally {
      flight.current = null;
      if (current()) setBusy(false);
    }
  }

  if (state.phase === 'ready' && isCurrentSession(version)) return <>{children(state.key)}</>;

  return <ModalFrame label="Browser verification" onClose={onClose}>
    <h2 className="text-xl font-semibold text-gray-900">{recoveryId ? 'Recover your browser key' : 'Set up browser verification'}</h2>
    <p className="mt-3 text-sm text-gray-600">
      A signing key stays in this browser and proves it can sign your check-in. It does not verify hardware.
    </p>
    {state.phase === 'loading' && <p role="status" className="mt-3 text-sm">Checking your browser key…</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    {state.phase === 'setup' && <form onSubmit={enroll} className="mt-4 space-y-3">
      {state.others.length > 0 && <label className="block text-sm font-medium">
        Setup option
        <select className="mt-1 block w-full rounded-lg border border-gray-300 p-2" value={recoveryId}
          disabled={busy} onChange={event => setRecoveryId(event.target.value)}>
          <option value="">Enroll this browser separately</option>
          {state.others.map(item => <option key={item.id} value={item.id}>
            Replace browser {item.device_fingerprint.slice(0, 8)}…{item.device_fingerprint.slice(-4)}
          </option>)}
        </select>
      </label>}
      {recoveryId && <p className="text-sm text-gray-600">Recovery replaces the selected key. That key can no longer sign check-ins.</p>}
      <label className="block text-sm font-medium">
        Confirm your account password
        <input className="mt-1 block w-full rounded-lg border border-gray-300 p-2" type="password"
          name="browser-password" autoComplete="current-password" maxLength={128} required disabled={busy} />
      </label>
      <button className="btn-primary" type="submit" disabled={busy}>
        {busy ? 'Confirming…' : recoveryId ? 'Recover browser key' : 'Enroll browser key'}
      </button>
    </form>}
    {error && <button className="btn-secondary mt-3" type="button" disabled={busy} onClick={onRecheck}>
      Check browser key status
    </button>}
    <button type="button" onClick={onClose} className="btn-secondary mt-4 w-full">Cancel</button>
  </ModalFrame>;
}
