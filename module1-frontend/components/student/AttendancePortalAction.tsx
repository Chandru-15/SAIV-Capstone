'use client';

import { useEffect, useState } from 'react';
import { getPublicConfiguration } from '../../lib/client-config';
import { safePortalDestination } from '../../lib/portal-navigation';

export default function AttendancePortalAction() {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let mounted = true;
    void getPublicConfiguration().then(config => {
      const safe = safePortalDestination(config.studentPortalUrl, window.location?.origin, config.studentPortalLocalOnly === true);
      if (mounted) setUrl(safe);
    }).catch(() => { if (mounted) setUrl(null); });
    return () => { mounted = false; };
  }, []);
  return <div className="min-w-0">
    {url ? <a className="btn-secondary" href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Attendance & Appeals <span aria-hidden="true">↗</span></a>
      : <button type="button" className="btn-secondary" disabled>Attendance & Appeals</button>}
  </div>;
}
