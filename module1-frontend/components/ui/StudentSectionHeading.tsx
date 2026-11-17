import type { ReactNode } from 'react';
import StudentIcon from './StudentIcon';
import type { StudentIconName } from './StudentIcon';

export default function StudentSectionHeading({ id, title, description, icon, children }: {
  id: string; title: string; description: string; icon: StudentIconName; children?: ReactNode;
}) {
  return <div className="section-heading student-section-heading">
    <div className="section-heading-copy"><StudentIcon name={icon} /><div><h2 id={id} className="section-title">{title}</h2><p>{description}</p></div></div>
    {children}
  </div>;
}

export function RefreshButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return <button type="button" className="refresh-button" aria-label={label} disabled={disabled} onClick={onClick}><StudentIcon name="refresh" />Refresh</button>;
}
