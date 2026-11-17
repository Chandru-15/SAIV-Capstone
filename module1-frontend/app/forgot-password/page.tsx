'use client';

import AuthFrame from '../../components/ui/AuthFrame';

import Link from 'next/link';
import { FormEvent, useEffect, useRef, useState } from 'react';
import { requestPasswordCode, resetPassword, resetPasswordValidation, recoveryFailure,
  RESET_CODE_SECONDS, RESET_RESEND_SECONDS, RESET_REQUEST_NOTICE } from '../../lib/password-reset';

export default function ForgotPasswordPage() {
  const [stage, setStage] = useState<'email' | 'reset' | 'done'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [show, setShow] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false);
  const [requestedAt, setRequestedAt] = useState(0);
  const [resendAt, setResendAt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const mounted = useRef(true);
  const busy = useRef(false);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    mounted.current = true;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => { mounted.current = false; operation.current?.abort(); clearInterval(timer); };
  }, []);
  const cooldown = Math.max(0, Math.ceil((resendAt - now) / 1000));
  const inputClass = 'w-full rounded-lg border border-gray-300 px-4 py-3 text-base text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100';

  async function send(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (busy.current || Date.now() < resendAt) return;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { setError('Enter a valid email address.'); return; }
    busy.current = true; setLoading(true); setError(''); setNotice('');
    const controller = new AbortController(); operation.current = controller;
    try {
      await requestPasswordCode(email, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      const timestamp = Date.now(); setNow(timestamp); setRequestedAt(timestamp);
      setResendAt(timestamp + RESET_RESEND_SECONDS * 1000);
      setEmail(email.trim().toLowerCase()); setCode(''); setStage('reset'); setNotice(RESET_REQUEST_NOTICE);
    } catch (failure) {
      if (!mounted.current) return;
      const parsed = recoveryFailure(failure, false);
      if (!parsed.cancelled) setError(parsed.message);
      if (parsed.retryAfter) setResendAt(Date.now() + Math.max(RESET_RESEND_SECONDS, parsed.retryAfter) * 1000);
    } finally { if (mounted.current) { busy.current = false; setLoading(false); } }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy.current) return;
    const validation = resetPasswordValidation(code, password, confirmation);
    if (validation) { setError(validation); return; }
    busy.current = true; setLoading(true); setError(''); setNotice('');
    const controller = new AbortController(); operation.current = controller;
    try {
      await resetPassword(email, code, password, controller.signal);
      if (mounted.current && !controller.signal.aborted) setStage('done');
    } catch (failure) {
      if (!mounted.current) return;
      const parsed = recoveryFailure(failure, true);
      if (!parsed.cancelled) setError(parsed.message);
    } finally {
      if (mounted.current) {
        setCode(''); setPassword(''); setConfirmation(''); setShow(false);
        busy.current = false; setLoading(false);
      }
    }
  }

  function changeEmail() {
    if (busy.current) return;
    setStage('email'); setCode(''); setPassword(''); setConfirmation(''); setShow(false);
    setError(''); setNotice(''); setRequestedAt(0); setResendAt(0);
  }

  return <AuthFrame>
      <p className="font-bold text-blue-700">SAIV</p>
      <h1 className="mt-2 text-2xl font-bold text-gray-900">{stage === 'done' ? 'Password changed' : 'Reset your password'}</h1>
      {stage !== 'done' && <p className="mt-2 text-sm text-gray-600">Use the email for your SAIV student or staff account.</p>}
      {error && <p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-4 rounded-lg bg-blue-50 p-3 text-sm text-blue-900">{notice}</p>}
      {stage === 'email' && <form onSubmit={send} className="mt-6 space-y-4" aria-busy={loading}>
        <div><label htmlFor="recovery-email" className="mb-2 block text-sm font-medium">Email</label>
          <input id="recovery-email" type="email" autoComplete="email" autoCapitalize="none" spellCheck={false}
            required maxLength={254} value={email} disabled={loading} onChange={event => setEmail(event.target.value)} className={inputClass} /></div>
        <button type="submit" disabled={loading || cooldown > 0} className="min-h-12 w-full rounded-lg bg-blue-600 px-4 py-3 font-semibold text-white disabled:opacity-50">
          {loading ? 'Requesting code…' : 'Send Verification Code'}</button>
        {cooldown > 0 && <p role="timer" className="text-sm">Please wait {cooldown} seconds before another request.</p>}
      </form>}
      {stage === 'reset' && <>
        <p className="mt-5 break-words text-sm text-gray-700">Email: {email}</p>
        <p id="code-help" className="mt-2 text-sm text-gray-600">Codes expire {RESET_CODE_SECONDS / 60} minutes after issue. Enter the latest code and your new password together; the server verifies both.</p>
        {requestedAt > 0 && now >= requestedAt + RESET_CODE_SECONDS * 1000 && <p role="status" className="mt-2 text-sm text-amber-900">This request is over 10 minutes old. You may need a new code; the server checks actual expiry.</p>}
        <form onSubmit={submit} className="mt-5 space-y-4" aria-busy={loading}>
          <div><label htmlFor="reset-code" className="mb-2 block text-sm font-medium">Verification code</label>
            <input id="reset-code" type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" required maxLength={6}
              aria-describedby="code-help" disabled={loading} value={code} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))} className={inputClass} /></div>
          <p id="password-help" className="text-sm text-gray-600">At least 8 characters and no more than 72 UTF-8 bytes. Some characters use multiple bytes.</p>
          <div><label htmlFor="new-password" className="mb-2 block text-sm font-medium">New password</label>
            <input id="new-password" type={show ? 'text' : 'password'} autoComplete="new-password" required maxLength={128}
              aria-describedby="password-help" disabled={loading} value={password} onChange={event => setPassword(event.target.value)} className={inputClass} /></div>
          <div><label htmlFor="confirm-password" className="mb-2 block text-sm font-medium">Confirm new password</label>
            <input id="confirm-password" type={show ? 'text' : 'password'} autoComplete="new-password" required maxLength={128}
              disabled={loading} value={confirmation} onChange={event => setConfirmation(event.target.value)} className={inputClass} /></div>
          <button type="button" aria-pressed={show} disabled={loading} onClick={() => setShow(!show)} className="min-h-11 text-sm font-medium text-blue-700 underline">{show ? 'Hide passwords' : 'Show passwords'}</button>
          <button type="submit" disabled={loading} className="min-h-12 w-full rounded-lg bg-blue-600 px-4 py-3 font-semibold text-white disabled:opacity-50">{loading ? 'Changing password…' : 'Change password'}</button>
        </form>
        <button type="button" disabled={loading || cooldown > 0} onClick={() => void send()} className="mt-4 min-h-11 w-full rounded-lg border border-blue-600 px-4 py-3 font-medium text-blue-700 disabled:opacity-50">{cooldown > 0 ? `Resend Code in ${cooldown}s` : 'Resend Code'}</button>
        <button type="button" disabled={loading} onClick={changeEmail} className="mt-2 min-h-11 text-sm text-blue-700 underline">Use a different email</button>
        <p className="mt-3 text-xs text-gray-600">Delivery and resends are subject to server limits. If email does not arrive, check spam or contact your administrator.</p>
      </>}
      {stage === 'done' && <p role="status" className="mt-5 text-gray-700">Your password has been changed. Sign in with your new password. You have not been signed in automatically.</p>}
      <Link href="/login" className="mt-6 inline-block min-h-11 py-3 font-medium text-blue-700 underline">{stage === 'done' ? 'Return to Login' : 'Back to Login'}</Link>
    </AuthFrame>;
}
