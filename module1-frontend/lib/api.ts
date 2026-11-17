import axios, { AxiosError, CanceledError } from 'axios';
import type { AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios';
import { clearAuthSession, getAccessToken, getRefreshToken, getSessionVersion, isCurrentSession, rotateTokens } from './auth';
import { getPublicConfiguration } from './client-config';
import { normalizeApiPath } from './public-config';
import { ClientDataError, SignedRequestRefreshError } from './api-error';
import type { RefreshTokenResponse } from '../types/api';

interface RequestSession {
  version?: number;
  token?: string | null;
  retried?: boolean;
  detachedLogout?: boolean;
  noReplay?: boolean;
  transport?: AxiosRequestConfig['adapter'];
}
declare module 'axios' {
  interface AxiosRequestConfig { saiv?: RequestSession; }
}

const publicPaths = new Set(['/auth/login', '/auth/register', '/auth/refresh',
  '/auth/forgot-password', '/auth/reset-password']);
let refreshFlight: { version: number; promise: Promise<RefreshTokenResponse> } | undefined;

export const api = axios.create({
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 10000,
});

api.interceptors.request.use((config) => {
  config.url = normalizeApiPath(config.url ?? '/');
  const anonymous = publicPaths.has(config.url);
  const transport = config.saiv?.transport ?? config.adapter;
  const accessToken = getAccessToken();
  config.saiv = {
    ...config.saiv, transport,
    version: anonymous || config.saiv?.detachedLogout ? undefined : getSessionVersion(),
    token: accessToken,
  };
  if (!anonymous && !config.saiv.detachedLogout && accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  } else if (anonymous) {
    delete config.headers.Authorization;
  }
  // Capture identity synchronously, before runtime configuration or transport awaits.
  config.adapter = async (request) => {
    let configuration;
    try {
      configuration = await getPublicConfiguration();
    } catch (error) {
      assertCurrent(request);
      // The shared configuration fetch has its own deadline. That AbortError is
      // a timeout, not a user cancellation that should silently leave loading UI.
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new AxiosError('Application configuration timed out.', 'ETIMEDOUT', request);
      }
      throw error;
    }
    assertCurrent(request);
    request.baseURL = configuration.apiUrl;
    return axios.getAdapter(transport)(request);
  };
  return config;
}, (error: unknown) => { throw error; }, { synchronous: true });

function assertCurrent(config: InternalAxiosRequestConfig): void {
  if (config.signal?.aborted || (config.saiv?.version !== undefined &&
      !isCurrentSession(config.saiv.version))) throw new CanceledError('Session changed or request cancelled.');
}

export function isTokenResponse(value: unknown): value is RefreshTokenResponse {
  if (!value || typeof value !== 'object') return false;
  const tokens = value as Record<string, unknown>;
  return typeof tokens.access_token === 'string' && !!tokens.access_token.trim() &&
    typeof tokens.refresh_token === 'string' && !!tokens.refresh_token.trim() && tokens.token_type === 'bearer';
}

export function recoverAccessToken(version = getSessionVersion()): Promise<RefreshTokenResponse> {
  if (refreshFlight?.version === version) return refreshFlight.promise;
  const refreshToken = getRefreshToken();
  const flight = {
    version,
    promise: (async () => {
      try {
        if (!isCurrentSession(version) || !refreshToken?.trim()) throw new ClientDataError('Please sign in again.');
        const response = await api.post<unknown>('/auth/refresh', { refresh_token: refreshToken });
        if (!isCurrentSession(version)) throw new CanceledError('Session changed.');
        if (!isTokenResponse(response.data)) throw new ClientDataError('Invalid authentication response. Please sign in again.');
        if (!rotateTokens(version, response.data.access_token, response.data.refresh_token)) throw new CanceledError('Session changed.');
        return response.data;
      } catch (error) {
        if (isCurrentSession(version)) clearAuthSession();
        throw error;
      }
    })(),
  };
  refreshFlight = flight;
  void flight.promise.finally(() => { if (refreshFlight === flight) refreshFlight = undefined; }).catch(() => {});
  return flight.promise;
}

api.interceptors.response.use((response) => {
  assertCurrent(response.config);
  return response;
}, async (error: unknown) => {
  if (!axios.isAxiosError(error) || !error.config || axios.isCancel(error)) throw error;
  const config = error.config;
  assertCurrent(config);
  if (error.response?.status === 403 && config.url === '/auth/me' && config.method === 'get') {
    clearAuthSession();
    throw error;
  }
  if (error.response?.status !== 401 || publicPaths.has(config.url ?? '') || config.saiv?.detachedLogout) throw error;
  const version = config.saiv?.version;
  if (version === undefined || !config.saiv?.token) throw error;
  if (config.saiv.retried) {
    if (isCurrentSession(version)) clearAuthSession();
    throw error;
  }
  // A late 401 for the old access token can reuse an already-completed rotation.
  if (getAccessToken() === config.saiv.token) await recoverAccessToken(version);
  assertCurrent(config);
  if (config.saiv.noReplay) {
    throw new SignedRequestRefreshError('Your session was refreshed. Start a fresh attempt.');
  }
  return api.request({ ...config, saiv: { ...config.saiv, retried: true } });
});
