import type { User } from '../types/api';

export type FrontendAccess = 'student' | 'staff' | 'denied';

export function isValidatedProfile(value: unknown): value is User {
  if (!value || typeof value !== 'object') return false;
  const profile = value as Record<string, unknown>;
  return typeof profile.id === 'string' && profile.id.length > 0 &&
    typeof profile.email === 'string' && profile.email.length > 0 &&
    typeof profile.full_name === 'string' &&
    typeof profile.role === 'string' && profile.is_active === true;
}

export function frontendAccess(role: string): FrontendAccess {
  if (role === 'student') return 'student';
  if (['ta', 'instructor', 'admin'].includes(role)) return 'staff';
  return 'denied';
}
