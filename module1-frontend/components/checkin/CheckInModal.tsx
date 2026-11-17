'use client';

import CheckinResultSummary from './CheckinResultSummary';
import ModalFrame from '../ui/ModalFrame';
import { CheckinCameraPreview, CheckinChallengeStatus, CheckinStepHeading } from './CheckinPresentation';

import { useEffect, useRef, useState } from 'react';
import jsQR from 'jsqr';

import { findSessionCheckin, submitCheckin } from '../../lib/student-api';
import { ClientDataError, SignedRequestRefreshError, parseApiError } from '../../lib/api-error';
import { getSessionVersion, isCurrentSession } from '../../lib/auth';
import { signBrowserChallenge } from '../../lib/browser-key';
import type { BrowserKey } from '../../lib/browser-key';
import { requestBrowserCheckinChallenge } from '../../lib/device-api';
import { checkinWindowReason } from '../../lib/student-policy';
import { markCheckin, pendingCheckin } from '../../lib/checkin-attempt';
import { useActiveLiveness } from '../../hooks/useActiveLiveness';
import { confirmBrowserRegistration } from '../../lib/device-registration';
import DeviceRegistrationGate from './DeviceRegistrationGate';
import ColourEmitter from './ColourEmitter';
import type {
  ActiveSession,
  CheckInResponse,
  CheckInCreateRequest,
  FacePolicy,
  User,
} from '../../types/api';

interface CheckInModalProps {
  user: User;
  session: ActiveSession;
  policy: FacePolicy | null;
  // True when this session already has an unsettled check-in, so the very
  // first submission here must go out as a retry.
  priorAttempt?: boolean;
  onClose: () => void;
  onCompleted: (record: CheckInResponse) => Promise<void>;
  onUnauthorized: () => void;
}

type CameraStatus = 'idle' | 'requesting' | 'ready' | 'error';
type LocationStatus = 'idle' | 'requesting' | 'ready' | 'error';
type QrScanStatus = 'idle' | 'scanning' | 'success' | 'error';

// An advisory UI threshold, not the backend risk threshold.
const LOCATION_ACCURACY_GUIDANCE_METERS = 100;
// Frontend freshness guidance; the API does not accept an evidence timestamp.
const LOCATION_MAX_AGE_MS = 60000;
const LOCATION_CLOCK_SKEW_MS = 1000;
// Consecutive refusals of an over-age fix before the platform is treated as
// stuck and the reading is accepted with disclosure. See requestLocation.
const LOCATION_STALE_ATTEMPTS = 3;
const QR_MAX_AGE_MS = 30000;

interface LocationReading {
  latitude: number;
  longitude: number;
  accuracy: number;
  capturedAt: number;
  // How far behind receipt the platform's own acquisition time was, for a
  // reading accepted despite being over age. 0 for an ordinary fresh fix.
  staleByMs: number;
}

/** Plain-language age for the stale-fix disclosure. */
function describeAge(milliseconds: number): string {
  const minutes = Math.floor(milliseconds / 60000);
  return minutes < 2 ? 'over a minute' : `about ${minutes} minutes`;
}

export default function CheckInModal(props: CheckInModalProps) {
  // Remount all camera, location, QR and challenge state on a session switch.
  return <CheckInSession key={props.session.id} {...props} />;
}

function CheckInSession(props: CheckInModalProps) {
  const [recovering, setRecovering] = useState(() => pendingCheckin(props.session.id));
  if (props.user?.role !== 'student' || props.user.is_active !== true) {
    return <div role="dialog" aria-modal="true" aria-label="Check-in unavailable" className="fixed inset-0 z-[60] bg-white p-6">
      <p role="alert">Check-in requires a validated active student account.</p>
      <button type="button" onClick={props.onClose}>Close</button>
    </div>;
  }
  // History reconciliation must remain available even if device registration
  // is unavailable. It never captures evidence or sends a new attendance POST.
  if (recovering || !props.policy) return <CheckInAttempt {...props} deviceFingerprint={null}
    onRestart={() => setRecovering(false)} onDeviceFailure={() => {}} />;
  if (!props.user.camera_consent || !props.user.geolocation_consent || (props.policy.faceMatchRequired && !props.user.face_enrolled)) {
    return <UnavailableCheckIn message="Complete the required consent and face enrollment before checking in." onClose={props.onClose} />;
  }
  const windowError = checkinWindowReason(props.session);
  if (windowError) return <UnavailableCheckIn message={windowError} onClose={props.onClose} />;
  return <DeviceRegistrationGate user={props.user} onClose={props.onClose}>
    {(identifier, invalidate, browserKey) => {
      const latestWindowError = checkinWindowReason(props.session);
      return latestWindowError ? <UnavailableCheckIn message={latestWindowError} onClose={props.onClose} /> :
        <CheckInAttempt {...props} deviceFingerprint={identifier} browserKey={browserKey}
          onRestart={() => {}} onDeviceFailure={invalidate} />;
    }}
  </DeviceRegistrationGate>;
}

function UnavailableCheckIn({ message, onClose }: { message: string; onClose: () => void }) {
  return <div role="dialog" aria-modal="true" aria-label="Check-in unavailable" className="fixed inset-0 z-[60] bg-white p-6">
    <p role="alert">{message}</p><button type="button" onClick={onClose}>Close</button>
  </div>;
}

function CheckInAttempt({
  user,
  session,
  policy,
  priorAttempt = false,
  onClose,
  onCompleted,
  onUnauthorized,
  deviceFingerprint,
  browserKey,
  onDeviceFailure,
  onRestart,
}: CheckInModalProps & { deviceFingerprint: string | null; browserKey?: BrowserKey; onDeviceFailure: (error: unknown) => void; onRestart: () => void }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mountedRef = useRef(true);
  const qrScanActiveRef = useRef(false);
  const qrAnimationRef = useRef<number | null>(null);
  const qrCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const cameraGeneration = useRef(0);
  const locationGeneration = useRef(0);
  const staleFixRefusals = useRef(0);
  const submittingRef = useRef(false);
  const reconcileRef = useRef(false);
  const networkController = useRef<AbortController | null>(null);
  const qrCapturedAt = useRef<number | null>(null);

  const [cameraStatus, setCameraStatus] =
    useState<CameraStatus>('idle');
  const [locationStatus, setLocationStatus] =
    useState<LocationStatus>('idle');
  const [location, setLocation] =
    useState<LocationReading | null>(null);
  const [qrCodeSecret, setQrCodeSecret] = useState('');
  const [qrScanStatus, setQrScanStatus] = useState<QrScanStatus>('idle');
  const [qrScanMessage, setQrScanMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [refreshWarning, setRefreshWarning] = useState('');
  const [result, setResult] = useState<CheckInResponse | null>(null);
  const [uncertain, setUncertain] = useState(() => pendingCheckin(session.id));
  // A submission that already produced a record must be sent as a retry, or
  // the backend refuses it as a duplicate. Set from both paths that can
  // surface a prior record: our own submit, and reconciliation on reopen.
  const hasPriorAttempt = useRef(priorAttempt);
  // Server-dictated wait between attempts, from the 429's Retry-After.
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [clockTick, setClockTick] = useState(0);
  const [reconciling, setReconciling] = useState(false);
  const [reconciledEmpty, setReconciledEmpty] = useState(false);

  // Re-render once a second while a cooldown is running so the countdown
  // advances and the button re-enables on its own.
  useEffect(() => {
    if (cooldownUntil <= Date.now()) return;
    const timer = setInterval(() => setClockTick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [cooldownUntil, clockTick]);
  const cooldownRemaining = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));

  const requiresImage = policy?.imageRequired ?? true;
  const requiresActiveLiveness = session.require_liveness_check;
  const liveness = useActiveLiveness(session.id, getCurrentFrame, onUnauthorized);
  const resetLiveness = liveness.reset;
  const captureBusy = liveness.phase === 'capturing' || liveness.phase === 'requesting';

  useEffect(() => {
    mountedRef.current = true;
    // Keep the stable counter objects for cleanup, not a snapshot of their values.
    const cameraRequests = cameraGeneration;
    const locationRequests = locationGeneration;
    return () => {
      mountedRef.current = false;
      cameraRequests.current++;
      locationRequests.current++;
      networkController.current?.abort();
      qrScanActiveRef.current = false;
      if (qrAnimationRef.current !== null) {
        cancelAnimationFrame(qrAnimationRef.current);
        qrAnimationRef.current = null;
      }
      qrCanvasRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const interrupt = () => {
      if (document.visibilityState !== 'hidden') return;
      resetLiveness('Capture interrupted while the tab was hidden. Open the camera and request a new challenge.');
      cameraGeneration.current++;
      locationGeneration.current++;
      qrScanActiveRef.current = false;
      if (qrAnimationRef.current !== null) cancelAnimationFrame(qrAnimationRef.current);
      qrAnimationRef.current = null; qrCanvasRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop()); streamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setCameraStatus('idle'); setLocation(null); setLocationStatus('idle');
      setQrCodeSecret(''); qrCapturedAt.current = null; setQrScanStatus('idle');
    };
    document.addEventListener?.('visibilitychange', interrupt);
    return () => document.removeEventListener?.('visibilitychange', interrupt);
  }, [resetLiveness]);

  function stopQrScan() {
    qrScanActiveRef.current = false;
    if (qrAnimationRef.current !== null) {
      cancelAnimationFrame(qrAnimationRef.current);
      qrAnimationRef.current = null;
    }
    setQrScanStatus((previous) => previous === 'scanning' ? 'idle' : previous);
  }

  function stopCamera() {
    cameraGeneration.current++;
    stopQrScan();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }

  function closeModal() {
    if (submittingRef.current) return;
    liveness.reset();
    stopCamera();
    onClose();
  }

  function requestLocation() {
    if (!deviceFingerprint || uncertain || submittingRef.current) return;
    const windowError = checkinWindowReason(session);
    if (windowError) { setError(windowError); return; }
    const version = ++locationGeneration.current;
    setError('');
    setLocationStatus('requesting');
    setLocation(null);

    if (!navigator.geolocation) {
      setLocationStatus('error');
      setError('Geolocation is not available in this browser.');
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (!mountedRef.current || version !== locationGeneration.current) return;
        if (![position.coords.latitude, position.coords.longitude, position.coords.accuracy].every(Number.isFinite) ||
            Math.abs(position.coords.latitude) > 90 || Math.abs(position.coords.longitude) > 180 || position.coords.accuracy < 0) {
          setLocationStatus('error'); setError('Location data is unavailable. Refresh your location.'); return;
        }
        const receivedAt = Date.now();
        if (!Number.isFinite(position.timestamp) || position.timestamp <= 0 ||
            position.timestamp > receivedAt + LOCATION_CLOCK_SKEW_MS) {
          setLocationStatus('error');
          setError('Location timing could not be verified. Refresh your location and check your device clock.'); return;
        }
        // An over-age fix is refused, but only while refusing can still
        // achieve something. Asking again is the only remedy this code has,
        // and there are two common cases where it cannot work: macOS and iOS
        // answer maximumAge: 0 with the same cached fix every time, and a
        // device whose clock runs behind makes every fix look old. Refusing
        // forever then locks the student out of attendance altogether, with
        // advice ("enable precise location") that cannot change a timestamp
        // the platform has already chosen. So after LOCATION_STALE_ATTEMPTS
        // consecutive refusals the reading is taken and the student is told
        // plainly that it may be out of date.
        const staleBy = receivedAt - position.timestamp;
        const stale = staleBy > LOCATION_MAX_AGE_MS;
        if (stale) {
          staleFixRefusals.current += 1;
          if (staleFixRefusals.current < LOCATION_STALE_ATTEMPTS) {
            setLocationStatus('error');
            setError('Your location reading is more than a minute old. Refresh your location or enable precise location in your device settings.'); return;
          }
          // Deliberately not reset: the platform has shown it is stuck, so a
          // later refresh that returns the same cached fix is accepted at
          // once rather than discarding a reading the student already has.
        } else {
          staleFixRefusals.current = 0;
        }
        setLocation({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
          // Preserve acquisition age even if the platform returns a cached fix.
          // Small clock skew must not grant extra time beyond receipt. An
          // accepted stale fix is timed at receipt instead: it has already
          // been judged on its age once, and holding it to the acquisition
          // time would fail the freshness gate before submission and
          // reinstate the dead end this branch exists to avoid.
          capturedAt: stale ? receivedAt : Math.min(position.timestamp, receivedAt),
          staleByMs: stale ? staleBy : 0,
        });
        setLocationStatus('ready');
      },
      (geoError) => {
        if (!mountedRef.current || version !== locationGeneration.current) return;
        setLocationStatus('error');
        if (geoError.code === geoError.PERMISSION_DENIED) {
          setError('Location permission was denied. Allow location access and try again.');
        } else if (geoError.code === geoError.TIMEOUT) {
          setError('Location capture timed out. Please try refreshing your location.');
        } else if (geoError.code === geoError.POSITION_UNAVAILABLE) {
          setError('Your device could not determine its location. Check device location settings and try again.');
        } else {
          setError('Unable to get your current location. Please try again.');
        }
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  }

  async function startCamera() {
    if (!deviceFingerprint || uncertain || cameraStatus === 'requesting' || submittingRef.current) return;
    const windowError = checkinWindowReason(session);
    if (windowError) { setError(windowError); return; }
    stopCamera(); liveness.reset();
    const version = cameraGeneration.current;
    setError('');
    setCameraStatus('requesting');
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new ClientDataError('Camera is not available in this browser.');
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: 'user',
        },
        audio: false,
      });
      if (!mountedRef.current || !videoRef.current || version !== cameraGeneration.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      stream.getTracks().forEach((track) => track.addEventListener?.('ended', () => {
        if (!mountedRef.current || streamRef.current !== stream) return;
        liveness.reset('The camera became unavailable. Reopen it and request a new challenge.');
        stopCamera(); setCameraStatus('error');
      }, { once: true }));
      videoRef.current.srcObject = stream;
      try {
        await videoRef.current.play();
      } catch {
        // Most browsers honor the autoPlay attribute.
      }
      if (mountedRef.current && version === cameraGeneration.current) setCameraStatus('ready');
    } catch (err) {
      if (!mountedRef.current || version !== cameraGeneration.current) return;
      stopCamera();
      if (!mountedRef.current) return;
      setCameraStatus('error');
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        setError('Camera permission was denied. Allow camera access and try again.');
      } else if (err instanceof DOMException && err.name === 'NotFoundError') {
        setError('No camera was found on this device.');
      } else {
        setError(err instanceof Error ? err.message : 'Unable to open the camera.');
      }
    }
  }

  function readyVideo(): HTMLVideoElement {
    const video = videoRef.current;
    if (
      !video || !streamRef.current ||
      video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA ||
      !video.videoWidth || !video.videoHeight
    ) {
      throw new ClientDataError('The camera is not ready. Please try again.');
    }
    return video;
  }

  function getCurrentFrame(): string {
    const video = readyVideo();
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new ClientDataError('Unable to capture the camera frame.');

    try {
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const image = canvas.toDataURL('image/jpeg', 0.9).split(',')[1];
      if (!image) throw new ClientDataError('Unable to encode the camera image.');
      return image;
    } catch (failure) {
      if (failure instanceof ClientDataError) throw failure;
      throw new ClientDataError('Camera capture was interrupted. Close and reopen the camera, then try a new challenge.');
    } finally {
      context.clearRect(0, 0, canvas.width, canvas.height);
      canvas.width = 0;
      canvas.height = 0;
    }
  }

  // Decode a raw short-lived session secret from a QR shown by the instructor.
  // The backend is responsible for verifying it against the session and expiry.
  function startQrScan() {
    if (captureBusy || submittingRef.current || uncertain) return;
    const video = videoRef.current;
    if (cameraStatus !== 'ready' || !video || !streamRef.current) {
      setQrScanStatus('error');
      setQrScanMessage('Open the camera before scanning a QR code.');
      return;
    }

    stopQrScan();
    setQrCodeSecret('');
    qrCapturedAt.current = null;
    setQrScanStatus('scanning');
    setQrScanMessage('Point the instructor QR code toward your camera.');
    qrScanActiveRef.current = true;

    let lastDecodeAt = 0;
    const scanStart = performance.now();
    const scanFrame = (timestamp: number) => {
      if (!qrScanActiveRef.current || !mountedRef.current) return;

      if (timestamp - scanStart > 20000) {
        stopQrScan();
        setQrScanStatus('error');
        setQrScanMessage('No QR code found. Try again or enter the code manually.');
        return;
      }

      if (timestamp - lastDecodeAt >= 200 &&
          video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
          video.videoWidth > 0 && video.videoHeight > 0) {
        lastDecodeAt = timestamp;
        const canvas = qrCanvasRef.current ?? document.createElement('canvas');
        qrCanvasRef.current = canvas;

        const scale = Math.min(1, 720 / video.videoWidth);
        canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
        canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
        const context = canvas.getContext('2d', { willReadFrequently: true });

        if (context) {
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          const frame = context.getImageData(0, 0, canvas.width, canvas.height);
          const decoded = jsQR(frame.data, frame.width, frame.height, {
            inversionAttempts: 'attemptBoth',
          });

          if (decoded) {
            stopQrScan();
            const secret = decoded.data.trim();
            // Phong's API currently issues URL-safe random secrets, not URLs.
            if (/^[A-Za-z0-9_-]{16,64}$/.test(secret)) {
              setQrCodeSecret(secret);
              qrCapturedAt.current = Date.now();
              setQrScanStatus('success');
              setQrScanMessage(
                'QR code scanned. Look back at the camera and submit promptly; codes expire 30 seconds after the instructor issues them.'
              );
            } else {
              setQrScanStatus('error');
              setQrScanMessage(
                'QR code read, but it does not contain a supported SAIV session secret. Try the instructor code or enter it manually.'
              );
            }
            context.clearRect(0, 0, canvas.width, canvas.height);
            canvas.width = 0; canvas.height = 0; qrCanvasRef.current = null;
            return;
          }
          context.clearRect(0, 0, canvas.width, canvas.height);
        }
      }

      qrAnimationRef.current = requestAnimationFrame(scanFrame);
    };

    qrAnimationRef.current = requestAnimationFrame(scanFrame);
  }

  async function handleSubmit() {
    if (!deviceFingerprint || !location || submittingRef.current || uncertain || pendingCheckin(session.id) || qrScanActiveRef.current || captureBusy) return;
    const windowError = checkinWindowReason(session);
    if (!policy || windowError) {
      liveness.reset(); stopCamera(); setCameraStatus('idle');
      setError(windowError ?? 'Verification policy is unavailable. Refresh the dashboard.');
      return;
    }
    if (!freshEvidence()) return;
    setError('');
    submittingRef.current = true;
    setSubmitting(true);

    let request: CheckInCreateRequest | undefined;
    let sent = false;
    const version = getSessionVersion();
    try {
      // Re-read status without registering again: a device may have been revoked
      // while the student was capturing evidence. The server remains authoritative.
      try {
        const confirmed = await confirmBrowserRegistration(user, deviceFingerprint);
        if (browserKey && (confirmed.device.browser_key_id !== browserKey.keyId || confirmed.device.browser_key_usable === false)) {
          throw new ClientDataError('The registered browser key changed or expired. Check registration again.');
        }
      }
      catch (failure) { if (mountedRef.current) onDeviceFailure(failure); throw failure; }
      const controller = new AbortController();
      networkController.current = controller;
      const browserChallenge = browserKey
        ? await requestBrowserCheckinChallenge(session.id, deviceFingerprint, controller.signal)
        : undefined;
      if (!mountedRef.current) return;
      const latestWindowError = checkinWindowReason(session);
      if (latestWindowError) throw new ClientDataError(latestWindowError);
      if (!freshEvidence()) return;
      request = {
        session_id: session.id,
        latitude: location.latitude,
        longitude: location.longitude,
        location_accuracy_meters: location.accuracy,
        device_fingerprint: deviceFingerprint,
      };
      if (hasPriorAttempt.current) request.retry = true;

      if (requiresActiveLiveness) {
        Object.assign(request, liveness.takeEvidence());
      } else if (requiresImage) {
        request.image = getCurrentFrame();
      }
      stopCamera(); setCameraStatus('idle');

      if (qrCodeSecret.trim()) {
        request.qr_code_secret = qrCodeSecret.trim();
      }

      let proof;
      if (browserKey && browserChallenge) {
        const body = JSON.stringify(request);
        const signature = await signBrowserChallenge(browserKey, browserChallenge, body);
        proof = { body, challengeId: browserChallenge.challenge_id, signature };
      }
      if (!mountedRef.current || !isCurrentSession(version)) return;
      markCheckin(session.id, true); sent = true;
      const attendance = await submitCheckin(request, proof);
      markCheckin(session.id, false);
      if (!mountedRef.current) return;
      hasPriorAttempt.current = true;
      setCooldownUntil(0);
      setResult(attendance);

      try {
        await onCompleted(attendance);
      } catch {
        setRefreshWarning(
          'Check-in was submitted, but the attendance history did not refresh. Reload the dashboard to check your record.'
        );
      }
    } catch (err) {
      if (!mountedRef.current) return;
      if (err instanceof SignedRequestRefreshError) {
        if (sent) markCheckin(session.id, false);
        setError(err.message);
        return;
      }
      const failure = parseApiError(err);
      // A failed transport (including an interrupted tab/account) cannot tell
      // us whether the server committed. Never replay this POST automatically.
      if (sent && (!failure.status || failure.status >= 500 || failure.status === 408)) {
        setUncertain(true); setReconciledEmpty(false);
        setError('Submission outcome is unknown. Check attendance status before starting another attempt.');
        return;
      }
      if (sent) markCheckin(session.id, false);
      if (failure.kind === 'cancelled') return;
      if (failure.status === 401) { onUnauthorized(); return; }
      if (failure.status === 429) {
        // Nothing was recorded, so this stays a retry. The wait grows with
        // each attempt, so trust the server's number rather than our own.
        const wait = failure.retryAfterSeconds ?? 30;
        if (sent) hasPriorAttempt.current = true;
        setCooldownUntil(Date.now() + wait * 1000);
        setError(`Too soon. Wait ${wait} seconds before trying again.`);
        return;
      }
      setError(failure.message);
    } finally {
      if (request?.challenge_frames) request.challenge_frames.length = 0;
      if (request) { delete request.image; delete request.challenge_frames; }
      liveness.reset();
      if (mountedRef.current) { setQrCodeSecret(''); qrCapturedAt.current = null; setQrScanStatus('idle'); }
      submittingRef.current = false;
      if (mountedRef.current) setSubmitting(false);
    }
  }

  function freshEvidence(): boolean {
    if (!location || Date.now() - location.capturedAt > LOCATION_MAX_AGE_MS || location.capturedAt > Date.now() + LOCATION_CLOCK_SKEW_MS) {
      setLocation(null); setLocationStatus('idle');
      setError('Your location is missing or more than a minute old. Capture your current location again.'); return false;
    }
    if (qrCodeSecret.trim() && (qrCapturedAt.current === null || Date.now() - qrCapturedAt.current >= QR_MAX_AGE_MS)) {
      setQrCodeSecret(''); qrCapturedAt.current = null; setQrScanStatus('idle');
      setError('This QR code was collected too long ago. Scan or enter a newly issued code.'); return false;
    }
    return true;
  }

  function beginChallenge() {
    if (!deviceFingerprint || !policy || !requiresActiveLiveness || cameraStatus !== 'ready' || qrScanActiveRef.current ||
        submittingRef.current || uncertain || pendingCheckin(session.id) || captureBusy) return;
    const windowError = checkinWindowReason(session);
    if (windowError) { setError(windowError); return; }
    try { readyVideo(); } catch { setError('Wait for the camera preview before requesting a challenge.'); return; }
    if (!freshEvidence()) return;
    setError(''); void liveness.request();
  }

  function beginCapture() {
    if (qrScanActiveRef.current || submittingRef.current || uncertain || cameraStatus !== 'ready') return;
    const windowError = checkinWindowReason(session);
    if (!policy || windowError) {
      liveness.reset(); stopCamera(); setCameraStatus('idle');
      setError(windowError ?? 'Verification policy is unavailable. Refresh the dashboard.'); return;
    }
    if (freshEvidence()) void liveness.capture();
  }

  async function reconcileSubmission() {
    if (reconcileRef.current) return;
    reconcileRef.current = true; setReconciling(true); setError(''); setReconciledEmpty(false);
    const controller = new AbortController(); networkController.current = controller;
    try {
      const existing = await findSessionCheckin(session.id, controller.signal);
      if (!mountedRef.current) return;
      if (existing) {
        markCheckin(session.id, false); setUncertain(false);
        hasPriorAttempt.current = true; setResult(existing);
        try { await onCompleted(existing); } catch { setRefreshWarning('The record was found, but history did not refresh. Refresh the dashboard.'); }
      } else setReconciledEmpty(true);
    } catch (failure) {
      if (!mountedRef.current) return;
      const parsed = parseApiError(failure);
      if (parsed.status === 401) onUnauthorized();
      else setError(parsed.message);
    } finally { reconcileRef.current = false; if (mountedRef.current) setReconciling(false); }
  }

  if (!policy) {
    return <div role="dialog" aria-modal="true" aria-label="Check-in unavailable" className="fixed inset-0 z-[60] bg-white p-6">
      <p role="alert">Verification policy is unavailable. Refresh the dashboard before checking in.</p>
      <button type="button" onClick={closeModal}>Close</button>
    </div>;
  }

  return (
    <>
    {/* Outside the dialog, and deliberately. During a colour-reflection
        challenge the screen IS the light source, so the colour has to cover
        the viewport rather than sit inside a modal panel: the quantity the
        backend measures is how much light reached the face. The camera keeps
        running underneath -- this covers the UI, it does not detach the
        video element. */}
    {liveness.colour && <ColourEmitter colour={liveness.colour} progress={liveness.progress} />}
    <ModalFrame labelledBy="checkin-dialog-title" onClose={closeModal} busy={submitting} panelClassName="checkin-dialog">
        <div className="checkin-dialog-heading">
          <div>
            <h2 id="checkin-dialog-title" className="checkin-dialog-title">
              Attendance Check-in
            </h2>
            <p className="checkin-session-name">{session.name}</p>
          </div>
          <button
            type="button"
            aria-label="Close check-in"
            onClick={closeModal}
            disabled={submitting}
            className="checkin-close"
          >
            ×
          </button>
        </div>

        {!result ? (
          <>
            <div className="checkin-intro">
              {deviceFingerprint && <span className="checkin-step-status is-complete">{browserKey ? 'Browser key ready' : 'Browser registration confirmed'}</span>}
              <details className="checkin-privacy"><summary>Privacy &amp; evidence</summary>
                <p>Location checks attendance eligibility. Camera images are used only when this session requires face matching or liveness. Images are not saved to browser storage.</p>
              </details>
            </div>

            <section className="checkin-step" aria-label="Location">
              <CheckinStepHeading number={1} icon="location" title="Location"
                description="Confirm your location for attendance."
                status={locationStatus === 'ready' && location ? 'Captured' : locationStatus === 'requesting' ? 'Locating…' : 'Required'}
                complete={locationStatus === 'ready' && !!location} />
              {locationStatus === 'ready' && location ? (
                <div className="checkin-location-result">
                  <p
                    role="status"
                    className={`text-sm ${
                      location.accuracy > LOCATION_ACCURACY_GUIDANCE_METERS
                        ? 'text-amber-800'
                        : 'text-green-700'
                    }`}
                  >
                    Location captured · Accuracy ±{Math.round(location.accuracy)} m
                  </p>
                  {location.staleByMs > 0 && (
                    <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
                      Your device reported this position as {describeAge(location.staleByMs)} ago
                      and would not produce a newer one, so it was accepted to let you
                      continue. If you have moved since, refresh your location before
                      submitting; otherwise the venue check may not match where you are
                      now and this check-in can be flagged for review.
                    </p>
                  )}
                  {location.accuracy > LOCATION_ACCURACY_GUIDANCE_METERS && (
                    <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
                      Approximate reading. Refresh or enable precise location for a better fix.
                      The backend decides whether you are within the attendance area.
                    </p>
                  )}
                  <p className="checkin-hint">
                    Location accuracy is an estimate, not your distance from the venue.
                  </p>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={requestLocation}
                  disabled={locationStatus === 'requesting' || submitting || captureBusy || uncertain}
                  className="btn-secondary mt-3"
                >
                  {locationStatus === 'requesting' ? 'Getting location...' : 'Get Current Location'}
                </button>
              )}
              {location && (
                <button
                  type="button"
                  onClick={requestLocation}
                  disabled={submitting || captureBusy || uncertain}
                  className="checkin-text-action"
                >
                  Refresh location
                </button>
              )}
            </section>

            {requiresImage && (
              <section className="checkin-step" aria-label="Face verification">
                <CheckinStepHeading number={2} icon="face" title="Face verification"
                  description="Keep your face visible in good lighting."
                  status={requiresActiveLiveness && liveness.phase === 'captured' ? 'Captured' : cameraStatus === 'ready' ? 'Camera ready' : 'Required'}
                  complete={requiresActiveLiveness && liveness.phase === 'captured'} />
                {session.require_liveness_check && cameraStatus !== 'ready' && (
                  <p className="checkin-hint">Get location and any instructor code first. Then open the camera.</p>
                )}
                {cameraStatus !== 'ready' && (
                  <button
                    type="button"
                    onClick={startCamera}
                    disabled={cameraStatus === 'requesting' || submitting || uncertain}
                    className="btn-secondary mt-3"
                  >
                    {cameraStatus === 'requesting' ? 'Opening Camera...' : 'Open Camera'}
                  </button>
                )}
                {requiresActiveLiveness && <CheckinChallengeStatus challenge={liveness.challenge}
                  phase={liveness.phase} remaining={liveness.remaining} progress={liveness.progress} />}
                <CheckinCameraPreview ref={videoRef} ready={cameraStatus === 'ready'} />
                {cameraStatus === 'ready' && (
                  <div className="checkin-camera-toolbar">
                    <p>Live camera</p>
                    <button type="button" onClick={() => {
                      liveness.reset(); stopCamera(); setCameraStatus('idle');
                    }} disabled={captureBusy || submitting} className="checkin-text-action">Close camera</button>
                  </div>
                )}
                {requiresActiveLiveness && (
                  <div className="checkin-challenge-actions">
                    {liveness.phase === 'ready' && <button type="button" onClick={beginCapture}
                      disabled={qrScanStatus === 'scanning' || cameraStatus !== 'ready' || submitting}
                      className="btn-primary">Capture challenge</button>}
                    {!captureBusy && <button type="button" onClick={beginChallenge}
                      disabled={cameraStatus !== 'ready' || !location || submitting || uncertain || qrScanStatus === 'scanning'}
                      className="btn-secondary">
                      {liveness.challenge ? 'Request a new challenge' : 'Request liveness challenge'}
                    </button>}
                    {(captureBusy || liveness.challenge) && <button type="button" onClick={() => {
                      liveness.reset('Challenge cancelled. Open the camera and request a new challenge when ready.');
                      stopCamera(); setCameraStatus('idle');
                    }} disabled={submitting} className="checkin-text-action">Cancel challenge</button>}
                    {liveness.error && <p role="alert" className="text-sm text-red-700">{liveness.error}</p>}
                  </div>
                )}
              </section>
            )}

            <section className="checkin-step" aria-label="QR code">
              <CheckinStepHeading number={requiresImage ? 3 : 2} icon="camera" title="QR code"
                description="Scan or enter the instructor’s code, if required."
                status={qrCodeSecret.trim() ? 'Code entered' : 'Optional'} complete={!!qrCodeSecret.trim()} />
              <p className="checkin-hint">Codes expire 30 seconds after issue.</p>

              {!requiresImage && (
                <>
                  {cameraStatus !== 'ready' && (
                    <button
                      type="button"
                      onClick={startCamera}
                      disabled={submitting || uncertain || cameraStatus === 'requesting'}
                      className="btn-secondary mt-3"
                    >
                      {cameraStatus === 'requesting' ? 'Opening Camera...' : 'Open Camera to Scan QR'}
                    </button>
                  )}
                  <CheckinCameraPreview ref={videoRef} ready={cameraStatus === 'ready'} />
                </>
              )}

              {cameraStatus === 'ready' ? (
                <button
                  type="button"
                  onClick={qrScanStatus === 'scanning' ? stopQrScan : startQrScan}
                  disabled={submitting || captureBusy || uncertain}
                  className="btn-secondary mt-3"
                >
                  {qrScanStatus === 'scanning' ? 'Stop QR Scanning' : 'Scan QR Code'}
                </button>
              ) : requiresImage ? (
                <p className="checkin-hint">
                  Open the face-verification camera above to enable QR scanning.
                </p>
              ) : null}

              {qrScanMessage && (
                <p
                  role="status"
                  className={`mt-2 text-sm ${qrScanStatus === 'success'
                    ? 'text-green-700'
                    : qrScanStatus === 'error' ? 'text-amber-800' : 'text-blue-700'}`}
                >
                  {qrScanMessage}
                </p>
              )}

              <label htmlFor="checkin-qr-code" className="sr-only">Instructor session code (optional)</label>
              <input
                id="checkin-qr-code"
                type={qrScanStatus === 'success' ? 'password' : 'text'}
                value={qrCodeSecret}
                onChange={(event) => {
                  setQrCodeSecret(event.target.value);
                  qrCapturedAt.current = event.target.value.trim() ? Date.now() : null;
                  setQrScanStatus('idle');
                  setQrScanMessage('');
                }}
                disabled={submitting || captureBusy || uncertain || qrScanStatus === 'scanning'}
                placeholder="Optional session code"
                autoComplete="off"
                className="checkin-code-input"
              />
            </section>

            {error && (
              <p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">
                {error}
              </p>
            )}

            {uncertain && <div role="status" className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
              <p>A previous submission may have been recorded. Its images have been discarded. Check attendance status before trying again.</p>
              <button type="button" onClick={reconcileSubmission} disabled={reconciling}
                className="mt-2 underline disabled:opacity-50">{reconciling ? 'Checking attendance…' : 'Check attendance status'}</button>
              {reconciledEmpty && <>
                <p className="mt-2">No record is visible yet. The server may still be processing. Wait and check again before starting a fresh attempt.</p>
                <button type="button" onClick={() => {
                  markCheckin(session.id, false); setUncertain(false); setReconciledEmpty(false);
                  liveness.reset(); stopCamera(); setCameraStatus('idle'); setLocation(null); setLocationStatus('idle');
                  setQrCodeSecret(''); qrCapturedAt.current = null; setError('');
                  onRestart();
                }} className="mt-2 underline">Start a fresh attempt</button>
              </>}
            </div>}

            <button
              type="button"
              onClick={handleSubmit}
              disabled={!deviceFingerprint || submitting || uncertain || captureBusy || qrScanStatus === 'scanning' || !location ||
                cooldownRemaining > 0 ||
                (requiresImage && cameraStatus !== 'ready') || (requiresActiveLiveness && liveness.phase !== 'captured')}
              className="btn-primary checkin-submit"
            >
              {submitting ? 'Submitting Attendance...'
                : cooldownRemaining > 0 ? `Try again in ${cooldownRemaining}s`
                : hasPriorAttempt.current ? 'Try Check-in Again' : 'Submit Check-in'}
            </button>
          </>
        ) : (
          <div className="mt-5 rounded-lg border border-gray-200 p-4">
            <CheckinResultSummary result={result} activeChallenge={requiresActiveLiveness} />
            {/* An automatic rejection is retryable; one a reviewer made is not,
                and is appealed instead. result.reviewed_by_id tells them apart. */}
            {!result.reviewed_by_id && result.status !== 'approved' && result.status !== 'appealed' && (
              <div className="mt-3 rounded-md bg-gray-50 p-3">
                <p className="text-sm text-gray-700">
                  If a dark room, a stale location or a missed blink caused this, you
                  can verify again. Your attendance record is updated in place rather
                  than duplicated, and the wait grows with each attempt.
                </p>
                <button type="button" onClick={() => {
                  // Evidence is deliberately NOT carried over: location must be
                  // re-captured to pass the freshness check and a challenge is
                  // single-use, so the next attempt starts from a clean capture.
                  setResult(null); setError(''); setRefreshWarning('');
                  setLocation(null); setLocationStatus('idle');
                  setQrCodeSecret(''); qrCapturedAt.current = null; setQrScanStatus('idle');
                  liveness.reset();
                }} className="mt-2 text-sm font-medium text-blue-700 underline">
                  Verify again
                </button>
              </div>
            )}
            {result.status === 'rejected' && <p className="mt-2 text-sm text-red-700">
              Attendance was not approved.{result.reviewed_by_id
                ? ' Your instructor decided this; appeal it if you disagree.'
                : ' You can verify again, or appeal if you believe the result is wrong.'}
            </p>}
            {refreshWarning && (
              <p className="mt-3 text-sm text-amber-700">{refreshWarning}</p>
            )}
          </div>
        )}

        <button
          type="button"
          onClick={closeModal}
          disabled={submitting}
          className="btn-secondary checkin-dismiss"
        >
          {result ? 'Done' : 'Cancel'}
        </button>
    </ModalFrame>
    </>
  );
}
