import { redirect } from 'next/navigation';

// Emit an HTTP redirect on each request instead of prerendering a redirect payload.
export const dynamic = 'force-dynamic';

export default function Home() {
  // The dashboard gate validates the backend profile and selects student/staff access.
  // Session storage is browser-only; the server must not infer authentication here.
  redirect('/dashboard');
}
