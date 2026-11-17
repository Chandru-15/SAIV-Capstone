import type { ReactNode } from 'react';

export default function AuthFrame({ children }: { children: ReactNode }) {
  return <main className="auth-layout">
    <aside className="auth-story" aria-label="About SAIV">
      <div className="text-xl font-bold tracking-tight">SAIV <span className="ml-2 text-sm font-normal text-teal-100">Secure Attendance & Identity Verification</span></div>
      <div className="max-w-md"><p className="text-xs font-semibold uppercase tracking-[.2em] text-teal-200">Your university. Your presence.</p><h2 className="mt-5 text-5xl font-semibold leading-tight tracking-tight">Be present.<br />Be recognised.</h2><p className="mt-6 text-lg leading-relaxed text-slate-200">A considered approach to attendance, with identity verification and privacy at every step.</p></div>
      <p className="text-sm text-slate-300">NTU · SC3099 Capstone Project</p>
    </aside>
    <div className="auth-form"><div className="auth-card"><p className="eyebrow mb-8">SAIV / Student access</p>{children}<p className="mt-8 border-t pt-5 text-xs leading-relaxed muted">Secure Attendance & Identity Verification<br />Use your own account and browser for attendance.</p></div></div>
  </main>;
}
