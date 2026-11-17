"""Wipe the SAIV database and fill it with realistic demo data.

Standard library only. Builds SQL and pipes it into the Postgres container:

    python seed_demo.py

Every account's password is password123. Re-run it right before a demo:
all timestamps are relative to the moment it runs, so "today" and the live
session are always current.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

PASSWORD = "password123"
PASSWORD_HASH = "$2a$10$poBgu5avlZVfFdG.1oJ0FuKQDMW1zquyKIzTJ/NwuCBycOwknemDi"
SEMESTER = "AY2026-27 S1"
SGT = timedelta(hours=8)
HISTORY_DAYS = 20
RETENTION_DAYS = 30

rng = random.Random(3099)
NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
TODAY_SGT = (NOW + SGT).date()

STUDENT_NAMES = [
    "Aisha Rahman", "Benjamin Tan", "Chloe Lim", "Danish Ong", "Elena Wong",
    "Farhan Ismail", "Gabrielle Ng", "Hafiz Rahman", "Isabelle Koh", "Jonas Lee",
    "Keisha Pillai", "Liam Chong", "Maya Suresh", "Nathan Goh", "Olivia Chen",
    "Pranav Menon", "Qiuyue Lin", "Ryan Teo", "Sofia Abdullah", "Taufiq Hassan",
    "Uma Sharma", "Victor Yap", "Wren Tan", "Xin Yi Low", "Yusuf Ahmad",
    "Zara De Souza", "Aaron Phua", "Bella Chua", "Caleb Sim", "Diana Ho",
    "Ethan Pang", "Fiona Kwok", "Gavin Seah", "Hannah Bose", "Ian Wee",
    "Jasmine Rao", "Kai Wen", "Lina Mustaffa", "Marcus Toh", "Nadia Fong",
]

INSTRUCTORS = [
    ("Dr Tan Wei Ming", "tan.weiming@saiv.edu"),
    ("Dr Priya Nair", "priya.nair@saiv.edu"),
    ("Prof Lim Hock Seng", "lim.hockseng@saiv.edu"),
]

TAS = [
    ("Jeremy Chua", "jeremy.chua@saiv.edu"),
    ("Siti Nurhaliza", "siti.nurhaliza@saiv.edu"),
    ("Kevin Ong", "kevin.ong@saiv.edu"),
]

VENUES = {
    "LT1": ("NTU LT1", 1.3449, 103.6812),
    "HIVE": ("The Hive LHN", 1.3434, 103.6827),
    "NS4": ("North Spine NS4", 1.3471, 103.6806),
    "SS2": ("South Spine SS2", 1.3423, 103.6797),
    "ARC": ("The Arc LT", 1.3446, 103.6879),
    "WAVE": ("Wave Hall", 1.3438, 103.6864),
    "SWLAB": ("Software Lab 1, N4", 1.3440, 103.6797),
}

OFF_CAMPUS = [
    ("Jurong East MRT", 1.3334, 103.7422),
    ("Orchard Road", 1.3048, 103.8318),
    ("Punggol Waterway", 1.4053, 103.9024),
]

COURSES = [
    dict(code="SC2001", name="Algorithm Design and Analysis", venue="LT1", instructor=0,
         attendance=1.0, weekday=0, hour=9, tutorials=True, face=False,
         description="Divide and conquer, dynamic programming, graph algorithms."),
    dict(code="SC2002", name="Object Oriented Design and Programming", venue="HIVE", instructor=1,
         attendance=0.97, weekday=1, hour=10, labs=True, face=False,
         description="OO principles, UML and design patterns in Java."),
    dict(code="SC2005", name="Operating Systems", venue="NS4", instructor=2,
         attendance=0.86, weekday=2, hour=14, face=False,
         description="Processes, scheduling, memory management and file systems."),
    dict(code="SC2008", name="Computer Network", venue="SS2", instructor=0,
         attendance=0.74, weekday=3, hour=11, face=False,
         description="Layered architecture, TCP/IP, routing and network security."),
    dict(code="SC3000", name="Artificial Intelligence", venue="ARC", instructor=1,
         attendance=0.93, weekday=4, hour=13, face=False,
         description="Search, knowledge representation, reasoning and learning."),
    dict(code="SC4001", name="Neural Networks and Deep Learning", venue="WAVE", instructor=2,
         attendance=0.55, weekday=2, hour=16, face=True,
         description="Perceptrons, CNNs, RNNs and modern deep learning practice."),
]

CAMPUS_IPS = [f"155.69.{a}.{b}" for a in (12, 24, 160, 183) for b in (14, 37, 88, 102, 211)]
USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Safari/604.1",
    "Mozilla/5.0 (Linux; Android 15; Pixel 8) Chrome/130.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) Chrome/130.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Edg/130.0 Safari/537.36",
]

ROWS: dict = {}
COLUMNS: dict = {}


def uid() -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def hexhash() -> str:
    return hashlib.sha256(uid().encode()).hexdigest()


def add(table: str, **row) -> dict:
    ROWS.setdefault(table, []).append(row)
    COLUMNS.setdefault(table, list(row.keys()))
    return row


def sgt_to_utc(day, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute) - SGT


def lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(round(value, 6)) if isinstance(value, float) else str(value)
    if isinstance(value, datetime):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    return "'" + str(value).replace("'", "''") + "'"


def metres(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def audit(action, at, user_id=None, resource_type=None, resource_id=None, details=None,
          success=True, ip=None, device_id=None):
    add("audit_logs", id=uid(), user_id=user_id, action=action, resource_type=resource_type,
        resource_id=resource_id, ip_address=ip or rng.choice(CAMPUS_IPS),
        user_agent=rng.choice(USER_AGENTS), device_id=device_id,
        details=json.dumps(details) if details is not None else None,
        success=success, timestamp=at)


def build() -> dict:
    created = NOW - timedelta(days=HISTORY_DAYS + 8)
    people = {}

    admin = add("users", id=uid(), email="admin@saiv.edu", full_name="System Admin",
                hashed_password=PASSWORD_HASH, role="admin", is_active=True,
                camera_consent=False, geolocation_consent=False, face_embedding_hash=None,
                face_enrolled=False, created_at=created, updated_at=created,
                last_login_at=NOW - timedelta(minutes=12), scheduled_deletion_at=None)
    people["admin"] = admin

    def staff(name, email, role):
        row = add("users", id=uid(), email=email, full_name=name, hashed_password=PASSWORD_HASH,
                  role=role, is_active=True, camera_consent=False, geolocation_consent=False,
                  face_embedding_hash=None, face_enrolled=False,
                  created_at=created + timedelta(hours=1), updated_at=created + timedelta(hours=1),
                  last_login_at=NOW - timedelta(hours=rng.randint(1, 30)), scheduled_deletion_at=None)
        audit("user_created", created + timedelta(hours=1), admin["id"], "user", row["id"],
              {"email": email, "role": role})
        return row

    instructors = [staff(n, e, "instructor") for n, e in INSTRUCTORS]
    tas = [staff(n, e, "ta") for n, e in TAS]

    students = []
    for i, name in enumerate(STUDENT_NAMES):
        email = name.lower().replace(" ", ".") + "@e.saiv.edu"
        enrolled_face = i % 10 != 9
        row = add("users", id=uid(), email=email, full_name=name, hashed_password=PASSWORD_HASH,
                  role="student", is_active=True, camera_consent=True, geolocation_consent=True,
                  face_embedding_hash=hexhash() if enrolled_face else None,
                  face_enrolled=enrolled_face, created_at=created + timedelta(hours=2),
                  updated_at=created + timedelta(hours=2),
                  last_login_at=NOW - timedelta(hours=rng.randint(2, 70)), scheduled_deletion_at=None)
        row["_reliability"] = rng.choice([1.0] * 8 + [0.92] * 2 + [0.6, 0.4])
        students.append(row)
        audit("user_created", created + timedelta(hours=2), admin["id"], "user", row["id"],
              {"email": email, "role": "student"})
        if enrolled_face:
            audit("face_enrolled", created + timedelta(days=1, minutes=i), row["id"], "user", row["id"])

    inactive = students[-1]
    inactive["is_active"] = False
    inactive["_reliability"] = 0.0
    audit("user_deactivated", NOW - timedelta(days=3), admin["id"], "user", inactive["id"],
          {"email": inactive["email"], "reason": "Withdrew from the programme"})

    devices = {}
    risky_devices = set()
    for i, s in enumerate(students):
        platform = ["ios", "android", "web"][i % 3]
        emulator = i in (7, 23)
        rooted = i == 31
        device = add("devices", id=uid(), user_id=s["id"], device_fingerprint=hexhash(),
                     device_name={"ios": "iPhone 15", "android": "Pixel 8", "web": "Chrome on MacBook"}[platform],
                     platform=platform, browser="Safari" if platform == "ios" else "Chrome",
                     os_version={"ios": "18.1", "android": "15", "web": "macOS 14.6"}[platform],
                     app_version="1.4.2", public_key=f"demo-public-key-{i:02d}",
                     public_key_created_at=created + timedelta(days=1),
                     public_key_expires_at=created + timedelta(days=366),
                     attestation_passed=not (emulator or rooted), last_attestation_at=NOW - timedelta(days=1),
                     attestation_token=None, is_trusted=not (emulator or rooted),
                     trust_score="low" if (emulator or rooted) else ("high" if i % 4 else "medium"),
                     is_emulator=emulator, is_rooted_jailbroken=rooted,
                     first_seen_at=created + timedelta(days=1), last_seen_at=NOW - timedelta(hours=3),
                     total_checkins=0, is_active=True, revoked_at=None, revocation_reason=None)
        devices[s["id"]] = device
        if emulator or rooted:
            risky_devices.add(s["id"])
        audit("device_registered", created + timedelta(days=1, minutes=i), s["id"], "device", device["id"],
              {"platform": platform})

    courses = []
    pool = students[:-1]
    for c in COURSES:
        venue_name, lat, lon = VENUES[c["venue"]]
        row = add("courses", id=uid(), code=c["code"], name=c["name"], description=c["description"],
                  semester=SEMESTER, is_active=True, venue_latitude=lat, venue_longitude=lon,
                  venue_name=venue_name, geofence_radius_meters=100.0,
                  require_face_recognition=c["face"], require_device_binding=True, risk_threshold=0.5,
                  created_at=created, updated_at=created)
        row["_spec"] = c
        add("course_settings", course_id=row["id"], has_tutorials=bool(c.get("tutorials")),
            has_labs=bool(c.get("labs")))
        lecturer = instructors[c["instructor"]]
        add("course_instructors", id=uid(), course_id=row["id"], instructor_id=lecturer["id"],
            assigned_by_id=admin["id"], assigned_at=created + timedelta(hours=3))
        audit("instructor_assigned", created + timedelta(hours=3), admin["id"], "course", row["id"],
              {"instructor_id": lecturer["id"]})

        size = rng.randint(18, 26)
        roster = rng.sample(pool, size)
        if c["code"] == "SC2005":
            roster.append(inactive)
        row["_roster"] = roster
        for s in roster:
            enrolment = add("enrollments", id=uid(), student_id=s["id"], course_id=row["id"],
                            is_active=True, enrolled_at=created + timedelta(hours=4), dropped_at=None)
            audit("enrollment_added", created + timedelta(hours=4), admin["id"], "enrollment",
                  enrolment["id"],
                  {"course_id": row["id"], "student_id": s["id"]})

        row["_groups"] = []
        kind = "tutorial" if c.get("tutorials") else ("lab" if c.get("labs") else None)
        if kind:
            half = (len(roster) + 1) // 2
            for g, members in enumerate((roster[:half], roster[half:]), start=1):
                name = ("T" if kind == "tutorial" else "L") + str(g)
                group = add("course_groups", id=uid(), course_id=row["id"], kind=kind, name=name,
                            capacity=half, created_at=created + timedelta(hours=5))
                group["_members"] = members
                for s in members:
                    add("group_members", id=uid(), group_id=group["id"], student_id=s["id"],
                        course_id=row["id"], kind=kind)
                row["_groups"].append(group)
        courses.append(row)

    by_code = {c["code"]: c for c in courses}
    ta_for = {"SC2001": tas[0], "SC2002": tas[1], "SC3000": tas[2], "SC2005": tas[2]}
    flagged_rows = []

    def session(course, name, kind, start, minutes, status, group=None, members=None):
        spec = course["_spec"]
        lecturer = instructors[spec["instructor"]]
        end = start + timedelta(minutes=minutes)
        s = add("sessions", id=uid(), course_id=course["id"], instructor_id=lecturer["id"], name=name,
                session_type=kind, description=None, scheduled_start=start, scheduled_end=end,
                checkin_opens_at=start - timedelta(minutes=15), checkin_closes_at=start + timedelta(minutes=30),
                status=status,
                actual_start=start if status in ("active", "closed") else None,
                actual_end=end if status == "closed" else None,
                venue_latitude=None, venue_longitude=None, venue_name=None, geofence_radius_meters=None,
                require_liveness_check=True, require_face_match=spec["face"], risk_threshold=None,
                qr_code_secret=None, qr_code_expires_at=None,
                created_at=start - timedelta(days=7), updated_at=start)
        audit("session_created", min(start - timedelta(days=7), NOW - timedelta(days=1)),
              lecturer["id"], "session", s["id"], {"course_id": course["id"], "name": name})
        if group is not None:
            add("session_groups", session_id=s["id"], group_id=group["id"])
        ta = ta_for.get(spec["code"])
        if ta is not None:
            add("session_tas", id=uid(), session_id=s["id"], ta_id=ta["id"], assigned_by_id=lecturer["id"],
                assigned_at=s["created_at"])
        s["_course"] = course
        s["_roster"] = members if members is not None else course["_roster"]
        return s

    def check_ins(sess, share=1.0, until=None):
        course = sess["_course"]
        spec = course["_spec"]
        start = sess["scheduled_start"]
        lecturer = instructors[spec["instructor"]]
        for s in sess["_roster"]:
            if not s["is_active"] and start > NOW - timedelta(days=3):
                continue
            chance = spec["attendance"] * s["_reliability"] * share
            if rng.random() > chance:
                continue
            at = start + timedelta(minutes=rng.uniform(-12, 18))
            if until is not None and at > until:
                at = until - timedelta(minutes=rng.uniform(0.5, 12))
            if at > NOW:
                continue
            roll = rng.random()
            signals = []
            if s["id"] in risky_devices:
                signals.append(("device_emulator" if devices[s["id"]]["is_emulator"] else "device_rooted",
                                "high", 0.45))
            if roll < 0.045:
                signals.append(("geo_out_of_bounds", "high", 0.5))
            elif roll < 0.07:
                signals.append(("vpn_detected", "medium", 0.42))
            elif roll < 0.095:
                signals.append(("liveness_low_confidence", "medium", 0.42))
            elif roll < 0.11:
                signals.append(("liveness_failed", "critical", 0.75))
            elif roll < 0.12:
                signals.append(("impossible_travel", "critical", 0.75))
            if spec["face"] and rng.random() < 0.06:
                signals.append(("face_match_low_confidence", "medium", 0.4))

            off = any(sig[0] in ("geo_out_of_bounds", "impossible_travel") for sig in signals)
            v_name, v_lat, v_lon = VENUES[spec["venue"]]
            if off:
                _, lat, lon = rng.choice(OFF_CAMPUS)
                lat += rng.uniform(-0.002, 0.002)
                lon += rng.uniform(-0.002, 0.002)
            else:
                lat = v_lat + rng.uniform(-0.00035, 0.00035)
                lon = v_lon + rng.uniform(-0.00035, 0.00035)
            distance = metres(lat, lon, v_lat, v_lon)

            live_fail = any(sig[0] == "liveness_failed" for sig in signals)
            live_low = any(sig[0] == "liveness_low_confidence" for sig in signals)
            liveness = rng.uniform(0.18, 0.38) if live_fail else (
                rng.uniform(0.52, 0.68) if live_low else rng.uniform(0.84, 0.99))
            face_low = any(sig[0] == "face_match_low_confidence" for sig in signals)
            face_score = (rng.uniform(0.55, 0.7) if face_low else rng.uniform(0.86, 0.99)) if spec["face"] else None

            risk = min(0.98, rng.uniform(0.05, 0.2) + sum(sig[2] for sig in signals))
            if risk >= 0.85:
                status = "rejected"
            elif risk >= 0.5:
                status = "flagged"
            else:
                status = "approved"

            row = add("checkins", id=uid(), session_id=sess["id"], student_id=s["id"],
                      device_id=devices[s["id"]]["id"], status=status, checked_in_at=at,
                      verified_at=at + timedelta(seconds=rng.uniform(1.2, 3.5)),
                      latitude=lat, longitude=lon, location_accuracy_meters=rng.uniform(4, 28),
                      distance_from_venue_meters=distance, liveness_passed=not live_fail,
                      liveness_score=liveness,
                      liveness_challenge_type=rng.choice(["blink", "head_turn", "speak", "passive"]),
                      face_match_passed=(not face_low) if spec["face"] else None,
                      face_match_score=face_score,
                      face_embedding_hash=s["face_embedding_hash"] if s["face_enrolled"] else None,
                      risk_score=round(risk, 3),
                      risk_factors=json.dumps([sig[0] for sig in signals]) if signals else None,
                      qr_code_verified=rng.random() < 0.7, reviewed_by_id=None, reviewed_at=None,
                      review_notes=None, appeal_reason=None, appealed_at=None,
                      scheduled_deletion_at=at + timedelta(days=RETENTION_DAYS))
            row["_lecturer"] = lecturer
            devices[s["id"]]["total_checkins"] += 1
            for kind_, severity, weight in signals:
                detail = {"distance_m": round(distance)} if kind_ == "geo_out_of_bounds" else {}
                add("risk_signals", id=uid(), checkin_id=row["id"], signal_type=kind_, severity=severity,
                    confidence=round(rng.uniform(0.7, 0.98), 2), details=json.dumps(detail),
                    weight=weight, detected_at=row["verified_at"])
            audit(f"checkin_{status}", at, s["id"], "checkin", row["id"],
                  {"session_id": sess["id"], "risk_score": row["risk_score"]},
                  device_id=devices[s["id"]]["id"])
            audit("login_success", at - timedelta(minutes=rng.uniform(1, 4)), s["id"], "user", s["id"])
            if status in ("flagged", "rejected"):
                flagged_rows.append(row)

    sessions = []
    for offset in range(HISTORY_DAYS, 0, -1):
        day = TODAY_SGT - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        week = (HISTORY_DAYS - offset) // 7 + 1
        for course in courses:
            spec = course["_spec"]
            if day.weekday() == spec["weekday"]:
                start = sgt_to_utc(day, spec["hour"])
                status = "closed"
                if spec["code"] == "SC2005" and offset in range(8, 13):
                    status = "cancelled"
                sess = session(course, f"Week {week} Lecture", "lecture", start, 120, status)
                if status == "closed":
                    check_ins(sess)
                sessions.append(sess)
            if course["_groups"] and day.weekday() == (spec["weekday"] + 2) % 5:
                for g, group in enumerate(course["_groups"]):
                    kind = group["kind"]
                    label = ("Tutorial" if kind == "tutorial" else "Lab")
                    start = sgt_to_utc(day, 14 + 2 * g)
                    sess = session(course, f"Week {week} {label} {group['name']}", kind, start, 90,
                                   "closed", group=group, members=group["_members"])
                    check_ins(sess, share=1.04)
                    sessions.append(sess)

    today_plan = [
        ("SC2001", "Lecture: Graph Algorithms", "lecture", NOW - timedelta(hours=6), 120, "closed"),
        ("SC2008", "Lecture: Transport Layer", "lecture", NOW - timedelta(hours=3, minutes=30), 120, "closed"),
        ("SC4001", "Lecture: Convolutional Networks", "lecture", NOW - timedelta(hours=2, minutes=15), 90, "closed"),
    ]
    for code, name, kind, start, minutes, status in today_plan:
        sess = session(by_code[code], name, kind, start, minutes, status)
        check_ins(sess)
        sessions.append(sess)

    live = session(by_code["SC3000"], "Lecture: Adversarial Search", "lecture",
                   NOW - timedelta(minutes=20), 120, "active")
    live["checkin_opens_at"] = NOW - timedelta(minutes=35)
    live["checkin_closes_at"] = NOW + timedelta(minutes=60)
    check_ins(live, share=0.75, until=NOW)
    sessions.append(live)

    t1 = by_code["SC2001"]["_groups"][0]
    upcoming = [
        session(by_code["SC2002"], "Lecture: Design Patterns", "lecture", NOW + timedelta(hours=3), 120, "scheduled"),
        session(by_code["SC2001"], "Week 4 Tutorial T1", "tutorial", NOW + timedelta(days=1, hours=1), 90,
                "scheduled", group=t1, members=t1["_members"]),
        session(by_code["SC2005"], "Week 4 Lecture: Virtual Memory", "lecture", NOW + timedelta(days=2), 120, "scheduled"),
    ]
    sessions.extend(upcoming)

    for row in flagged_rows:
        age = NOW - row["checked_in_at"]
        lecturer = row["_lecturer"]
        if age > timedelta(days=2) and rng.random() < 0.75:
            approve = row["status"] == "flagged" and rng.random() < 0.7
            row["reviewed_by_id"] = lecturer["id"]
            row["reviewed_at"] = row["checked_in_at"] + timedelta(hours=rng.uniform(3, 30))
            row["status"] = "approved" if approve else "rejected"
            row["review_notes"] = rng.choice([
                "Confirmed present in class.", "Seen in the lecture hall, VPN was on.",
                "Student showed up at the start.",
            ]) if approve else rng.choice([
                "Location 9 km from the venue.", "Not seen in class.", "Liveness check failed twice.",
            ])
            audit("checkin_reviewed", row["reviewed_at"], lecturer["id"], "checkin", row["id"],
                  {"approved": approve, "notes": row["review_notes"]})

    appeal_pool = [r for r in flagged_rows
                   if r["status"] in ("rejected", "flagged")
                   and timedelta(hours=8) < NOW - r["checked_in_at"] < timedelta(days=6)]
    reasons = [
        "I was in the lecture hall, my phone's location was off.",
        "My camera was blurry, I can show my attendance with my friend.",
        "Had to use the library Wi-Fi, which goes through a VPN.",
    ]
    for i, row in enumerate(appeal_pool[:4]):
        row["appeal_reason"] = reasons[i % len(reasons)]
        row["appealed_at"] = row["checked_in_at"] + timedelta(hours=rng.uniform(2, 6))
        audit("checkin_appealed", row["appealed_at"], row["student_id"], "checkin", row["id"],
              {"reason": row["appeal_reason"]})
        if i == 0:
            lecturer = row["_lecturer"]
            row["status"] = "approved"
            row["reviewed_by_id"] = lecturer["id"]
            row["reviewed_at"] = row["appealed_at"] + timedelta(hours=5)
            row["review_notes"] = "Appeal accepted: confirmed with the TA's head count."
            audit("checkin_reviewed", row["reviewed_at"], lecturer["id"], "checkin", row["id"],
                  {"approved": True, "notes": row["review_notes"], "appeal": True})
        else:
            row["status"] = "appealed"

    for person in instructors + tas + [admin]:
        for d in range(HISTORY_DAYS, -1, -2):
            audit("login_success", NOW - timedelta(days=d, hours=rng.uniform(0.2, 9)),
                  person["id"], "user", person["id"])
    for d in range(HISTORY_DAYS, -1, -1):
        if rng.random() < 0.3:
            audit("data_exported", NOW - timedelta(days=d, hours=rng.uniform(1, 8)),
                  rng.choice(instructors)["id"], "session", rng.choice(sessions)["id"], {"format": "csv"})

    target = students[4]
    for minutes in (95, 92, 90, 88):
        audit("login_failed", NOW - timedelta(minutes=minutes), target["id"], "user", None,
              {"email": target["email"], "reason": "wrong_password"}, success=False, ip="203.117.44.19")
    audit("login_failed", NOW - timedelta(hours=7), None, "user", None,
          {"email": "unknown.user@e.saiv.edu", "reason": "unknown_email"}, success=False, ip="118.189.12.6")
    audit("security_violation", NOW - timedelta(minutes=86), target["id"], "user", target["id"],
          {"reason": "Repeated failed logins from an unfamiliar network"}, success=False, ip="203.117.44.19")

    forgetful = students[11]
    audit("password_reset_requested", NOW - timedelta(hours=5), forgetful["id"], "user", forgetful["id"],
          {"email": forgetful["email"]})

    return {"admin": admin, "instructors": instructors, "tas": tas, "students": students,
            "courses": courses, "sessions": sessions, "live": live}


TABLE_ORDER = [
    "users", "devices", "courses", "course_settings", "course_instructors", "enrollments",
    "course_groups", "group_members", "sessions", "session_groups", "session_tas",
    "checkins", "risk_signals", "audit_logs",
]


def to_sql() -> str:
    lines = [
        "SET client_min_messages = warning;",
        "BEGIN;",
        "DO $$ DECLARE t text; BEGIN",
        "  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
        "           AND tablename NOT IN ('alembic_version', 'schema_migrations') LOOP",
        "    EXECUTE format('TRUNCATE TABLE %I CASCADE', t);",
        "  END LOOP;",
        "END $$;",
    ]
    for table in TABLE_ORDER:
        rows = ROWS.get(table) or []
        if not rows:
            continue
        cols = [c for c in COLUMNS[table] if not c.startswith("_")]
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            values = ",\n".join("(" + ", ".join(lit(r.get(c)) for c in cols) + ")" for r in chunk)
            lines.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n{values};")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--container", default="saiv-postgres")
    parser.add_argument("--psql", help="Run this psql command instead of docker exec")
    parser.add_argument("--sql-only", action="store_true", help="Print the SQL instead of running it")
    args = parser.parse_args()

    data = build()
    sql = to_sql()
    if args.sql_only:
        sys.stdout.write(sql)
        return 0

    if args.psql:
        cmd = shlex.split(args.psql) + ["-q", "-v", "ON_ERROR_STOP=1"]
    else:
        cmd = ["docker", "exec", "-i", args.container, "psql", "-U", "saiv", "-d", "saiv",
               "-q", "-v", "ON_ERROR_STOP=1"]
    result = subprocess.run(cmd, input=sql, text=True)
    if result.returncode != 0:
        print("\nSeeding failed; nothing was changed (the whole load runs in one transaction).")
        return result.returncode

    counts = {t: len(ROWS.get(t) or []) for t in ("users", "courses", "sessions", "checkins", "audit_logs")}
    statuses = {}
    for row in ROWS["checkins"]:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    live = data["live"]
    lecturer = next(i for i in data["instructors"] if i["id"] == live["instructor_id"])
    print("Demo data loaded.")
    print("  " + ", ".join(f"{n} {t}" for t, n in counts.items()))
    print("  check-ins by status: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    print(f"\nEvery account's password is {PASSWORD}")
    print("  admin       admin@saiv.edu")
    for name, email in INSTRUCTORS:
        print(f"  instructor  {email:<28} {name}")
    for name, email in TAS:
        print(f"  ta          {email:<28} {name}")
    print(f"  students    <first>.<last>@e.saiv.edu, e.g. {data['students'][0]['email']}")
    print(f"\nLive now: SC3000 '{live['name']}' ({lecturer['full_name']}), "
          f"check-in open for the next 60 minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())