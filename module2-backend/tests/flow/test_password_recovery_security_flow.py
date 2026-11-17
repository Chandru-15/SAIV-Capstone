"""Synthetic/disposable PostgreSQL only. Email stays in memory; no live SMTP."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import re
import threading
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import PasswordResetCode, User
from app.services.auth_service import AuthService
from app.services.user_service import UserService
from app.services.exceptions import ValidationError

pytestmark = pytest.mark.flow


@pytest.fixture
def mailbox(monkeypatch):
    sent = []
    monkeypatch.setattr(UserService, '_email_code', staticmethod(lambda email, code, by_admin=False: sent.append((email, code))))
    return sent


def request(client, email):
    return client.post('/auth/forgot-password', json={'email': email})


def redeem(client, email, code, password='changed-password1'):
    return client.post('/auth/reset-password', json={'email': email, 'code': code, 'new_password': password})


@pytest.mark.parametrize('role', ['student', 'ta', 'instructor', 'admin'])
def test_same_reset_and_login_contract_for_all_roles(client, make_actor, role, mailbox):
    actor = make_actor(role)
    assert request(client, actor.email).status_code == 200
    assert redeem(client, actor.email, mailbox[0][1]).status_code == 200
    assert client.post('/auth/login', json={'email': actor.email, 'password': 'password123'}).status_code == 401
    result = client.post('/auth/login', json={'email': actor.email, 'password': 'changed-password1'})
    assert result.status_code == 200 and result.json()['user']['role'] == role
    # Module 4 SaivClient.login uses this same route/token/user shape.
    assert {'access_token', 'refresh_token', 'user'} <= result.json().keys()


def test_inactive_account_cannot_request_or_redeem_without_reactivation(client, admin, student, mailbox, db):
    request(client, student.email)
    code = mailbox[0][1]
    user = db.get(User, student.id)
    user.is_active = False; db.commit()
    previous = user.hashed_password
    mailbox.clear()
    assert request(client, student.email).json() == request(client, 'unknown@example.edu').json()
    assert mailbox == []
    assert redeem(client, student.email, code).status_code == 422
    db.refresh(user)
    assert not user.is_active and user.hashed_password == previous


def test_admin_password_change_invalidates_pending_email_code(client, admin, student, mailbox):
    request(client, student.email); code = mailbox[0][1]
    result = client.post(f'/admin/users/{student.id}/reset-password', headers=admin.headers)
    assert result.status_code == 200
    assert redeem(client, student.email, code).status_code == 422
    assert client.post('/auth/login', json={'email': student.email, 'password': result.json()['temporary_password']}).status_code == 200


@pytest.mark.parametrize('password', ['x' * 73, '\U0001f600' * 19])
def test_bcrypt_truncation_is_refused_without_consuming_code(client, student, mailbox, password):
    request(client, student.email); code = mailbox[0][1]
    assert redeem(client, student.email, code, password).status_code == 422
    assert redeem(client, student.email, code).status_code == 200


@pytest.fixture
def lock_wait_clock(monkeypatch):
    """Advance a controlled clock across the real PostgreSQL account lock.

    No wall-clock sleeps or machine-clock changes: the real locked query still
    runs, then elapsed lock-wait time is simulated before the caller resumes.
    """
    from app.models.base import utcnow
    from app.services import user_service

    before = utcnow()
    after = before + timedelta(minutes=2)
    clock = [before]
    original = UserService._locked_reset_user

    def locked(service, **kwargs):
        user = original(service, **kwargs)
        clock[0] = after
        return user

    def install():
        monkeypatch.setattr(user_service, 'utcnow', lambda: clock[0])
        monkeypatch.setattr(UserService, '_locked_reset_user', locked)

    return before, after, install


@pytest.mark.parametrize('seconds_remaining,expected_status', [(1, 200), (0, 422), (-1, 422)])
def test_redemption_checks_expiry_after_lock_wait(
        client, student, mailbox, db, lock_wait_clock, seconds_remaining, expected_status):
    request(client, student.email)
    before, after, install = lock_wait_clock
    row = db.scalars(select(PasswordResetCode).where(PasswordResetCode.user_id == student.id)).one()
    row.expires_at = after + timedelta(seconds=seconds_remaining)
    assert row.expires_at > before  # Every case is valid before the lock wait.
    db.commit()
    previous_hash = db.get(User, student.id).hashed_password
    install()

    response = redeem(client, student.email, mailbox[0][1])
    assert response.status_code == expected_status
    db.refresh(row)
    db.expire_all()
    if expected_status == 200:
        assert row.used_at == after
        assert client.post('/auth/login', json={'email': student.email, 'password': 'changed-password1'}).status_code == 200
    else:
        assert row.used_at is None and row.attempts == 0
        assert db.get(User, student.id).hashed_password == previous_hash
        assert client.post('/auth/login', json={'email': student.email, 'password': 'password123'}).status_code == 200


@pytest.mark.parametrize('by_admin', [False, True])
def test_issued_code_gets_full_validity_after_account_lock(
        client, admin, student, mailbox, db, lock_wait_clock, by_admin, monkeypatch):
    from app.services.user_service import RESET_CODE_TTL_MINUTES

    before, after, install = lock_wait_clock
    install()
    if by_admin:
        monkeypatch.setenv('SMTP_HOST', 'mock-mail.example.edu')  # Delivery is mocked by mailbox.
        assert client.post(f'/admin/users/{student.id}/send-reset-email', headers=admin.headers).status_code == 200
    else:
        assert request(client, student.email).status_code == 200
    row = db.scalars(select(PasswordResetCode).where(PasswordResetCode.user_id == student.id)).one()
    assert len(mailbox) == 1
    assert row.expires_at == after + timedelta(minutes=RESET_CODE_TTL_MINUTES)
    assert row.expires_at > before + timedelta(minutes=RESET_CODE_TTL_MINUTES)


def test_two_concurrent_redemptions_have_exactly_one_winner(client, student, mailbox, engine, monkeypatch):
    request(client, student.email); code = mailbox[0][1]
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    start = threading.Barrier(2)
    original_hash = AuthService.hash_password
    def slower_hash(password):
        time.sleep(.08)  # Gives an unlocked implementation a reproducible race.
        return original_hash(password)
    monkeypatch.setattr(AuthService, 'hash_password', staticmethod(slower_hash))
    def attempt(password):
        with sessions() as session:
            start.wait(timeout=5)
            try:
                UserService(session).confirm_password_reset(student.email, code, password)
                return password
            except ValidationError:
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['concurrent-password-A', 'concurrent-password-B']))
    winners = [value for value in results if value]
    assert len(winners) == 1
    assert client.post('/auth/login', json={'email': student.email, 'password': winners[0]}).status_code == 200
    assert redeem(client, student.email, code).status_code == 422


def test_concurrent_guesses_cannot_lose_attempt_increments(client, student, mailbox, engine):
    request(client, student.email); code = mailbox[0][1]
    wrong = '111111' if code != '111111' else '222222'
    sessions = sessionmaker(bind=engine); start = threading.Barrier(5)
    def guess(_):
        with sessions() as session:
            start.wait(timeout=5)
            try: UserService(session).confirm_password_reset(student.email, wrong, 'changed-password1')
            except ValidationError: return True
            return False
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert all(pool.map(guess, range(5)))
    with sessions() as session:
        row = session.scalars(select(PasswordResetCode).where(PasswordResetCode.user_id == student.id)).one()
        assert row.attempts == 5 and row.used_at is not None
    assert redeem(client, student.email, code).status_code == 422


def test_concurrent_requests_do_not_bypass_resend_cooldown(student, mailbox, engine):
    sessions = sessionmaker(bind=engine); start = threading.Barrier(2)
    def issue(_):
        with sessions() as session:
            start.wait(timeout=5)
            UserService(session).request_password_reset(student.email, ip_address=None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(issue, range(2)))
    assert len(mailbox) == 1


def test_account_hourly_limit_remains_generic(client, student, mailbox, db):
    for _ in range(6):
        assert request(client, student.email).status_code == 200
        for row in db.scalars(select(PasswordResetCode).where(PasswordResetCode.user_id == student.id)):
            row.created_at -= timedelta(seconds=61)
        db.commit()
    assert len(mailbox) == 5


def test_ip_limit_counts_unknown_requests_without_revealing_accounts(client, student, mailbox):
    # This Starlette TestClient has no transport peer address. Supply the
    # existing proxy hint explicitly; all requests must have the same source.
    headers = {'x-forwarded-for': '192.0.2.10'}
    messages = [client.post('/auth/forgot-password', headers=headers,
        json={'email': f'unknown{i}@example.edu'}).json() for i in range(11)]
    assert all(message == messages[0] for message in messages)
    assert client.post('/auth/forgot-password', headers=headers, json={'email': student.email}).json() == messages[0]
    assert len(mailbox) == 0


def test_concurrent_ip_limit_is_atomic_without_a_new_rate_limit_table(engine, monkeypatch):
    from sqlalchemy import func
    from app.models import AuditLog
    from app.services import user_service
    monkeypatch.setattr(user_service, 'RESET_REQUESTS_PER_IP_PER_HOUR', 2)
    sessions = sessionmaker(bind=engine); start = threading.Barrier(4)
    def issue(index):
        with sessions() as session:
            start.wait(timeout=5)
            UserService(session).request_password_reset(f'unknown{index}@example.edu', ip_address='192.0.2.11')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(issue, range(4)))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == 'password_reset_requested', AuditLog.ip_address == '192.0.2.11')) == 2


def test_real_email_composition_and_api_reset_with_mocked_smtp(client, student, monkeypatch, caplog):
    from app.services import email_service
    delivered = threading.Event(); captured = []
    class CaptureSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self, context):
            assert context.check_hostname
        def send_message(self, message): captured.append(message); delivered.set()
    monkeypatch.setenv('SMTP_HOST', 'mock-mail.example.edu')
    monkeypatch.setenv('SMTP_PORT', '2525')
    monkeypatch.setenv('SMTP_STARTTLS', '1')
    monkeypatch.setenv('SMTP_SSL', '0')
    monkeypatch.delenv('SMTP_USER', raising=False)
    monkeypatch.setattr(email_service.smtplib, 'SMTP', CaptureSMTP)
    response = request(client, student.email)
    assert response.status_code == 200 and delivered.wait(3)
    code = re.search(r'code is: ([0-9]{6})', captured[0].get_content()).group(1)
    assert code not in response.text and code not in caplog.text
    assert '10 minutes' in captured[0].get_content()
    assert redeem(client, student.email, code).status_code == 200


def test_email_provider_failure_is_generic_and_does_not_change_password(client, student, monkeypatch):
    from app.services import email_service
    attempted = threading.Event()
    class FailedSMTP:
        def __init__(self, *args, **kwargs): attempted.set(); raise OSError('synthetic failure')
    monkeypatch.setenv('SMTP_HOST', 'mock-mail.example.edu')
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FailedSMTP)
    assert request(client, student.email).json() == request(client, 'unknown@example.edu').json()
    assert attempted.wait(3)
    assert client.post('/auth/login', json={'email': student.email, 'password': 'password123'}).status_code == 200


def test_frontend_policy_matches_existing_backend_constants():
    from app.services.user_service import RESET_CODE_TTL_MINUTES, RESET_CODE_RESEND_SECONDS
    source = (Path(__file__).resolve().parents[3] / 'module1-frontend/lib/password-reset.ts').read_text(encoding='utf-8')
    assert int(re.search(r'RESET_CODE_SECONDS = (\d+)', source).group(1)) == RESET_CODE_TTL_MINUTES * 60
    assert int(re.search(r'RESET_RESEND_SECONDS = (\d+)', source).group(1)) == RESET_CODE_RESEND_SECONDS


def test_existing_jwts_are_not_claimed_revoked_by_reset(client, student, mailbox):
    request(client, student.email); redeem(client, student.email, mailbox[0][1])
    # Document existing stateless-token limitation explicitly; no new auth system.
    assert client.get('/auth/me', headers=student.headers).status_code == 200
