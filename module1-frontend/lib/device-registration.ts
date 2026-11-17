import { CanceledError } from 'axios';
import { ClientDataError, parseApiError } from './api-error';
import { getSessionVersion, isCurrentSession, subscribeAuthSession } from './auth';
import { getBrowserDeviceIdentifier } from './browser-device';
import { getMyDevices, registerBrowserDevice } from './device-api';
import type { RegisteredDevice, User } from '../types/api';

export type DeviceStage = 'checking' | 'registering';
type RegistrationUser = Pick<User, 'id' | 'role' | 'is_active'>;
export interface BrowserRegistration {
  identifier: string;
  userId: string;
  version: number;
  device: RegisteredDevice;
}
export class DeviceRegistrationFailure extends ClientDataError {
  constructor(message: string, readonly rejected = false) { super(message); }
}

interface Entry {
  identifier: string; userId: string; version: number;
  observedOrAttempted: boolean;
  rejected?: DeviceRegistrationFailure;
  stage: DeviceStage;
  controller?: AbortController;
  flight?: Promise<BrowserRegistration>;
  listeners: Set<(stage: DeviceStage) => void>;
}
const entries = new Map<string, Entry>();
const markerKeys = new Set<string>();
subscribeAuthSession(() => {
  entries.forEach((entry) => entry.controller?.abort());
  entries.clear();
  // Markers prevent replay within this login, but confer no ready status and
  // must not carry account-specific registration state into the next login.
  try {
    for (let index = sessionStorage.length - 1; index >= 0; index--) {
      const key = sessionStorage.key(index);
      if (key?.startsWith('saiv_device_registration_v1:')) sessionStorage.removeItem(key);
    }
    markerKeys.forEach((key) => sessionStorage.removeItem(key));
  } catch { /* Memory state above is still invalidated. */ }
  markerKeys.clear();
});

function assertCurrent(entry: Entry): void {
  if (!isCurrentSession(entry.version)) throw new CanceledError('The signed-in account changed.');
}
function marker(entry: Entry): string { return `saiv_device_registration_v1:${entry.userId}:${entry.identifier}`; }
function remember(entry: Entry): void {
  entry.observedOrAttempted = true;
  markerKeys.add(marker(entry));
  // Contains no token, browser characteristics, images or device response.
  try { sessionStorage.setItem(marker(entry), 'observed-or-attempted'); } catch { /* Memory fallback for this page. */ }
}
function entryFor(user: RegistrationUser, identifier: string): Entry {
  if (!user?.id || user.role !== 'student' || user.is_active !== true) throw new DeviceRegistrationFailure('Browser registration requires an active student account.', true);
  const version = getSessionVersion();
  if (!isCurrentSession(version)) throw new CanceledError('Please sign in again.');
  const key = `${version}:${user.id}:${identifier}`;
  let entry = entries.get(key);
  if (!entry) {
    entry = { identifier, userId: user.id, version, stage: 'checking', observedOrAttempted: false, listeners: new Set() };
    try {
      entry.observedOrAttempted = sessionStorage.getItem(marker(entry)) !== null;
      if (entry.observedOrAttempted) markerKeys.add(marker(entry));
    } catch { /* Memory fallback. */ }
    entries.set(key, entry);
  }
  return entry;
}

function refuse(entry: Entry, message: string): never {
  remember(entry);
  entry.rejected = new DeviceRegistrationFailure(message, true);
  throw entry.rejected;
}
function confirm(entry: Entry, device: RegisteredDevice): BrowserRegistration {
  assertCurrent(entry);
  if (device.user_id !== entry.userId || device.device_fingerprint !== entry.identifier) {
    throw new DeviceRegistrationFailure('The server returned inconsistent device ownership. Contact your instructor.');
  }
  remember(entry);
  if (!device.is_active) refuse(entry, 'This browser registration is disabled or revoked. Contact your instructor; it will not be re-registered automatically.');
  return { identifier: entry.identifier, userId: entry.userId, version: entry.version, device };
}

async function run(entry: Entry, lookupOnly: boolean): Promise<BrowserRegistration> {
  assertCurrent(entry);
  if (entry.rejected) throw entry.rejected;
  const controller = new AbortController(); entry.controller = controller;
  entry.stage = 'checking'; entry.listeners.forEach((listener) => listener('checking'));
  try {
    const devices = await getMyDevices(controller.signal);
    assertCurrent(entry);
    // Never treat a malformed/scoped-wrong list as proof the identifier is new.
    if (devices.some((device) => device.user_id !== entry.userId)) {
      throw new DeviceRegistrationFailure('Device ownership could not be verified. Contact your instructor.');
    }
    const matches = devices.filter((device) => device.device_fingerprint === entry.identifier);
    if (matches.some((device) => !device.is_active)) {
      refuse(entry, 'This browser registration is disabled or revoked. Contact your instructor; it will not be re-registered automatically.');
    }
    if (matches.length > 1) throw new DeviceRegistrationFailure('Device registration data is inconsistent. Contact your instructor.');
    if (matches[0]) return confirm(entry, matches[0]);
    if (lookupOnly || entry.observedOrAttempted) {
      throw new DeviceRegistrationFailure('Registration is not confirmed. A previous registration may still be processing or unavailable. Check status again or contact your instructor; no registration request will be replayed.');
    }
    // Mark before dispatch: a timeout or unmount must not lead to a replay that
    // could reactivate a device revoked after the first registration committed.
    remember(entry);
    entry.stage = 'registering'; entry.listeners.forEach((listener) => listener('registering'));
    return confirm(entry, await registerBrowserDevice(entry.identifier, controller.signal));
  } catch (error) {
    assertCurrent(entry);
    const failure = parseApiError(error);
    if (failure.kind === 'cancelled' || failure.status === 401) throw error;
    if (failure.status === 403 || failure.status === 409) {
      refuse(entry, failure.status === 409 ? 'This browser is registered to another account. Contact your instructor; the identifier will not be replaced to bypass this restriction.'
        : 'Device registration was refused. Contact your instructor.');
    }
    if (error instanceof DeviceRegistrationFailure) throw error;
    // Do not reflect an identifier echoed by an error response into the UI.
    throw new DeviceRegistrationFailure(failure.message.includes(entry.identifier)
      ? 'Device registration failed. Check registration status or contact your instructor.' : failure.message);
  }
}

function coordinate(entry: Entry, lookupOnly: boolean, onStage?: (stage: DeviceStage) => void): Promise<BrowserRegistration> {
  if (onStage) { entry.listeners.add(onStage); onStage(entry.stage); }
  if (!entry.flight) {
    const promise = run(entry, lookupOnly);
    entry.flight = promise;
    void promise.finally(() => { if (entry.flight === promise) entry.flight = undefined; }).catch(() => {});
  }
  return entry.flight.finally(() => { if (onStage) entry.listeners.delete(onStage); });
}

/** Lazy preflight on opening check-in, never on ordinary dashboard renders. */
export function prepareBrowserRegistration(user: RegistrationUser, onStage?: (stage: DeviceStage) => void): Promise<BrowserRegistration> {
  try {
    if (!user?.id || user.role !== 'student' || user.is_active !== true || !isCurrentSession(getSessionVersion())) {
      throw new DeviceRegistrationFailure('Browser registration requires an active student account. Sign in again.', true);
    }
    return coordinate(entryFor(user, getBrowserDeviceIdentifier()), false, onStage);
  }
  catch (error) { return Promise.reject(error); }
}

/** Fresh read-only check immediately before attendance submission; never POST. */
export function confirmBrowserRegistration(user: RegistrationUser, identifier: string): Promise<BrowserRegistration> {
  try { return coordinate(entryFor(user, identifier), true); }
  catch (error) { return Promise.reject(error); }
}
