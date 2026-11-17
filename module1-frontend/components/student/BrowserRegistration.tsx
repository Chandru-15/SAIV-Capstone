'use client';

import StudentIcon from '../ui/StudentIcon';
import StudentSectionHeading, { RefreshButton } from '../ui/StudentSectionHeading';
import { useEffect, useState } from 'react';
import { getMyDevices } from '../../lib/device-api';
import { readBrowserDeviceIdentifier } from '../../lib/browser-device';
import { readBrowserKey } from '../../lib/browser-key';
import { getSessionVersion, isCurrentSession } from '../../lib/auth';
import { formatDateTime } from '../../lib/student-policy';
import type { RegisteredDevice } from '../../types/api';

type LocalKeyStatus = 'Checking…' | 'Key enrolled · Available here' | 'Recovery required · No matching local key' | 'Unavailable';

type RegistrationState =
  | { owner: string; status: 'loading' | 'error' }
  | { owner: string; status: 'loaded'; identifier: string | null; devices: RegisteredDevice[]; localKeyStatus?: LocalKeyStatus };

export function shortIdentifier(identifier: string): string {
  return `${identifier.slice(0, 8)}…${identifier.slice(-4)}`;
}

export default function BrowserRegistration({ userId }: { userId: string }) {
  const [state, setState] = useState<RegistrationState>({ owner: userId, status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const version = getSessionVersion();
    setState({ owner: userId, status: 'loading' });
    async function load() {
      try {
        const identifier = readBrowserDeviceIdentifier();
        const devices = await getMyDevices(controller.signal);
        if (controller.signal.aborted || !isCurrentSession(version)) return;
        // Never show another account's metadata, even if a malformed API response supplies it.
        if (devices.some(device => device.user_id !== userId)) throw new Error('Unexpected device owner');
        const current = devices.find(device => device.device_fingerprint === identifier);
        const loaded = { owner: userId, status: 'loaded' as const, identifier, devices };
        if (!identifier || !current?.is_active || !current.browser_key_id || current.browser_key_usable !== true) {
          setState(loaded);
          return;
        }

        setState({ ...loaded, localKeyStatus: 'Checking…' });
        let localKeyStatus: LocalKeyStatus = 'Unavailable';
        try {
          const key = await readBrowserKey(userId, identifier);
          localKeyStatus = key?.keyId === current.browser_key_id
            ? 'Key enrolled · Available here'
            : 'Recovery required · No matching local key';
        } catch {
          // A storage error cannot establish that the key is missing.
        }
        if (!controller.signal.aborted && isCurrentSession(version)) setState({ ...loaded, localKeyStatus });
      } catch {
        if (!controller.signal.aborted && isCurrentSession(version)) setState({ owner: userId, status: 'error' });
      }
    }
    void load();
    return () => controller.abort();
  }, [userId, attempt]);

  const currentState = state.owner === userId ? state : { owner: userId, status: 'loading' as const };
  const loaded = currentState.status === 'loaded' ? currentState : null;
  const current = loaded?.devices.find(device => device.device_fingerprint === loaded.identifier);

  return <section className="panel" aria-labelledby="browser-title">
    <StudentSectionHeading id="browser-title" title="Browser Registration" description="Registered browsers for attendance." icon="browser">
      <RefreshButton label="Refresh browsers" onClick={() => setAttempt(n => n + 1)} disabled={currentState.status === 'loading'} />
    </StudentSectionHeading>
    {currentState.status === 'loading' && <p role="status" className="muted">Loading browser registrations…</p>}
    {currentState.status === 'error' && <p role="alert" className="notice notice-error">Browser registration information is unavailable. Allow site storage and try refreshing. No browser was registered by this page.</p>}
    {loaded && <div className="browser-list">
      {!current && <div className="browser-empty-current"><StudentIcon name="browser" /><div><h3>This Browser <span className="tag tag-neutral">Not registered to this account</span></h3><p>{loaded.identifier ? shortIdentifier(loaded.identifier) : 'No saved browser identifier.'}</p></div></div>}
      {[...loaded.devices].sort((a, b) => Number(b === current) - Number(a === current)).map(device => (
        <BrowserRegistrationRow key={device.id} device={device} current={device === current}
          localKeyStatus={device === current ? loaded.localKeyStatus : undefined} />
      ))}
      {!loaded.devices.length && <p className="browser-none">No registered browsers were returned for this account.</p>}
    </div>}
    <p className="settings-note"><StudentIcon name="info" />Registration identifies the browser by UUID. An enrolled signing key proves key possession during check-in. Neither establishes hardware identity.</p>
  </section>;
}

/** Presentation only; ownership filtering and read-only loading remain above. */
export function BrowserRegistrationRow({ device, current, localKeyStatus }: {
  device: RegisteredDevice;
  current: boolean;
  localKeyStatus?: LocalKeyStatus;
}) {
  let signingKeyStatus = 'Unavailable';
  if (device.browser_key_id === null && device.browser_key_usable !== true) {
    signingKeyStatus = 'Not enrolled';
  } else if (device.is_active && device.browser_key_id && device.browser_key_usable !== false) {
    signingKeyStatus = current ? localKeyStatus ?? 'Unavailable' : 'Key enrolled (server record)';
  }

  return <details className="browser-row">
        <summary>
          <StudentIcon name="browser" />
          <span className="browser-identity"><span className={current ? 'browser-current-label' : ''}>{current ? 'This Browser' : 'Other Browser'}</span><strong>{shortIdentifier(device.device_fingerprint)}</strong></span>
          <span className="browser-trust"><span className="sr-only">Backend Trust Assessment: </span>{device.platform && <span className="capitalize">{device.platform} · </span>}<span className="capitalize">{device.trust_score}</span> trust</span>
          <span className={`tag ${device.is_active ? 'tag-approved' : 'tag-rejected'}`}>{device.is_active ? 'Active' : 'Inactive'}</span><StudentIcon name="arrow" className="disclosure-arrow" />
        </summary>
        <div className="browser-details"><dl className="detail-list sm:grid-cols-3">
          <div><dt>Browser / platform</dt><dd>{[device.browser, device.platform, device.os_version].filter(Boolean).join(' · ') || 'Not supplied'}</dd></div>
          <div><dt>First registered</dt><dd>{formatDateTime(device.first_seen_at)}</dd></div>
          <div><dt>Last seen</dt><dd>{formatDateTime(device.last_seen_at)}</dd></div>
          <div><dt>Browser signing key</dt><dd>{signingKeyStatus}</dd></div>
        </dl>{!device.is_active && <p className="mt-3 text-xs muted">Inactive registrations may be revoked. The API does not distinguish these states; contact course staff. This page cannot reactivate registration.</p>}</div>
      </details>;
}
