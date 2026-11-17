import { resolvePublicConfiguration } from '../../../lib/public-config';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  try {
    // Passing the environment object avoids build-time NEXT_PUBLIC_* inlining.
    return Response.json(resolvePublicConfiguration(process.env), {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch {
    // Invalid environment values may accidentally contain credentials; never echo them.
    return Response.json({ message: 'Invalid application configuration.' }, {
      status: 503,
      headers: { 'Cache-Control': 'no-store' },
    });
  }
}
