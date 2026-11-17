"""Course structure and tutorial / lab group allocation.

A course is lecture-only, or also has tutorials and/or labs (set by the admin
when the course is created). For a course with tutorials or labs, the admin
allocates the enrolled students into groups in one of two ways:

    group_count     "split into 8 tutorials"   -> 8 groups, sizes differ by at most 1
    max_per_group   "at most 30 per tutorial"  -> ceil(students / 30) groups,
                                                  balanced, none over 30

Allocation is random (shuffled) but even. ``mode="add_new"`` places only
students who are not in a group yet - for late enrolments - into the
emptiest groups that still have room, opening a new group if all are full.

A tutorial / lab session is tied to one group; only that group's students
can see it, check in to it, or appear on its register.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

from sqlalchemy import func, select

from ..models import (
    GROUP_KINDS,
    Course,
    CourseGroup,
    CourseSettings,
    Enrollment,
    GroupMember,
    SessionGroup,
    User,
    UserRole,
)
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import NotFoundError, PermissionDeniedError, ValidationError
from .staff_scope import can_view_course
from .user_service import UserService

_PREFIX = {"tutorial": "T", "lab": "L"}


def group_name(kind: str, index: int) -> str:
    return f"{_PREFIX[kind]}{index:02d}"


def split_evenly(items: list, groups: int) -> List[list]:
    """Round-robin: sizes differ by at most one."""
    buckets: List[list] = [[] for _ in range(groups)]
    for i, item in enumerate(items):
        buckets[i % groups].append(item)
    return buckets


def groups_needed(students: int, max_per_group: int) -> int:
    return max(1, math.ceil(students / max_per_group))


class GroupService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.audit = AuditService(session)

    # -- course structure ---------------------------------------------------

    def _course(self, course_id: str) -> Course:
        course = self.session.get(Course, course_id)
        if course is None:
            raise NotFoundError(f"No course with id {course_id}")
        return course

    def set_structure(self, actor: User, course_id: str, has_tutorials: bool,
                      has_labs: bool) -> Course:
        """Record whether the course has tutorials and/or labs. Joins the
        caller's transaction (CourseService.create) or commits on its own."""
        UserService.require_role(actor, UserRole.ADMIN)
        course = self._course(course_id)
        settings = course.settings
        if settings is None:
            settings = CourseSettings(course_id=course_id)
            course.settings = settings
        settings.has_tutorials = bool(has_tutorials)
        settings.has_labs = bool(has_labs)
        self.session.flush()
        self.audit.record("course_structure_updated", user_id=actor.id, resource_type="course",
                          resource_id=course_id,
                          details={"has_tutorials": settings.has_tutorials,
                                   "has_labs": settings.has_labs})
        return course

    @transactional
    def update_structure(self, actor: User, course_id: str, has_tutorials: bool,
                         has_labs: bool) -> Course:
        return self.set_structure(actor, course_id, has_tutorials, has_labs)

    # -- reads --------------------------------------------------------------

    def _enrolled_ids(self, course_id: str) -> List[str]:
        return list(self.session.scalars(
            select(Enrollment.student_id).where(Enrollment.course_id == course_id,
                                                Enrollment.is_active.is_(True))
            .order_by(Enrollment.student_id)))

    def _groups(self, course_id: str, kind: str) -> List[CourseGroup]:
        return list(self.session.scalars(
            select(CourseGroup).where(CourseGroup.course_id == course_id,
                                      CourseGroup.kind == kind)
            .order_by(CourseGroup.name)))

    def overview(self, actor: User, course_id: str) -> Dict[str, object]:
        """Groups with their members, plus who is not in a group yet."""
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
        course = self._course(course_id)
        if not can_view_course(self.session, actor, course_id):
            raise PermissionDeniedError("This course is not one of yours")
        enrolled = set(self._enrolled_ids(course_id))
        people = {u.id: u for u in self.session.scalars(
            select(User).where(User.id.in_(enrolled)))} if enrolled else {}
        out: Dict[str, object] = {
            "course_id": course_id,
            "has_tutorials": course.has_tutorials,
            "has_labs": course.has_labs,
            "enrolled": len(enrolled),
        }
        for kind in GROUP_KINDS:
            groups = self._groups(course_id, kind)
            placed = set()
            rows = []
            for g in groups:
                members = [m.student_id for m in g.members]
                placed.update(members)
                rows.append({
                    "id": g.id, "name": g.name, "kind": kind, "capacity": g.capacity,
                    "size": len(members),
                    "members": [_person(people.get(sid), sid) for sid in members],
                })
            out[f"{kind}_groups"] = rows
            out[f"{kind}_unallocated"] = [_person(people.get(sid), sid)
                                          for sid in sorted(enrolled - placed)]
        return out

    # -- allocation ---------------------------------------------------------

    @transactional
    def allocate(self, actor: User, course_id: str, kind: str, *,
                 group_count: Optional[int] = None, max_per_group: Optional[int] = None,
                 mode: str = "reshuffle", seed: Optional[int] = None) -> Dict[str, object]:
        UserService.require_role(actor, UserRole.ADMIN)
        course = self._course(course_id)
        if kind not in GROUP_KINDS:
            raise ValidationError("kind must be 'tutorial' or 'lab'")
        if not (course.has_tutorials if kind == "tutorial" else course.has_labs):
            raise ValidationError(f"{course.code} is not set up to have {kind}s")
        if mode not in ("reshuffle", "add_new"):
            raise ValidationError("mode must be 'reshuffle' or 'add_new'")
        for label, value in (("group_count", group_count), ("max_per_group", max_per_group)):
            if value is not None and value < 1:
                raise ValidationError(f"{label} must be at least 1")

        students = self._enrolled_ids(course_id)
        rng = random.Random(seed)
        rng.shuffle(students)
        groups = self._groups(course_id, kind)

        if mode == "reshuffle":
            if (group_count is None) == (max_per_group is None):
                raise ValidationError("Give either the number of groups or the maximum per group")
            if not students:
                raise ValidationError("Nobody is enrolled in this course yet")
            count = group_count or groups_needed(len(students), max_per_group)
            self.session.query(GroupMember).filter(
                GroupMember.course_id == course_id, GroupMember.kind == kind
            ).delete(synchronize_session=False)
            self.session.expire_all()
            groups = self._groups(course_id, kind)
            groups = self._ensure_groups(course_id, kind, groups, count, max_per_group)
            self.session.flush()
            for group, bucket in zip(groups, split_evenly(students, count)):
                for sid in bucket:
                    self.session.add(GroupMember(group_id=group.id, student_id=sid,
                                                 course_id=course_id, kind=kind))
            placed = len(students)
        else:
            placed_ids = set(self.session.scalars(
                select(GroupMember.student_id).where(GroupMember.course_id == course_id,
                                                     GroupMember.kind == kind)))
            waiting = [s for s in students if s not in placed_ids]
            sizes = {g.id: len(g.members) for g in groups}
            limit = max_per_group or next((g.capacity for g in groups if g.capacity), None)
            if not groups and not limit:
                raise ValidationError("No groups exist yet; allocate everyone first")
            for sid in waiting:
                open_groups = [g for g in groups if not limit or sizes[g.id] < limit]
                if not open_groups:
                    new = CourseGroup(course_id=course_id, kind=kind,
                                      name=self._next_name(kind, groups), capacity=limit)
                    self.session.add(new)
                    self.session.flush()
                    groups.append(new)
                    sizes[new.id] = 0
                    open_groups = [new]
                target = min(open_groups, key=lambda g: (sizes[g.id], g.name))
                self.session.add(GroupMember(group_id=target.id, student_id=sid,
                                             course_id=course_id, kind=kind))
                sizes[target.id] += 1
            placed = len(waiting)

        self.session.flush()
        self.session.expire_all()
        self.audit.record("groups_allocated", user_id=actor.id, resource_type="course",
                          resource_id=course_id,
                          details={"kind": kind, "mode": mode, "placed": placed,
                                   "group_count": group_count, "max_per_group": max_per_group})
        return self.overview(actor, course_id)

    def _ensure_groups(self, course_id: str, kind: str, existing: List[CourseGroup],
                       count: int, capacity: Optional[int]) -> List[CourseGroup]:
        """Exactly ``count`` groups named T01..Tnn. Surplus groups are removed
        unless a session already uses them (then they are kept, empty)."""
        by_name = {g.name: g for g in existing}
        wanted = [group_name(kind, i) for i in range(1, count + 1)]
        result = []
        for name in wanted:
            group = by_name.pop(name, None)
            if group is None:
                group = CourseGroup(course_id=course_id, kind=kind, name=name)
                self.session.add(group)
            group.capacity = capacity
            result.append(group)
        for leftover in by_name.values():
            in_use = self.session.scalar(
                select(func.count()).select_from(SessionGroup)
                .where(SessionGroup.group_id == leftover.id))
            if not in_use:
                self.session.delete(leftover)
        return result

    @staticmethod
    def _next_name(kind: str, groups: List[CourseGroup]) -> str:
        taken = {g.name for g in groups}
        i = 1
        while group_name(kind, i) in taken:
            i += 1
        return group_name(kind, i)

    @transactional
    def move(self, actor: User, course_id: str, student_id: str, group_id: str) -> Dict[str, object]:
        """Put one student into a specific group (manual fix-ups)."""
        UserService.require_role(actor, UserRole.ADMIN)
        group = self.session.get(CourseGroup, group_id)
        if group is None or group.course_id != course_id:
            raise NotFoundError("No such group on this course")
        enrolled = self.session.scalar(select(Enrollment.id).where(
            Enrollment.course_id == course_id, Enrollment.student_id == student_id,
            Enrollment.is_active.is_(True)))
        if enrolled is None:
            raise ValidationError("That student is not enrolled in this course")
        existing = self.session.scalar(select(GroupMember).where(
            GroupMember.course_id == course_id, GroupMember.kind == group.kind,
            GroupMember.student_id == student_id))
        if existing is None:
            self.session.add(GroupMember(group_id=group.id, student_id=student_id,
                                         course_id=course_id, kind=group.kind))
        else:
            existing.group_id = group.id
        self.session.flush()
        self.session.expire_all()
        self.audit.record("group_member_moved", user_id=actor.id, resource_type="course",
                          resource_id=course_id,
                          details={"student_id": student_id, "group": group.name})
        return self.overview(actor, course_id)


def _person(user: Optional[User], fallback_id: str) -> Dict[str, str]:
    if user is None:
        return {"id": fallback_id, "email": "", "full_name": ""}
    return {"id": user.id, "email": user.email, "full_name": user.full_name}
