"""Optional disposable-demo entrypoint, never a production application change.

Allows only the new localhost:3003 frontend. Refuses original databases/providers.
"""
import os
from urllib.parse import urlsplit

database = urlsplit(os.environ.get('DATABASE_URL', ''))
if (database.hostname != 'postgres' or database.path != '/saiv_forgot_password_demo' or
        os.environ.get('SMTP_HOST') != 'mailpit'):
    raise RuntimeError('This entrypoint is restricted to the disposable password-reset demo')

from fastapi.middleware.cors import CORSMiddleware
from app.main import app

for middleware in app.user_middleware:
    if middleware.cls is CORSMiddleware:
        middleware.kwargs['allow_origins'] = ['http://localhost:3003']
        break
else:
    raise RuntimeError('Expected existing CORS middleware unavailable')
