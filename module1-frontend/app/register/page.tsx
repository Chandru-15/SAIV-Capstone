'use client';

import AuthFrame from '../../components/ui/AuthFrame';

import { FormEvent, useState } from 'react';
import { useRouter } from 'next/navigation';
import { parseApiError } from '../../lib/api-error';

import { registerUser } from '../../lib/auth-api';
import type { UserRole } from '../../types/api';

export default function RegisterPage() {
  const router = useRouter();

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [role, setRole] = useState<UserRole | ''>('');

  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setError('');

    if (password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    if (!role) {
      setError('Please select your role.');
      return;
    }

    setIsLoading(true);

    try {
      await registerUser({
        email,
        password,
        full_name: fullName,
        role,
      });

      router.push('/login');
    } catch (error) {
      const failure = parseApiError(error);
      if (failure.kind !== 'cancelled') setError(failure.message);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <AuthFrame>
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900">
            Create account
          </h1>

          <p className="mt-2 text-gray-600">
            Register for SAIV attendance
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-5"
        >
          <div>
            <label
              htmlFor="fullName"
              className="block text-sm font-medium text-gray-700"
            >
              Full name
            </label>

            <input
              id="fullName"
              type="text"
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              required
              autoComplete="name"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 outline-none focus:border-blue-500"
              placeholder="Your full name"
            />
          </div>

          <div>
            <label
              htmlFor="email"
              className="block text-sm font-medium text-gray-700"
            >
              Email
            </label>

            <input
              id="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoComplete="email"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 outline-none focus:border-blue-500"
              placeholder="student@e.ntu.edu.sg"
            />
          </div>

          <div>
            <label
              htmlFor="role"
              className="block text-sm font-medium text-gray-700"
            >
              Role
            </label>

            <select
              id="role"
              value={role}
              onChange={(event) => setRole(event.target.value as UserRole | '')}
              required
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 outline-none focus:border-blue-500"
            >
              <option value="">Select your role</option>
              <option value="student">Student</option>
              <option value="ta">Teaching Assistant</option>
              <option value="instructor">Instructor</option>
              <option value="admin">Admin</option>
            </select>
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-gray-700"
            >
              Password
            </label>

            <div className="relative mt-1">
              <input
                id="password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 pr-16 text-gray-900 outline-none focus:border-blue-500"
                placeholder="Minimum 8 characters"
              />

              <button
                type="button"
                onKeyDown={(event) => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); setShowPassword(true); } }}
                onKeyUp={(event) => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); setShowPassword(false); } }}
                onPointerDown={() => setShowPassword(true)}
                onPointerUp={() => setShowPassword(false)}
                onPointerLeave={() => setShowPassword(false)}
                onPointerCancel={() => setShowPassword(false)}
                onBlur={() => setShowPassword(false)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-sm font-medium text-gray-500 hover:text-gray-700"
                aria-label="Press and hold to show password"
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          <div>
            <label
              htmlFor="confirmPassword"
              className="block text-sm font-medium text-gray-700"
            >
              Confirm password
            </label>

            <div className="relative mt-1">
              <input
                id="confirmPassword"
                type={showConfirmPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(event) =>
                  setConfirmPassword(event.target.value)
                }
                required
                minLength={8}
                autoComplete="new-password"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 pr-16 text-gray-900 outline-none focus:border-blue-500"
                placeholder="Re-enter your password"
              />

              <button
                type="button"
                onKeyDown={(event) => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); setShowConfirmPassword(true); } }}
                onKeyUp={(event) => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); setShowConfirmPassword(false); } }}
                onPointerDown={() => setShowConfirmPassword(true)}
                onPointerUp={() => setShowConfirmPassword(false)}
                onPointerLeave={() => setShowConfirmPassword(false)}
                onPointerCancel={() => setShowConfirmPassword(false)}
                onBlur={() => setShowConfirmPassword(false)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-sm font-medium text-gray-500 hover:text-gray-700"
                aria-label="Press and hold to show confirm password"
              >
                {showConfirmPassword ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {error && (
            <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isLoading}
            className="w-full rounded-lg bg-blue-600 px-4 py-2.5 font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isLoading ? 'Creating account...' : 'Create account'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-gray-600">
          Already have an account?{' '}
          <a
            href="/login"
            className="font-medium text-blue-600 hover:underline"
          >
            Sign in
          </a>
        </p>
      </AuthFrame>
  );
}