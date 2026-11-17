import type { ReactNode } from 'react';

const paths = {
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2M14 15h2M8 18h2"/></>,
  courses: <><path d="m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5"/></>,
  history: <><circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/></>,
  user: <><circle cx="12" cy="7" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/></>,
  camera: <><path d="m8 5 1-2h6l1 2h4v15H4V5h4Z"/><circle cx="12" cy="12" r="4"/></>,
  location: <><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></>,
  face: <><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5M8 9h.01M16 9h.01M8 14c2 3 6 3 8 0"/></>,
  browser: <><rect x="4" y="3" width="16" height="13" rx="1.5"/><path d="m4 16-2 5h20l-2-5H4ZM10 18h4"/></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
  refresh: <><path d="M20 7V3l-3 3a8 8 0 0 0-13 5M4 17v4l3-3a8 8 0 0 0 13-5M20 3v5h-5M4 21v-5h5"/></>,
  logout: <><path d="M10 4H4v16h6M14 8l4 4-4 4M8 12h12"/></>,
  arrow: <path d="m9 5 7 7-7 7"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  activity: <path d="M2 12h5l3-8 4 16 3-8h5"/>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
} satisfies Record<string, ReactNode>;

export type StudentIconName = keyof typeof paths;
export default function StudentIcon({ name, className = '' }: { name: StudentIconName; className?: string }) {
  return <svg className={`student-icon ${className}`} width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
