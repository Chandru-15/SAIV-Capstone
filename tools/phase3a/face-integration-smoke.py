"""Real Module 2 -> Module 3 checks with repository-approved image fixtures.

Creates one disposable student and stores only its legitimate service-generated
template. No mocks, synthetic success verdicts, raw-image files or image logging.
Run in the backend image with sample_images mounted read-only at /fixtures.
"""
import base64
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import sys
import uuid
import zlib

import httpx
from sqlalchemy import create_engine, text

sys.path.insert(0, os.getcwd())
from app.clients.face_client import FaceClient

public_origin = os.environ.get('SAIV_EXPECTED_PUBLIC_ORIGIN', 'https://gxcs7n6m-8080.asse.devtunnels.ms')
host = httpx.URL(public_origin).netloc.decode()
gateway = httpx.Client(base_url='http://gateway:8080/api/v1',
                       headers={'Host': host, 'Origin': public_origin}, timeout=60)
face = httpx.Client(base_url='http://face-recognition:8001', timeout=60)
checks = 0


def passed(label):
    global checks
    checks += 1
    print('PASS', label, flush=True)


def request(method, path, expected, **kwargs):
    response = gateway.request(method, path, **kwargs)
    assert response.status_code == expected, f'{method} {path}: expected {expected}, got {response.status_code}'
    return response


def fixture(name):
    path = Path('/fixtures') / name
    assert path.is_file(), 'Required real fixture is absent; no fallback permitted'
    return base64.b64encode(path.read_bytes()).decode('ascii')


def blank_image():
    # Same uniform non-face negative case as the public tests, generated in memory.
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    raw = (b'\x00'+bytes([50,100,50])*128)*128
    png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',128,128,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
    return base64.b64encode(png).decode('ascii')


try:
    config = gateway.get('http://gateway:8080/api/config').json()
    assert config['apiUrl'] == public_origin+'/api/v1'
    assert config['staffPortalUrl'] == public_origin+'/portal/'
    preflight = gateway.options('/users/me/face/enroll', headers={
        'Access-Control-Request-Method':'POST', 'Access-Control-Request-Headers':'authorization,content-type'})
    assert preflight.status_code == 200
    assert preflight.headers.get('access-control-allow-origin') == public_origin
    passed('public HTTPS URLs and exact-origin enrollment preflight (local gateway path; no private-tunnel authentication bypass)')

    enrolled_image, alternate_image, other_image = fixture('obama.jpg'), fixture('obama2.jpg'), fixture('biden.jpg')
    blank = blank_image()
    credentials = {'email':f'face-runtime-{uuid.uuid4().hex}@example.edu',
                   'password':secrets.token_urlsafe(24)}
    user = request('POST','/auth/register',201,json={**credentials,'full_name':'Phase 3A Real Model Fixture','role':'student'}).json()
    login = request('POST','/auth/login',200,json=credentials).json()
    auth = {'Authorization':'Bearer '+login['access_token']}
    assert user['camera_consent'] is False and user['face_enrolled'] is False
    refused = request('POST','/users/me/face/enroll',400,headers=auth,json={'image':enrolled_image})
    assert 'consent' in refused.json()['detail'].lower()
    passed('camera consent required before real fixture enrollment')

    request('PATCH','/auth/me',200,headers=auth,json={'camera_consent':True})
    enrollment = request('POST','/users/me/face/enroll',200,headers=auth,json={'image':enrolled_image}).json()
    assert enrollment['success'] is True and enrollment['face_enrolled'] is True
    assert 0.5 <= enrollment['quality_score'] <= 1
    assert 'face_embedding_hash' not in enrollment and 'image' not in enrollment
    profile = request('GET','/auth/me',200,headers=auth).json()
    assert profile['face_enrolled'] is True and profile['camera_consent'] is True
    passed('actual backend enrollment and refreshed authenticated enrollment state')

    engine = create_engine(os.environ['DATABASE_URL'])
    with engine.connect() as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        template = conn.scalar(text('SELECT face_embedding_hash FROM users WHERE id=:id'),{'id':user['id']})
        assert re.fullmatch('[0-9a-f]{64}',template)
        audits = list(conn.scalars(text("SELECT details FROM audit_logs WHERE user_id=:id AND action='face_enrolled'"),{'id':user['id']}))
        assert audits and all(template not in json.dumps(row) and enrolled_image not in json.dumps(row) for row in audits)
    engine.dispose()
    passed('only expected 64-hex template persisted; no template/raw fixture copied into enrollment audit details')

    scores = {}
    for label, image, expected_match in [('same_image',enrolled_image,True),
                                          ('same_person_other_image',alternate_image,True),
                                          ('different_person',other_image,False)]:
        result = request('POST','/users/me/face/verify',200,headers=auth,json={'image':image}).json()
        assert result['face_detected'] is True and math.isfinite(result['match_score'])
        assert result['match_threshold'] == 0.70
        scores[label] = {'score':result['match_score'],'passed':result['match_passed']}
        assert result['match_passed'] is expected_match, f'Actual fixture comparison failed: {label}, score={result["match_score"]}'
        passed('real recognition: '+label)
    print('Actual match scores:',json.dumps(scores),flush=True)

    for invalid in ['!'*40,base64.b64encode(b'not an image format at all, even though base64 is valid').decode()]:
        # Module 2 maps FaceImageRejected to its existing ValidationError (422).
        # Module 3 itself returns 400; this difference is also asserted by its flow tests.
        request('POST','/users/me/face/enroll',422,headers=auth,json={'image':invalid})
        assert face.post('/face/enroll',json={'user_id':user['id'],'camera_consent':True,'image':invalid}).status_code == 400
    request('POST','/users/me/face/enroll',422,headers=auth,json={'image':blank})
    assert face.post('/face/enroll',json={'user_id':user['id'],'camera_consent':True,'image':blank}).status_code == 400
    no_face = request('POST','/users/me/face/verify',200,headers=auth,json={'image':blank}).json()
    assert no_face['face_detected'] is False and no_face['match_passed'] is False
    passed('invalid base64, invalid image and missing-face handling without enrollment corruption')

    client = FaceClient()
    assert client.health()
    for challenge in ['blink','head_turn']:
        # Repeated still photographs are negative motion tests, never live evidence.
        liveness = request('POST','/users/me/face/liveness-frames',200,headers=auth,
                           json={'frames':[enrolled_image]*3,'challenge_type':challenge}).json()
        assert liveness['challenge_type'] == challenge and liveness['frames_with_face'] == 3
        assert liveness['challenge_passed'] is False and liveness['liveness_passed'] is False
        if challenge == 'blink':
            ears = liveness['details']['eye_aspect_ratios']
            assert len(ears) == 3 and all(x is not None and math.isfinite(x) for x in ears)
        else:
            assert liveness['details']['yaw_proxy_delta'] == 0
        passed('real '+challenge+' frame processing rejects repeated stills; live challenge completion remains manual')
    blank_live = client.check_liveness_frames([blank]*3,'blink')
    assert not blank_live.liveness_passed and blank_live.frames_with_face == 0
    invalid_type = face.post('/liveness/check',json={'challenge_type':'unsupported','challenge_response':blank})
    assert invalid_type.status_code == 400
    passed('real face-client response contracts and liveness invalid/no-face handling')

    final = request('GET','/auth/me',200,headers=auth).json()
    assert final['face_enrolled'] is True
    assert request('GET','/checkins/me',200,headers=auth).json()['total'] == 0
    assert request('GET','/devices/my-devices',200,headers=auth).json() == []
    passed('negative tests preserve valid enrollment; no attendance or device registration fabricated')
    print(f'COMPLETE: {checks} real-model integration groups passed. One synthetic account/template added; existing accounts untouched.',flush=True)
    print('NOT CLAIMED: phone enrollment, successful live blink/head turn, attendance acceptance or anti-spoofing effectiveness.',flush=True)
finally:
    gateway.close()
    face.close()
