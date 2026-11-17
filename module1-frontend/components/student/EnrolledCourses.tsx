import type { StudentCourse } from '../../types/api';
import StudentSectionHeading from '../ui/StudentSectionHeading';
import { EmptyState } from '../ui/Feedback';

export default function EnrolledCourses({ courses, loading, error }: { courses: StudentCourse[] | null; loading: boolean; error: string }) {
  return <section className="panel" aria-labelledby="courses-title">
    <StudentSectionHeading id="courses-title" title="Enrolled Courses" description="Your current courses." icon="courses" />
    {loading ? <p role="status" className="muted">Loading enrolled courses…</p> : error || courses === null ? <p className="notice notice-error" role="alert">Course information is unavailable. Use Refresh sessions to try again.</p>
      : !courses.length ? <EmptyState>No enrolled courses were returned. Contact course staff if you expect to be enrolled.</EmptyState>
      : <ul className="enrolled-courses">{courses.map(course => <li className="course-row" key={course.id}><p className="course-code">{course.code}</p><h3 className="font-semibold">{course.name}</h3></li>)}</ul>}
  </section>;
}
