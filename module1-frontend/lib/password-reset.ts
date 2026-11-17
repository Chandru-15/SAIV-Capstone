import { api } from './api';
import { ClientDataError, parseApiError } from './api-error';

// Existing Module 2 UserService policy, not client-side security enforcement.
// tests/flow/test_password_recovery_security_flow.py checks these against Python.
export const RESET_CODE_SECONDS = 600;
export const RESET_RESEND_SECONDS = 60;
export const RESET_CODE_LENGTH = 6;

export const RESET_REQUEST_NOTICE = 'If this email belongs to an eligible account, a verification code has been requested. Check your inbox and spam folder.';

function assertMessage(value: unknown): void {
  if (!value || typeof value !== 'object' || !('message' in value) || typeof value.message !== 'string') {
    throw new ClientDataError('The response could not be confirmed. Check your email or try signing in before repeating this request.');
  }
}

export async function requestPasswordCode(email: string, signal?: AbortSignal): Promise<void> {
  const response = await api.post<unknown>('/auth/forgot-password', { email: email.trim().toLowerCase() }, { signal });
  assertMessage(response.data); // Never reflect response text or any supplied secret.
}

export async function resetPassword(email: string, code: string, newPassword: string, signal?: AbortSignal): Promise<void> {
  const response = await api.post<unknown>('/auth/reset-password', {
    email: email.trim().toLowerCase(), code, new_password: newPassword,
  }, { signal });
  assertMessage(response.data);
}

export function resetPasswordValidation(code: string, password: string, confirmation: string): string | null {
  if (!/^[0-9]{6}$/.test(code)) return 'Enter the six-digit verification code.';
  if (Array.from(password).length < 8) return 'Use a password with at least 8 characters.';
  if (new TextEncoder().encode(password).length > 72) return 'Use a password no longer than 72 UTF-8 bytes (some characters use more than one byte).';
  if (password !== confirmation) return 'The passwords do not match.';
  return null;
}

export function recoveryFailure(error: unknown, resetting: boolean): { message: string; cancelled: boolean; retryAfter?: number } {
  const parsed = parseApiError(error);
  if (parsed.kind === 'cancelled') return { message: '', cancelled: true };
  if (parsed.kind === 'timeout') return { cancelled: false, message: resetting
    ? 'The request timed out. Your password may have changed. Try signing in with the new password before requesting another code.'
    : 'The request timed out. Check your inbox before requesting another code.' };
  if (parsed.kind === 'network') return { cancelled: false, message: resetting
    ? 'The connection was interrupted. Try signing in with the new password before repeating a reset.'
    : 'Unable to reach the server. Check your connection before trying again.' };
  if (parsed.status === 429) return { cancelled: false, retryAfter: parsed.retryAfterSeconds,
    message: 'Too many requests. Please wait before trying again.' };
  if (resetting && [400, 422].includes(parsed.status ?? 0)) return { cancelled: false,
    message: 'The code is invalid, expired or already used. Check the latest email, or request a new code.' };
  // Stable messages only: never display arbitrary validation input/password/OTP.
  return { cancelled: false, message: 'Unable to complete the request. Check your details and try again later.' };
}
