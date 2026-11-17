"""Additive runtime smoke checks against the isolated stack. No TRUNCATE/DELETE.

Creates one synthetic account/browser per run; never logs credentials or images.
Run inside backend with the gateway service name. Tests real HTTP, not stubs.
"""
import os
import secrets
import uuid

import httpx

origin = os.getenv('SAIV_SMOKE_ORIGIN', 'http://gateway:8080').rstrip('/')
client = httpx.Client(base_url=origin, headers={'Host': 'localhost:8080'}, timeout=20)
passed = 0


def check(method, path, expected, **kwargs):
    global passed
    response = client.request(method, path, **kwargs)
    if response.status_code != expected:
        raise AssertionError(f'{method} {path}: expected {expected}, got {response.status_code}')
    passed += 1
    print(f'PASS {method} {path}: {expected}')
    return response


try:
    config = check('GET', '/api/config', 200)
    assert config.headers['cache-control'] == 'no-store'
    assert config.json()['apiUrl'].endswith('/api/v1')
    for route in ['/login', '/register', '/forgot-password', '/dashboard']:
        page = check('GET', route, 200)
        if route == '/dashboard':
            assert 'Checking your session' in page.text
            assert 'Account Setup' not in page.text
    check('GET', '/portal/', 503)
    for route in ['/auth/me', '/devices/my-devices', '/checkins/me', '/courses/me', '/sessions/my-sessions']:
        check('GET', '/api/v1' + route, 401)
    for route in ['/users/me/face/enroll', '/users/me/face/verify', '/checkins/liveness-challenge']:
        check('POST', '/api/v1' + route, 401, json={})
    check('POST', '/api/v1/auth/register', 422, json={})
    check('POST', '/api/v1/auth/login', 422, json={})
    email = f'baseline-{uuid.uuid4().hex}@example.edu'
    password = secrets.token_urlsafe(24)
    account = check('POST', '/api/v1/auth/register', 201, json={
        'email': email, 'full_name': 'Phase 3A Synthetic Baseline',
        'password': password, 'role': 'student',
    }).json()
    assert account['camera_consent'] is False
    assert account['geolocation_consent'] is False
    assert 'hashed_password' not in account and 'face_embedding_hash' not in account
    login = check('POST', '/api/v1/auth/login', 200, json={'email': email, 'password': password}).json()
    auth = {'Authorization': 'Bearer ' + login['access_token']}
    check('GET', '/api/v1/auth/me', 200, headers=auth)
    check('GET', '/api/v1/users/', 403, headers=auth)
    check('GET', '/api/v1/devices/', 403, headers=auth)
    devices = check('GET', '/api/v1/devices/my-devices', 200, headers=auth).json()
    assert devices == []
    device = check('POST', '/api/v1/devices/', 201, headers=auth, json={
        'device_fingerprint': str(uuid.uuid4()), 'platform': 'web',
        'device_name': 'Phase 3A synthetic browser',
    }).json()
    assert device['user_id'] == account['id'] and device['is_active']
    assert device['is_trusted'] is False
    assert 'public_key' not in device and 'attestation_token' not in device
    check('GET', '/api/v1/devices/my-devices', 200, headers=auth)
    for route in ['/checkins/me', '/courses/me', '/sessions/active']:
        check('GET', '/api/v1' + route, 200, headers=auth)
    check('GET', '/api/v1/sessions/my-sessions', 403, headers=auth)
    # Meet the request-length constraint, without using anyone's biometric data.
    check('POST', '/api/v1/users/me/face/enroll', 400, headers=auth, json={'image': 'not-a-biometric-image' * 3})
    check('POST', '/api/v1/users/me/face/verify', 400, headers=auth, json={'image': 'not-a-biometric-image' * 3})
    check('POST', '/api/v1/checkins/liveness-challenge?session_id=' + str(uuid.uuid4()), 404, headers=auth)
    check('POST', '/api/v1/checkins/', 422, headers=auth, json={})
    refreshed = check('POST', '/api/v1/auth/refresh', 200, json={'refresh_token': login['refresh_token']}).json()
    assert refreshed['access_token'] and refreshed['refresh_token']
    check('POST', '/api/v1/auth/logout', 200, headers=auth)
    print(f'Runtime smoke complete: {passed} HTTP checks passed; one labelled synthetic account/browser added.')
    print('NOT TESTED: browser interactions, valid biometric inference, real attendance, tunnel or physical phone.')
finally:
    client.close()
