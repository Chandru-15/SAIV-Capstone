# Browser verification localhost pilot

Use a fresh project named `saiv-crypto-pilot` and disposable test accounts. The
override removes the base Compose file's fixed container names and replaces all
published ports. PostgreSQL and Redis are reachable only on the project
network. Keep this setup on loopback. The current demo registration API allows
self-registration as staff, and the existing Streamlit portal has its own
session handling.

## Start on Windows

Run from PowerShell in the feature checkout. Docker Compose v2.24.4 or later is
required for the override tags. Save the environment file path so it can be
reused when restarting the same pilot project.

```powershell
Set-Location 'C:\Users\user\Capstone-dev'
if ((git branch --show-current) -ne 'feature/crypto-device-verification') {
    throw 'Run this from the browser verification feature branch.'
}
docker compose version
$pilotPorts = @(5300, 5301, 5800, 5801, 5825, 5851)
if (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $pilotPorts }) {
    throw 'A pilot port is already occupied.'
}

function New-PilotSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
$pilotEnv = Join-Path $env:TEMP ('saiv-crypto-pilot-' + [Guid]::NewGuid().ToString('N') + '.env')
$pilotDbPassword = New-PilotSecret
$pilotSigningSecret = New-PilotSecret
$pilotGrafanaPassword = New-PilotSecret
@(
    'POSTGRES_USER=saiv',
    'POSTGRES_DB=saiv_crypto_pilot',
    "POSTGRES_PASSWORD=$pilotDbPassword",
    "SECRET_KEY=$pilotSigningSecret",
    "GF_ADMIN_PASSWORD=$pilotGrafanaPassword"
) | Set-Content -LiteralPath $pilotEnv -Encoding ASCII
$pilot = @('--env-file', $pilotEnv, '-p', 'saiv-crypto-pilot',
           '-f', 'docker-compose.yml', '-f', 'compose.crypto-pilot.yml')

# Inspect isolation without printing interpolated credentials.
$cfg = docker compose @pilot config --format json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $cfg.name -ne 'saiv-crypto-pilot') { throw 'Unexpected Compose project.' }
$expectedDatabase = "postgresql://saiv:${pilotDbPassword}@postgres:5432/saiv_crypto_pilot"
if ($cfg.services.backend.environment.DATABASE_URL -ne $expectedDatabase -or
    $cfg.services.backend.environment.SECRET_KEY -ne $pilotSigningSecret -or
    $cfg.services.grafana.environment.GF_SECURITY_ADMIN_PASSWORD -ne $pilotGrafanaPassword) {
    throw 'A shell environment variable overrode a pilot credential or database setting.'
}
foreach ($service in $cfg.services.PSObject.Properties) {
    if ($service.Value.container_name) { throw "Fixed container name on $($service.Name)." }
    foreach ($port in $service.Value.ports) {
        if ($port.host_ip -ne '127.0.0.1' -or [int]$port.published -notin $pilotPorts) {
            throw "Unexpected published port on $($service.Name)."
        }
    }
}
foreach ($resource in @($cfg.volumes.PSObject.Properties) + @($cfg.networks.PSObject.Properties)) {
    if ($resource.Value.external -or $resource.Value.name -notlike 'saiv-crypto-pilot_*') {
        throw "Unscoped Compose resource $($resource.Name)."
    }
}

docker compose @pilot up -d --wait postgres redis
if ($LASTEXITCODE -ne 0) { throw 'Pilot database or Redis did not become healthy.' }
docker compose @pilot run --rm migrate
if ($LASTEXITCODE -ne 0) { throw 'Pilot schema migration failed.' }
docker compose @pilot up -d --build frontend dashboard grafana
if ($LASTEXITCODE -ne 0) { throw 'Pilot application did not start.' }
docker compose @pilot ps
Invoke-RestMethod 'http://localhost:5800/health'
Invoke-RestMethod 'http://localhost:5801/health'
Invoke-RestMethod 'http://localhost:5300/api/config'
```

The student frontend is at [localhost:5300](http://localhost:5300) and the
separately authenticated staff dashboard is at [localhost:5851](http://localhost:5851).
The [local Mailpit inbox](http://localhost:5825) and [local Grafana](http://localhost:5301)
belong to this project.

Run `docker compose @pilot run --rm migrate` again whenever the schema changes.
Use `docker compose @pilot logs --tail 100 backend face-recognition migrate`
for startup errors. Stop with `docker compose @pilot down` to keep the pilot
database volume. Reuse the same environment file and project name to restart
that database.

## Activate and roll back one pilot account

Register a disposable student and a separate disposable admin in this stack.
In Streamlit, use the admin account to create a course and enroll the student.
Create an open attendance session and set its venue to the
test location and turn on face matching and active liveness for the session.
Use a second session for protocol rejection and replay tests.

In the same PowerShell terminal, set the email to the disposable student's
email. The update increments the account's token version so old sessions and
outstanding proofs cannot carry across the policy change.

```powershell
$pilotEmail = 'pilot.student@example.test'
$enablePilot = @'
UPDATE users
SET browser_verification_required = TRUE, token_version = token_version + 1
WHERE email = :'pilot_email' AND role = 'student' AND is_active
  AND browser_verification_required IS DISTINCT FROM TRUE
RETURNING id, email, browser_verification_required;
'@
$enablePilot | docker compose @pilot exec -T postgres psql -U saiv -d saiv_crypto_pilot -v ON_ERROR_STOP=1 -v "pilot_email=$pilotEmail"
```

Confirm one row was updated. Sign in again at the pilot frontend, complete
consent and face enrollment, and open check-in. Confirm the account password
to enroll the signing key. Reload and reopen check-in to confirm the key is
reused. Capture a real location and perform the issued liveness challenge.
The result should say **Browser key verified for this attempt**. The attendance
decision still depends on the face, liveness, and location evidence. Inspect
the same record in Streamlit using the separate staff account.

Run the following in the same terminal to roll back for this student.

```powershell
$disablePilot = $enablePilot.Replace('TRUE', 'FALSE')
$disablePilot | docker compose @pilot exec -T postgres psql -U saiv -d saiv_crypto_pilot -v ON_ERROR_STOP=1 -v "pilot_email=$pilotEmail"
```

Sign in again and open a fresh session. The
legacy workflow should complete with `browser_key_verified: false`. This
retains the key, device registration, and attendance history. A separate
student whose flag was never enabled can also be used for this check.

## Negative proof tests in Chrome

Use only a disposable pilot account and the second session. Keep active
liveness required and leave the session open for at least two minutes. Paste
the following in DevTools Console on the pilot dashboard, substituting the
session UUID from the Network response for the active sessions. The script
keeps the local access token in memory and prints statuses only.

```javascript
await (async () => {
  if (location.origin !== 'http://localhost:5300') throw Error('Open the loopback pilot frontend first');
  const session_id = 'REPLACE_WITH_SECOND_TEST_SESSION_UUID';
  const token = sessionStorage.getItem('saiv_access_token');
  const { id: studentId } = JSON.parse(sessionStorage.getItem('saiv_user'));
  const device_fingerprint = localStorage.getItem('saiv_device_fingerprint_v1');
  if (!token || !device_fingerprint) throw Error('Sign in and enroll this browser first');
  const request = (route, body, proof = {}) => fetch('/api/v1' + route, {
    method: 'POST', headers: { Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json', ...proof }, body,
  });
  const open = indexedDB.open('saiv-browser-keys', 1);
  const db = await new Promise((resolve, reject) => {
    open.onsuccess = () => resolve(open.result); open.onerror = reject;
    open.onupgradeneeded = () => open.transaction.abort();
  });
  const entry = await new Promise((resolve, reject) => {
    const read = db.transaction('signing-keys').objectStore('signing-keys').get([studentId, device_fingerprint]);
    read.onsuccess = () => resolve(read.result); read.onerror = reject;
  });
  db.close();
  if (!entry) throw Error('Enroll this browser first');
  const encode = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const utf8 = new TextEncoder();
  const raw = JSON.stringify({ session_id, device_fingerprint, retry: true });
  const issue = async () => {
    const result = await request('/checkins/browser-challenge', JSON.stringify({ session_id, device_fingerprint }));
    if (result.status !== 201) throw Error('Challenge issuance returned ' + result.status);
    return result.json();
  };
  const proof = async ch => ({
    'X-SAIV-Browser-Challenge': ch.challenge_id,
    'X-SAIV-Browser-Signature': encode(await crypto.subtle.sign(
      { name: 'ECDSA', hash: 'SHA-256' }, entry.privateKey,
      utf8.encode(ch.message + '\n' + encode(await crypto.subtle.digest('SHA-256', utf8.encode(raw)))),
    )),
  });
  const signed = await proof(await issue());
  console.log('Copied UUID without key', (await request('/checkins/', raw)).status); // 403
  console.log('Invalid signature', (await request('/checkins/', raw,
    { ...signed, 'X-SAIV-Browser-Signature': 'A'.repeat(86) })).status); // 403
  console.log('Altered body', (await request('/checkins/', raw + ' ', signed)).status); // 403
  const first = await request('/checkins/', raw, signed);
  console.log('First use', first.status, (await first.json()).status); // 201, flagged because evidence is absent
  console.log('Replay', (await request('/checkins/', raw, signed)).status); // 403
  const expired = await proof(await issue());
  await new Promise(resolve => setTimeout(resolve, 61000));
  console.log('Expired proof', (await request('/checkins/', raw, expired)).status); // 403
})();
```

Keep the tab in the foreground while testing expiry. A `429` means the
existing check-in or challenge budget was exhausted. Wait for the
`Retry-After` window and use another disposable session if needed.

- **Lost key** — Delete only the `saiv-browser-keys` IndexedDB database in
  DevTools Application. Keep the browser UUID, reload, and reopen check-in.
  The recovery form should require the password and bind a replacement key.
- **Cleared site storage** — Use a fresh profile or clear only this pilot
  origin. Sign in as the same student and select the previous browser under
  **Setup option**. Recover with the password and confirm the old registration
  is inactive in Profile & Settings.
- **Revocation** — From the pilot dashboard Console, run the request below.
  Then reopen check-in and confirm the same UUID is refused. Test recovery
  before revocation so both cases can be observed.

```javascript
await (async () => {
  if (location.origin !== 'http://localhost:5300') throw Error('Open the loopback pilot frontend first');
  const headers = { Authorization: 'Bearer ' + sessionStorage.getItem('saiv_access_token') };
  const response = await fetch('/api/v1/devices/my-devices', { headers });
  if (!response.ok) throw Error('Could not read registrations');
  const device = (await response.json()).find(row =>
    row.device_fingerprint === localStorage.getItem('saiv_device_fingerprint_v1'));
  if (!device) throw Error('No matching registration');
  const revoked = await fetch('/api/v1/devices/' + encodeURIComponent(device.id), { method: 'DELETE', headers });
  console.log('Revocation', revoked.status); // 200
})();
```

Physical Android camera and GPS still need a separate check on an HTTPS
origin restricted to authorized testers. Use a tunnel to this isolated
frontend only after reviewing access controls. Its origin gets its own
browser registration and IndexedDB key.
