"""Read-only DB/monitoring probes plus an expiring, single-use Redis probe.

No biometric inputs, table cleanup or model substitution.
"""
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.getcwd())

import httpx
from sqlalchemy import create_engine, text
from app.services.liveness_challenge import RedisChallengeStore, build_store, issue, _KEY_PREFIX

engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    conn.execute(text('SET TRANSACTION READ ONLY'))
    tables = set(conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    expected = {'users','courses','enrollments','sessions','devices','checkins','risk_signals','audit_logs',
                'course_instructors','session_tas','course_settings','course_groups','group_members',
                'session_groups','password_reset_codes'}
    assert expected == tables, f'Unexpected schema tables: {tables ^ expected}'
    schema = Path('/db/schema.sql').read_text()
    indexes = set(conn.scalars(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")))
    wanted_indexes = set(re.findall(r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)', schema, re.I))
    assert wanted_indexes <= indexes, 'Missing base schema indexes'
    constraints = set(conn.scalars(text("SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace")))
    wanted_constraints = set(re.findall(r'CONSTRAINT\s+(\w+)', schema))
    assert wanted_constraints <= constraints, 'Missing named base constraints'
    counts = {table: conn.scalar(text(f'SELECT count(*) FROM {table}')) for table in sorted(expected)}
    print(f'PASS database: {len(tables)} tables, {len(wanted_indexes)} declared indexes and {len(wanted_constraints)} named base constraints present')
    print('Development row counts:', counts)
engine.dispose()

store = build_store()
assert isinstance(store, RedisChallengeStore), 'Unexpected in-memory challenge fallback'
challenge = issue('phase3a-synthetic-probe', 'phase3a-synthetic-probe')
store.put(challenge, 10)
assert 0 < store._redis.ttl(_KEY_PREFIX + challenge.challenge_id) <= 10
assert store.take(challenge.challenge_id) == challenge
assert store.take(challenge.challenge_id) is None
print('PASS Redis: actual application store, TTL and atomic single-use consumption')

with httpx.Client(timeout=10) as client:
    for url in ['http://backend:8000/health', 'http://face-recognition:8001/health',
                'http://dashboard:8501/portal/_stcore/health', 'http://prometheus:9090/-/ready',
                'http://grafana:3000/api/health', 'http://metrics-exporter:9101/metrics']:
        response = client.get(url)
        assert response.status_code == 200, f'{url}: {response.status_code}'
        print('PASS service HTTP:', url)
    assert client.get('http://grafana:3000/api/datasources').status_code == 401
    targets = client.get('http://prometheus:9090/api/v1/targets').json()['data']['activeTargets']
    assert {t['labels']['job'] for t in targets} == {'saiv-exporter','prometheus','otel-collector'}
    assert all(t['health'] == 'up' for t in targets), [(t['labels']['job'], t['health']) for t in targets]
    print('PASS Prometheus: exporter, self and collector scrape targets up')
    for metric in ['saiv_db_up', 'saiv_backend_up', 'saiv_exporter_scrape_ok']:
        result = client.get('http://prometheus:9090/api/v1/query', params={'query': metric}).json()['data']['result']
        assert result and all(float(r['value'][1]) == 1 for r in result), metric
        print('PASS collected metric:', metric)
    # Synthetic trace proves collector intake. It does not prove app instrumentation.
    start = time.time_ns()
    trace = {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'phase3a-smoke'}}]},
                             'scopeSpans':[{'scope':{'name':'phase3a-smoke'}, 'spans':[{
                                 'traceId':uuid.uuid4().hex, 'spanId':uuid.uuid4().hex[:16],
                                 'name':'synthetic-baseline', 'kind':1,
                                 'startTimeUnixNano':str(start), 'endTimeUnixNano':str(start+1000)}]}]}]}
    assert client.post('http://otel-collector:4318/v1/traces', json=trace).status_code == 200
    print('PASS OTLP HTTP synthetic trace intake (application tracing is not claimed)')
    rejected = client.get('http://gateway:8080/api/v1/auth/me', headers={'Host':'unconfigured.invalid'})
    assert rejected.status_code == 400
    print('PASS gateway rejects unconfigured Host')
print('NOT VERIFIED: recognition/model readiness, portal WebSockets through gateway, private tunnel or physical phone.')
