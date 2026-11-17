import type { ReactNode } from 'react';
import type { CheckinStatus } from '../../types/api';
import StudentIcon from './StudentIcon';

export function StatusBadge({ status, showIcon = true }: { status: CheckinStatus; showIcon?: boolean }) {
  return <span className={`tag tag-${status}`}>{showIcon && <StudentIcon name={status === 'approved' ? 'check' : status === 'rejected' ? 'close' : status === 'flagged' ? 'info' : 'history'} />}<span className="capitalize">{status}</span></span>;
}

export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div role={error ? 'alert' : 'status'} className={`notice ${error ? 'notice-error' : ''}`}>{children}</div>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}
