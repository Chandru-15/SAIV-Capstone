import { api, isTokenResponse, recoverAccessToken } from './api';
import { CanceledError } from 'axios';
import { ClientDataError } from './api-error';
import {
  clearAuthSession,
  saveTokens,
  saveUser,
  getAccessToken,
  getRefreshToken,
  getSessionVersion,
  isCurrentSession,
} from './auth';

import type {
  ConsentUpdateRequest,
  LoginRequest,
  LoginResponse,
  RefreshTokenResponse,
  RegisterRequest,
  User,
} from '../types/api';

export async function registerUser(
  data: RegisterRequest
): Promise<User> {
  const version = getSessionVersion();
  const response = await api.post<User>(
    '/auth/register',
    data
  );

  if (version !== getSessionVersion()) throw new CanceledError('Registration was superseded by a session change.');
  return response.data;
}

export async function loginUser(
  data: LoginRequest
): Promise<LoginResponse> {
  clearAuthSession();
  const version = getSessionVersion();
  const response = await api.post<LoginResponse>(
    '/auth/login',
    data
  );

  if (version !== getSessionVersion()) throw new CanceledError('Sign-in was superseded.');
  if (!isTokenResponse(response.data) || !response.data.user) {
    throw new ClientDataError('Invalid sign-in response. Please try again.');
  }

  saveTokens(
    response.data.access_token,
    response.data.refresh_token
  );

  saveUser(response.data.user);

  return response.data;
}

export async function refreshAccessToken(
  refreshToken: string
): Promise<RefreshTokenResponse> {
  if (refreshToken !== getRefreshToken()) throw new CanceledError('Session changed.');
  return recoverAccessToken();
}

export async function getCurrentUser(signal?: AbortSignal): Promise<User> {
  const version = getSessionVersion();
  const response =
    await api.get<User>('/auth/me', { signal });

  if (!signal?.aborted && isCurrentSession(version)) saveUser(response.data);

  return response.data;
}

export async function updateConsent(
  data: ConsentUpdateRequest
): Promise<User> {
  const version = getSessionVersion();
  const response = await api.patch<User>('/auth/me', data);

  if (isCurrentSession(version)) saveUser(response.data);

  return response.data;
}

export function logoutUser(): Promise<void> {
  const token = getAccessToken();
  // Clear synchronously so late refresh/login/profile responses cannot resurrect this identity.
  clearAuthSession();
  if (!token) return Promise.resolve();
  return api.post('/auth/logout', undefined, {
    headers: { Authorization: `Bearer ${token}` },
    timeout: 5000,
    saiv: { detachedLogout: true },
  }).then(() => undefined, () => undefined);
}
