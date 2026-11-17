"""Run existing backend tests only against the dedicated Phase 3A test database."""
import os
import subprocess
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if url.host != 'postgres' or url.database != 'saiv':
    raise SystemExit('Unexpected source database; refusing test setup.')
test_url = url.set(database='saiv_phase3a_tests').render_as_string(hide_password=False)
os.environ['DATABASE_URL'] = test_url
os.environ['TEST_DATABASE_URL'] = test_url
print('Tests restricted to saiv_phase3a_tests. Existing face/challenge stubs remain test-only.', flush=True)
raise SystemExit(subprocess.call(['pytest', 'tests/unit', 'tests/flow', '-o', 'addopts=--strict-markers',
                                 '-q', '--tb=short', '--junitxml=/results/backend.xml']))
