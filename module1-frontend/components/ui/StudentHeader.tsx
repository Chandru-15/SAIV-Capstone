'use client';

import Link from 'next/link';
import StudentIcon from './StudentIcon';

export function Brand() {
  return <Link href="/dashboard" className="brand" aria-label="SAIV Student Dashboard">
    <span className="brand-symbol" aria-hidden="true"><svg width="40" height="40" viewBox="0 0 40 40" fill="none"><path d="M20 4 33 9v10c0 8-13 16-13 16S7 27 7 19V9L20 4Z" fill="#eef7ff" stroke="#0864c2" strokeWidth="2.2" strokeLinejoin="round"/><path d="m13.5 19.5 4.5 4.5 9-10" stroke="#0864c2" strokeWidth="2.7" strokeLinecap="round" strokeLinejoin="round"/></svg></span>
    <span><span className="brand-name">SAIV</span><span className="brand-description">Student attendance</span></span>
  </Link>;
}

export default function StudentHeader({ current, onSignOut }: { current: 'dashboard' | 'profile'; onSignOut: () => void }) {
  return <>
    <a href="#main-content" className="sr-only focus:not-sr-only focus:block focus:p-4">Skip to content</a>
    <header className="student-header">
      <div className="page-width header-inner">
        <Brand />
        <nav className="student-nav" aria-label="Student navigation">
          <Link href="/dashboard" className="btn btn-quiet" aria-current={current === 'dashboard' ? 'page' : undefined}>Dashboard</Link>
          <Link href="/profile" className="btn btn-quiet" aria-current={current === 'profile' ? 'page' : undefined}>Profile & Settings</Link>
          <button type="button" className="btn-secondary" aria-label="Sign out" onClick={onSignOut}><StudentIcon name="logout" />Sign out</button>
        </nav>
      </div>
    </header>
  </>;
}
