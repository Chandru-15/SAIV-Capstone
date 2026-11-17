'use client';

import type { CheckinHistoryItem } from '../../types/api';
import { StatusBadge, EmptyState, Notice } from '../ui/Feedback';
import StudentSectionHeading, { RefreshButton } from '../ui/StudentSectionHeading';
import { formatDateTime } from '../../lib/student-policy';

export default function AttendanceHistory({ items, total, complete, loading, error, labels, onLoadMore, onRefresh }: {
  items: CheckinHistoryItem[]; total: number; complete: boolean; loading: boolean; error: string;
  labels: Record<string, string>; onLoadMore: () => void; onRefresh: () => void;
}) {
  return (
    <section className="panel" aria-labelledby="history-title">
      <StudentSectionHeading id="history-title" title="Recent Attendance" description={`Showing ${items.length} of ${total} attendance records.`} icon="history">
        <RefreshButton label="Refresh history" onClick={onRefresh} disabled={loading} />
      </StudentSectionHeading>
      {loading && <p role="status" className="mt-4 text-sm text-gray-500">Loading attendance history...</p>}
      {error && <Notice error>{error}</Notice>}
      {!loading && !error && !items.length && <EmptyState>No attendance records. Your recorded check-ins will appear here.</EmptyState>}
      <div className="attendance-rows">
        {items.map((item) => (
          <article key={item.id} className="history-row">
            <h3>{labels[item.session_id] ?? 'Attendance session (loading details)'}</h3>
            <p>{formatDateTime(item.checked_in_at)}</p>
            <StatusBadge status={item.status} showIcon={item.status !== 'approved' && item.status !== 'rejected'} />
          </article>
        ))}
      </div>
      {!complete && items.length > 0 && (
        <button type="button" onClick={onLoadMore} disabled={loading}
          className="btn-secondary mt-5">Load more attendance</button>
      )}
    </section>
  );
}
