'use client';

import AuthFrame from '../../components/ui/AuthFrame';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { parseApiError } from '../../lib/api-error';
import { getAuthCleanupWarning, subscribeAuthSession } from '../../lib/auth';

import { loginUser } from '../../lib/auth-api';

export default function LoginPage() {
  const router = useRouter();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] =
    useState(false);

  const [error, setError] = useState('');
  const [cleanupWarning, setCleanupWarning] = useState(getAuthCleanupWarning);
  const [isLoading, setIsLoading] =
    useState(false);

  useEffect(() => subscribeAuthSession(() => setCleanupWarning(getAuthCleanupWarning())), []);

  function getApiErrorMessage(error: unknown): string {
    const failure = parseApiError(error);
    if (failure.status === 401) return 'Invalid email or password.';
    return failure.message;
  }

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    setError('');

    const cleanEmail = email.trim();

    if (!cleanEmail || !password) {
      setError(
        'Please enter your email and password.'
      );
      return;
    }

    const emailPattern =
      /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    if (!emailPattern.test(cleanEmail)) {
      setError(
        'Please enter a valid email address.'
      );
      return;
    }

    setIsLoading(true);

    try {
      await loginUser({
        email: cleanEmail,
        password,
      });

      router.push('/dashboard');
    } catch (caughtError) {
      setError(
        getApiErrorMessage(
          caughtError
        )
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <AuthFrame>
        <div>
          <h1 className="text-3xl font-bold text-gray-900">
            SAIV
          </h1>

          <p className="mt-2 text-lg text-gray-600">
            Sign in to your student account
          </p>
        </div>

        {cleanupWarning && (
          <p role="alert" className="mt-5 rounded-lg bg-amber-50 p-4 text-sm text-amber-900">
            {cleanupWarning}
          </p>
        )}

        <form
          onSubmit={handleSubmit}
          className="mt-8 space-y-5"
        >
          <div>
            <label
              htmlFor="email"
              className="mb-2 block text-sm font-medium text-gray-900"
            >
              Email
            </label>

            <input
              id="email"
              type="email"
              value={email}
              onChange={(event) =>
                setEmail(
                  event.target
                    .value
                )
              }
              autoComplete="email"
              placeholder="student@e.ntu.edu.sg"
              className="w-full rounded-lg border border-gray-300 px-4 py-3 text-gray-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="mb-2 block text-sm font-medium text-gray-900"
            >
              Password
            </label>

            <div className="relative">
              <input
                id="password"
                type={
                  showPassword
                    ? 'text'
                    : 'password'
                }
                value={password}
                onChange={(
                  event
                ) =>
                  setPassword(
                    event
                      .target
                      .value
                  )
                }
                autoComplete="current-password"
                className="w-full rounded-lg border border-gray-300 px-4 py-3 pr-20 text-gray-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
              />

              <button
                type="button"
                aria-label="Press and hold to show password"
                onKeyDown={(event) => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); setShowPassword(true); } }}
                onKeyUp={(event) => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); setShowPassword(false); } }}
                onBlur={() => setShowPassword(false)}
                onTouchCancel={() => setShowPassword(false)}
                onMouseDown={() =>
                  setShowPassword(
                    true
                  )
                }
                onMouseUp={() =>
                  setShowPassword(
                    false
                  )
                }
                onMouseLeave={() =>
                  setShowPassword(
                    false
                  )
                }
                onTouchStart={() =>
                  setShowPassword(
                    true
                  )
                }
                onTouchEnd={() =>
                  setShowPassword(
                    false
                  )
                }
                className="absolute right-4 top-1/2 -translate-y-1/2 text-sm font-medium text-gray-500 hover:text-gray-700"
              >
                {showPassword
                  ? 'Hide'
                  : 'Show'}
              </button>
            </div>
            <Link href="/forgot-password" className="mt-2 inline-block text-sm font-medium text-blue-700 underline hover:text-blue-900">
              Forgot Password?
            </Link>
          </div>

          {error && (
            <div role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isLoading}
            className="w-full rounded-lg bg-blue-600 px-4 py-3 font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isLoading
              ? 'Signing in...'
              : 'Sign in'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-gray-600">
          Don&apos;t have an account?{' '}
          <Link
            href="/register"
            className="font-medium text-blue-600 hover:text-blue-700"
          >
            Register
          </Link>
        </p>
      </AuthFrame>
  );
}
