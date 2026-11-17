import { api } from './api';
import { ClientDataError } from './api-error';
import type { RegisteredDevice } from '../types/api';

function isDevice(value: unknown): value is RegisteredDevice {
  if (!value || typeof value !== 'object') return false;
  const item = value as RegisteredDevice;
  return typeof item.id === 'string' && !!item.id && typeof item.user_id === 'string' && !!item.user_id &&
    typeof item.device_fingerprint === 'string' && item.device_fingerprint.length >= 8 && item.device_fingerprint.length <= 64 &&
    typeof item.is_active === 'boolean' && typeof item.is_trusted === 'boolean' && typeof item.trust_score === 'string' &&
    (item.browser_key_id == null || (typeof item.browser_key_id === 'string' && /^[A-Za-z0-9_-]{43}$/.test(item.browser_key_id))) &&
    (item.browser_key_usable == null || typeof item.browser_key_usable === 'boolean') &&
    Number.isInteger(item.total_checkins) && item.total_checkins >= 0 &&
    typeof item.first_seen_at === 'string' && typeof item.last_seen_at === 'string';
}

export async function getMyDevices(signal?: AbortSignal): Promise<RegisteredDevice[]> {
  const response = await api.get<unknown>('/devices/my-devices', { signal });
  if (!Array.isArray(response.data) || !response.data.every(isDevice)) {
    throw new ClientDataError('Device registration status could not be read. Check registration again.');
  }
  return response.data;
}

export async function registerBrowserDevice(identifier: string, signal?: AbortSignal): Promise<RegisteredDevice> {
  const response = await api.post<unknown>('/devices/', { device_fingerprint: identifier, platform: 'web' }, { signal });
  if (!isDevice(response.data)) throw new ClientDataError('Device registration could not be confirmed. Check registration status before trying again.');
  return response.data;
}

export interface BrowserChallenge {
  challenge_id: string;
  message: string;
  expires_at: string;
}

function browserChallenge(data: unknown): BrowserChallenge {
  const value = data as Partial<BrowserChallenge> | null;
  if (!value || typeof value.challenge_id !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value.challenge_id) ||
      typeof value.message !== 'string' || !value.message || value.message.length > 2048 ||
      typeof value.expires_at !== 'string' || !Number.isFinite(Date.parse(value.expires_at))) {
    throw new ClientDataError('The server did not return a usable browser challenge.');
  }
  return value as BrowserChallenge;
}

export async function requestBrowserEnrollment(deviceId: string, publicKey: string, recoveryId: string | undefined, signal: AbortSignal) {
  const response = await api.post<unknown>('/devices/browser-key/challenge', {
    device_id: deviceId, public_key: publicKey, recovery_device_id: recoveryId,
  }, { signal });
  return browserChallenge(response.data);
}

export async function completeBrowserEnrollment(challengeId: string, publicKey: string, signature: string,
  password: string, signal: AbortSignal): Promise<RegisteredDevice> {
  const response = await api.post<unknown>('/devices/browser-key/enroll', {
    challenge_id: challengeId, public_key: publicKey, signature, password,
  }, { signal, saiv: { noReplay: true } });
  if (!isDevice(response.data)) throw new ClientDataError('Check whether browser enrollment finished before trying again.');
  return response.data;
}

export async function requestBrowserCheckinChallenge(sessionId: string, identifier: string, signal: AbortSignal) {
  const response = await api.post<unknown>('/checkins/browser-challenge', {
    session_id: sessionId, device_fingerprint: identifier,
  }, { signal });
  return browserChallenge(response.data);
}
