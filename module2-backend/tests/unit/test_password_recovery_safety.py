import pytest
from app.services import email_service
from app.services.user_service import UserService


@pytest.mark.parametrize('port', ['not-a-port', '0', '99999'])
def test_invalid_smtp_configuration_fails_safely(monkeypatch, port, caplog):
    monkeypatch.setenv('SMTP_HOST', 'mock-mail.example.edu')
    monkeypatch.setenv('SMTP_PORT', port)
    assert email_service.send_email('synthetic@example.edu', 'Reset', '123456') is False
    assert '123456' not in caplog.text


def test_unavailable_mail_thread_does_not_reveal_code_or_break_generic_reply(monkeypatch, caplog):
    import threading
    class RefusedThread:
        def __init__(self, *args, **kwargs): pass
        def start(self): raise RuntimeError('synthetic failure')
    monkeypatch.setattr(threading, 'Thread', RefusedThread)
    UserService._email_code('synthetic@example.edu', '123456')
    assert '123456' not in caplog.text


def test_missing_hmac_secret_is_refused(monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        UserService._code_hash('synthetic', '123456')


@pytest.mark.parametrize('implicit', [False, True])
def test_smtp_tls_validates_server_identity(monkeypatch, implicit):
    import ssl
    contexts = []
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            if 'context' in kwargs: contexts.append(kwargs['context'])
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self, context): contexts.append(context)
        def send_message(self, message): pass
    monkeypatch.setenv('SMTP_HOST', 'mock-mail.example.edu')
    monkeypatch.setenv('SMTP_PORT', '465' if implicit else '587')
    monkeypatch.setenv('SMTP_SSL', '1' if implicit else '0')
    monkeypatch.setenv('SMTP_STARTTLS', '1')
    monkeypatch.delenv('SMTP_USER', raising=False)
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, 'SMTP_SSL', FakeSMTP)
    assert email_service.send_email('synthetic@example.edu', 'Reset', 'synthetic message')
    assert len(contexts) == 1 and contexts[0].check_hostname
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED


def test_smtp_credentials_cannot_be_sent_over_plaintext(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'mock-mail.example.edu')
    monkeypatch.setenv('SMTP_SSL', '0'); monkeypatch.setenv('SMTP_STARTTLS', '0')
    monkeypatch.setenv('SMTP_USER', 'synthetic-user')
    assert email_service.send_email('synthetic@example.edu', 'Reset', 'synthetic message') is False
