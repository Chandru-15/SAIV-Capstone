import { api } from './api';
import { ClientDataError } from './api-error';
import { validateChallenge } from './liveness';

import type {
  ActiveSession,
  CheckinHistoryItem,
  CheckInCreateRequest,
  CheckInResponse,
  FaceEnrollResponse,
  FaceReEnrollRequestRecord,
  PageResponse,
  StudentCourse,
  ActiveLivenessChallenge,
} from '../types/api';

function isSession(value: unknown): value is ActiveSession {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === 'string' && typeof item.course_id === 'string' && typeof item.name === 'string' &&
    typeof item.status === 'string';
}

export async function getActiveSessions(signal?: AbortSignal): Promise<ActiveSession[]> {
  const response = await api.get<unknown>('/sessions/active', { signal });
  if (!Array.isArray(response.data) || !response.data.every(isSession)) throw new ClientDataError('Session data is unavailable. Please refresh.');
  return response.data;
}

export async function getMyCourses(signal?: AbortSignal): Promise<StudentCourse[]> {
  const response = await api.get<unknown>('/courses/me', { signal });
  if (!Array.isArray(response.data) || !response.data.every((course: unknown) => {
    if (!course || typeof course !== 'object') return false;
    const item = course as Record<string, unknown>;
    return typeof item.id === 'string' && typeof item.code === 'string' && typeof item.name === 'string';
  })) throw new ClientDataError('Course enrollment and verification policy could not be loaded. Please refresh.');
  // Missing policy booleans remain unknown; deriveFacePolicy fails closed.
  return response.data as StudentCourse[];
}

export async function getSession(sessionId: string, signal?: AbortSignal): Promise<ActiveSession> {
  const response = await api.get<unknown>(`/sessions/${encodeURIComponent(sessionId)}`, { signal });
  if (!isSession(response.data) || response.data.id !== sessionId) throw new ClientDataError('Session details are unavailable.');
  return response.data;
}

export async function getMyCheckins(
  options: { limit?: number; offset?: number; signal?: AbortSignal } = {}
): Promise<PageResponse<CheckinHistoryItem>> {
  const { limit = 50, offset = 0, signal } = options;
  const response = await api.get<PageResponse<CheckinHistoryItem>>('/checkins/me', { params: { limit, offset }, signal });
  const page = response.data;
  if (!page || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0 ||
      !Number.isInteger(page.limit) || page.limit < 1 || page.limit > 100 || page.offset !== offset ||
      page.items.length > page.limit || !page.items.every((item) => item && typeof item.id === 'string' &&
        typeof item.session_id === 'string' && typeof item.status === 'string' && typeof item.checked_in_at === 'string')) {
    throw new ClientDataError('Attendance history returned invalid data. Please refresh.');
  }
  if (!page.items.length && offset < page.total) throw new ClientDataError('Attendance history changed while loading. Please refresh.');
  return page;
}

export async function enrollFace(
  image: string
): Promise<FaceEnrollResponse> {
  const response = await api.post<FaceEnrollResponse>(
    '/users/me/face/enroll',
    { image },
    { timeout: 30000 }
  );
  return response.data;
}

// Replaces an existing template. A PUT rather than a second POST to /enroll,
// so the backend can refuse it when nothing is enrolled yet and can record it
// as a re-enrolment. The old template keeps working if this fails.
//
// Carries no password and no approval id. Authority is an approved
// face_reenroll_requests row that an instructor or admin granted, which the
// server looks up for the CALLER and spends here; a 403 means there is none.
// Sending an id would let a client name somebody else's approval.
export async function reenrollFace(image: string): Promise<FaceEnrollResponse> {
  const response = await api.put<FaceEnrollResponse>(
    '/users/me/face',
    { image },
    { timeout: 30000 }
  );
  return response.data;
}

// Asks an instructor for permission to replace the template. Deliberately
// sends no photo: the capture happens only once this has been approved, so
// nothing biometric is held while the request waits for a decision.
export async function requestFaceReenrollment(
  reason?: string
): Promise<FaceReEnrollRequestRecord> {
  const trimmed = (reason ?? '').trim();
  const response = await api.post<FaceReEnrollRequestRecord>(
    '/users/me/face/reenroll-requests',
    trimmed ? { reason: trimmed } : {}
  );
  return response.data;
}

// The caller's own recent requests, newest first, so the portal can show
// whether one is waiting, usable, or was turned down and why.
export async function myFaceReenrollRequests(): Promise<FaceReEnrollRequestRecord[]> {
  const response = await api.get<PageResponse<FaceReEnrollRequestRecord>>(
    '/users/me/face/reenroll-requests',
    { params: { limit: 5 } }
  );
  return Array.isArray(response.data?.items) ? response.data.items : [];
}

export async function submitCheckin(
  request: CheckInCreateRequest,
  proof?: { body: string; challengeId: string; signature: string },
): Promise<CheckInResponse> {
  const response = await api.post<CheckInResponse>(
    '/checkins/',
    proof?.body ?? request,
    proof ? {
      timeout: 45000,
      transformRequest: [(body) => body],
      headers: { 'X-SAIV-Browser-Challenge': proof.challengeId, 'X-SAIV-Browser-Signature': proof.signature },
      saiv: { noReplay: true },
    } : { timeout: 45000 }
  );
  if (!isCheckinResult(response.data) || response.data.session_id !== request.session_id) {
    throw new ClientDataError('The submission response could not be confirmed. Check attendance status before trying again.');
  }
  if (proof && response.data.browser_key_verified !== true) {
    throw new ClientDataError('The server did not confirm browser verification. Check attendance status before trying again.');
  }
  return response.data;
}

function isCheckinResult(value: unknown): value is CheckInResponse {
  if (!value || typeof value !== 'object') return false;
  const item = value as CheckInResponse;
  return typeof item.id === 'string' && typeof item.session_id === 'string' &&
    ['approved', 'pending', 'flagged', 'rejected', 'appealed'].includes(item.status) &&
    Number.isFinite(item.risk_score) && typeof item.qr_code_verified === 'boolean';
}

export async function requestLivenessChallenge(sessionId: string, signal?: AbortSignal): Promise<ActiveLivenessChallenge> {
  const response = await api.post<unknown>('/checkins/liveness-challenge', null, {
    params: { session_id: sessionId }, signal,
  });
  return validateChallenge(response.data);
}

/** Reconcile an uncertain POST using only this student's history, including older pages. */
export async function findSessionCheckin(sessionId: string, signal?: AbortSignal): Promise<CheckInResponse | null> {
  let offset = 0;
  let total: number | undefined;
  for (let pageNumber = 0; pageNumber < 20; pageNumber++) {
    const page = await getMyCheckins({ offset, limit: 100, signal });
    const existing = page.items.find((item) => item.session_id === sessionId);
    if (existing) {
      if (!isCheckinResult(existing)) throw new ClientDataError('The recorded outcome is unavailable. Refresh attendance history.');
      return existing;
    }
    if (total !== undefined && total !== page.total) throw new ClientDataError('Attendance history changed. Check the submission status again.');
    total = page.total;
    offset += page.items.length;
    if (offset >= total) return null;
  }
  throw new ClientDataError('The complete attendance history could not be checked. Check with your instructor before trying again.');
}
