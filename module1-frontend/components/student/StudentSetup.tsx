'use client';

import { useEffect, useRef, useState } from 'react';
import ModalFrame from '../ui/ModalFrame';
import StudentIcon from '../ui/StudentIcon';
import StudentSectionHeading from '../ui/StudentSectionHeading';
import { clearAuthSession } from '../../lib/auth';
import { parseApiError } from '../../lib/api-error';
import { getCurrentUser, updateConsent } from '../../lib/auth-api';
import FaceEnrollmentModal from '../face/FaceEnrollmentModal';
import { faceReenrollLabel, faceReenrollState } from '../../lib/face-reenroll';
import { myFaceReenrollRequests, requestFaceReenrollment } from '../../lib/student-api';
import type { FaceReEnrollRequestRecord, User } from '../../types/api';

type CameraStatus = 'idle' | 'requesting' | 'ready' | 'error';
type LocationStatus = CameraStatus;
interface LocationReading { latitude: number; longitude: number; accuracy: number; }

// The established setup workflow is shared; permissions are requested only on explicit actions.
export default function StudentSetup({ user, onUserUpdate }: {
    user: User; onUserUpdate: (user: User) => void;
}) {
    const mountedRef = useRef(true);
    const cameraRequestRef = useRef(0);

    function applyUserUpdate(updatedUser: User) {
        if (!mountedRef.current) return;
        if (updatedUser.role !== 'student' || updatedUser.is_active !== true) {
            clearAuthSession();
            return;
        }
        onUserUpdate(updatedUser);
    }

    const [
        consentLoading,
        setConsentLoading,
    ] = useState<
        'camera' | 'location' | null
    >(null);

    const [
        consentError,
        setConsentError,
    ] = useState('');

    // Camera
    const [
        cameraModalOpen,
        setCameraModalOpen,
    ] = useState(false);

    const [
        cameraStatus,
        setCameraStatus,
    ] = useState<CameraStatus>('idle');

    const [
        cameraError,
        setCameraError,
    ] = useState('');

    const videoRef =
        useRef<HTMLVideoElement | null>(null);

    const cameraStreamRef =
        useRef<MediaStream | null>(null);

    // Face enrollment: null when closed, otherwise what the modal will do.
    const [faceEnrollmentMode, setFaceEnrollmentMode] =
        useState<'enroll' | 're-enroll' | null>(null);

    // Replacing a template is no longer the student's own call: they ask, and
    // an admin decides. These hold that conversation's state.
    const [reenrollRequests, setReenrollRequests] =
        useState<FaceReEnrollRequestRecord[] | null>(null);
    const [reenrollBusy, setReenrollBusy] = useState(false);
    const [reenrollError, setReenrollError] = useState('');
    // Why the student wants a new template. Required, so the admin deciding
    // the request always has something to go on; the backend caps it at 500.
    const [reenrollReason, setReenrollReason] = useState('');

    const reenroll = faceReenrollState(reenrollRequests);

    async function loadReenrollRequests() {
        if (user.face_enrolled !== true) {
            setReenrollRequests(null);
            return;
        }
        try {
            const rows = await myFaceReenrollRequests();
            if (mountedRef.current) setReenrollRequests(rows);
        } catch (error) {
            if (parseApiError(error).status === 401) {
                clearAuthSession();
                return;
            }
            // A failed read must not strand the student: treat it as "nothing
            // outstanding" so the button still offers to send a request, and
            // let the backend be the one to refuse a duplicate.
            if (mountedRef.current) setReenrollRequests([]);
        }
    }

    useEffect(() => {
        void loadReenrollRequests();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [user.face_enrolled]);

    async function askForReenrollment() {
        const reason = reenrollReason.trim();
        if (!reason) {
            setReenrollError('Tell us why you need to re-enroll your face.');
            return;
        }
        setReenrollBusy(true);
        setReenrollError('');
        try {
            await requestFaceReenrollment(reason);
            if (mountedRef.current) setReenrollReason('');
        } catch (error) {
            const parsed = parseApiError(error);
            if (parsed.status === 401) {
                clearAuthSession();
                return;
            }
            if (mountedRef.current) {
                setReenrollError(parsed.status === 400
                    ? 'You already have a re-enrollment request outstanding.'
                    : 'Could not send the request. Please try again.');
            }
        }
        // Reloaded either way: on success to pick up the new row, and on
        // failure because a 400 usually means a request is already there.
        await loadReenrollRequests();
        if (mountedRef.current) setReenrollBusy(false);
    }

    // Location
    const [
        locationModalOpen,
        setLocationModalOpen,
    ] = useState(false);

    const [
        locationStatus,
        setLocationStatus,
    ] =
        useState<LocationStatus>('idle');

    const [
        locationError,
        setLocationError,
    ] = useState('');

    const [
        locationReading,
        setLocationReading,
    ] =
        useState<LocationReading | null>(null);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            stopCamera();
        };
    }, []);

    function stopCamera() {
        cameraRequestRef.current++;
        if (cameraStreamRef.current) {
            cameraStreamRef.current
                .getTracks()
                .forEach((track) =>
                    track.stop()
                );

            cameraStreamRef.current = null;
        }

        if (videoRef.current) {
            videoRef.current.srcObject = null;
        }
    }

    function openCameraSetup() {
        setCameraError('');
        setCameraStatus('idle');
        setCameraModalOpen(true);
    }

    function closeCameraSetup() {
        stopCamera();

        setCameraStatus('idle');
        setCameraError('');
        setCameraModalOpen(false);
    }

    async function requestCameraAccess() {
        const requestId = ++cameraRequestRef.current;
        setCameraError('');
        setCameraStatus('requesting');

        try {
            if (
                !navigator.mediaDevices ||
                !navigator.mediaDevices
                    .getUserMedia
            ) {
                setCameraStatus('error');

                setCameraError(
                    'Camera access is not supported by this browser.'
                );

                return;
            }

            const stream =
                await navigator.mediaDevices.getUserMedia(
                    {
                        video: {
                            width: 640,
                            height: 480,
                            facingMode:
                                'user',
                        },
                        audio: false,
                    }
                );

            if (!mountedRef.current || requestId !== cameraRequestRef.current) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            cameraStreamRef.current = stream;

            if (videoRef.current) {
                videoRef.current.srcObject =
                    stream;

                try {
                    await videoRef.current.play();
                } catch {
                    // autoPlay handles most browsers
                }
            }

            setCameraStatus('ready');
        } catch (error) {
            stopCamera();

            setCameraStatus('error');

            if (
                error instanceof DOMException
            ) {
                switch (error.name) {
                    case 'NotAllowedError':
                        setCameraError(
                            'Camera permission was denied. Please allow camera access in your browser settings and try again.'
                        );
                        return;

                    case 'NotFoundError':
                        setCameraError(
                            'No camera was found on this device.'
                        );
                        return;

                    case 'NotReadableError':
                        setCameraError(
                            'Your camera could not be accessed. It may already be in use by another application.'
                        );
                        return;

                    case 'OverconstrainedError':
                        setCameraError(
                            'Your camera does not support the requested video settings.'
                        );
                        return;

                    case 'SecurityError':
                        setCameraError(
                            'Camera access was blocked for security reasons.'
                        );
                        return;
                }
            }

            setCameraError(
                'Unable to access your camera. Please try again.'
            );
        }
    }

    async function confirmCameraConsent() {
        setConsentError('');
        setCameraError('');
        setConsentLoading('camera');

        try {
            const updatedUser =
                await updateConsent({
                    camera_consent: true,
                });

            applyUserUpdate(updatedUser);
            closeCameraSetup();
        } catch (error) {
            const failure = parseApiError(error);
            if (mountedRef.current && failure.kind !== 'cancelled') setCameraError(failure.message);
        } finally {
            setConsentLoading(null);
        }
    }

    async function revokeCameraConsent() {
        setConsentError('');
        setConsentLoading('camera');

        try {
            const updatedUser =
                await updateConsent({
                    camera_consent: false,
                });

            applyUserUpdate(updatedUser);
        } catch (error) {
            const failure = parseApiError(error);
            if (mountedRef.current && failure.kind !== 'cancelled') setConsentError(failure.message);
        } finally {
            setConsentLoading(null);
        }
    }

    function openLocationSetup() {
        setLocationError('');
        setLocationReading(null);
        setLocationStatus('idle');
        setLocationModalOpen(true);
    }

    function closeLocationSetup() {
        setLocationError('');
        setLocationReading(null);
        setLocationStatus('idle');
        setLocationModalOpen(false);
    }

    function requestLocationAccess() {
        setLocationError('');
        setLocationReading(null);
        setLocationStatus('requesting');

        if (!navigator.geolocation) {
            setLocationStatus('error');

            setLocationError(
                'Geolocation is not supported by this browser.'
            );

            return;
        }

        navigator.geolocation.getCurrentPosition(
            (position) => {
                setLocationReading({
                    latitude:
                        position.coords.latitude,
                    longitude:
                        position.coords.longitude,
                    accuracy:
                        position.coords.accuracy,
                });

                setLocationStatus('ready');
            },
            (error) => {
                setLocationStatus('error');

                switch (error.code) {
                    case error.PERMISSION_DENIED:
                        setLocationError(
                            'Location permission was denied. Please allow location access in your browser settings and try again.'
                        );
                        break;

                    case error.POSITION_UNAVAILABLE:
                        setLocationError(
                            'Your current location could not be determined.'
                        );
                        break;

                    case error.TIMEOUT:
                        setLocationError(
                            'Location detection timed out. Please try again.'
                        );
                        break;

                    default:
                        setLocationError(
                            'Unable to access your location. Please try again.'
                        );
                }
            },
            {
                enableHighAccuracy: true,
                timeout: 10000,
                maximumAge: 0,
            }
        );
    }

    async function confirmLocationConsent() {
        setConsentError('');
        setLocationError('');
        setConsentLoading('location');

        try {
            const updatedUser =
                await updateConsent({
                    geolocation_consent:
                        true,
                });

            applyUserUpdate(updatedUser);
            closeLocationSetup();
        } catch (error) {
            const failure = parseApiError(error);
            if (mountedRef.current && failure.kind !== 'cancelled') setLocationError(failure.message);
        } finally {
            setConsentLoading(null);
        }
    }

    async function revokeLocationConsent() {
        setConsentError('');
        setConsentLoading('location');

        try {
            const updatedUser =
                await updateConsent({
                    geolocation_consent:
                        false,
                });

            applyUserUpdate(updatedUser);
        } catch (error) {
            const failure = parseApiError(error);
            if (mountedRef.current && failure.kind !== 'cancelled') setConsentError(failure.message);
        } finally {
            setConsentLoading(null);
        }
    }


    return <>
      <section className="panel verification-panel" aria-labelledby="setup-title">
        <StudentSectionHeading id="setup-title" title="Verification & Consent" description="Manage saved consent and face enrollment." icon="shield" />
        {consentError && <p role="alert" className="notice notice-error mb-4">{consentError}</p>}
        <div className="setup-grid">
          <div className={`consent-card ${user.camera_consent === true ? 'state-ready' : user.camera_consent === false ? 'state-needed' : 'state-unknown'}`}>
            <div className="consent-card-heading"><h3><StudentIcon name="camera" />Camera & Biometric</h3><span className={`tag ${user.camera_consent === true ? 'tag-approved' : user.camera_consent === false ? 'tag-rejected' : 'tag-neutral'}`}>{user.camera_consent === true ? 'Consented' : user.camera_consent === false ? 'Not given' : 'Unknown'}</span></div>
            <p className="consent-description">Identity and liveness verification.</p>
            <button type="button" className="btn-secondary consent-button" disabled={consentLoading !== null}
              onClick={() => { if (user.camera_consent) void revokeCameraConsent(); else openCameraSetup(); }}>
              {consentLoading === 'camera' ? 'Updating...' : user.camera_consent ? 'Revoke camera consent' : 'Set up camera'}</button>
          </div>
          <div className={`consent-card ${user.geolocation_consent === true ? 'state-ready' : user.geolocation_consent === false ? 'state-needed' : 'state-unknown'}`}>
            <div className="consent-card-heading"><h3><StudentIcon name="location" />Geolocation</h3><span className={`tag ${user.geolocation_consent === true ? 'tag-approved' : user.geolocation_consent === false ? 'tag-rejected' : 'tag-neutral'}`}>{user.geolocation_consent === true ? 'Consented' : user.geolocation_consent === false ? 'Not given' : 'Unknown'}</span></div>
            <p className="consent-description">Confirm your location at check-in.</p>
            <button type="button" className="btn-secondary consent-button" disabled={consentLoading !== null}
              onClick={() => { if (user.geolocation_consent) void revokeLocationConsent(); else openLocationSetup(); }}>
              {consentLoading === 'location' ? 'Updating...' : user.geolocation_consent ? 'Revoke location consent' : 'Set up location'}</button>
          </div>
          <div className={`consent-card ${user.face_enrolled === true ? 'state-ready' : user.face_enrolled === false ? 'state-needed' : 'state-unknown'}`}>
            <div className="consent-card-heading"><h3><StudentIcon name="face" />Face Enrollment</h3><span className={`tag ${user.face_enrolled === true ? 'tag-approved' : user.face_enrolled === false ? 'tag-rejected' : 'tag-neutral'}`}>{user.face_enrolled === true ? 'Enrolled' : user.face_enrolled === false ? 'Not enrolled' : 'Unknown'}</span></div>
            <p className="consent-description">Your template for face matching.</p>
            <button type="button" className="btn-secondary consent-button"
              disabled={!user.camera_consent || reenrollBusy || reenroll.stage === 'pending'}
              onClick={() => {
                  if (user.face_enrolled !== true) { setFaceEnrollmentMode('enroll'); return; }
                  // An approval is the only thing that opens the camera again.
                  if (reenroll.stage === 'approved') { setFaceEnrollmentMode('re-enroll'); return; }
                  void askForReenrollment();
              }}>
              {reenrollBusy ? 'Sending...' : faceReenrollLabel(reenroll.stage, user.face_enrolled === true)}</button>
            {!user.camera_consent && <p className="mt-1 text-xs muted">Camera consent required to enroll.</p>}
            {user.camera_consent && user.face_enrolled === true && reenroll.stage === 'none' &&
              <p className="mt-1 text-xs muted">Replacing your template needs an admin&apos;s approval.</p>}
            {user.camera_consent && user.face_enrolled === true &&
              (reenroll.stage === 'none' || reenroll.stage === 'denied') && (
              <label className="mt-2 block text-xs">
                <span className="muted">Reason for re-enrollment</span>
                <textarea
                  className="mt-1 block w-full rounded-lg border border-gray-300 p-2 text-sm"
                  rows={2}
                  maxLength={500}
                  placeholder="e.g. New glasses, my face match keeps failing"
                  value={reenrollReason}
                  disabled={reenrollBusy}
                  onChange={(event) => setReenrollReason(event.target.value)}
                />
              </label>
            )}
            {reenroll.stage === 'pending' &&
              <p className="mt-1 text-xs muted">Waiting on an admin to decide.</p>}
            {reenroll.stage === 'approved' &&
              <p className="mt-1 text-xs muted">Approved. Capturing a new photo uses this approval once.</p>}
            {reenroll.stage === 'denied' &&
              <p className="mt-1 text-xs muted">{reenroll.record?.review_notes
                  ? `Turned down: ${reenroll.record.review_notes}`
                  : 'Your last request was turned down. You can ask again.'}</p>}
            {reenrollError && <p role="alert" className="notice notice-error mt-2">{reenrollError}</p>}
          </div>
        </div>
        <p className="settings-note"><StudentIcon name="info" />SAIV consent and browser permissions are separate. Access is requested only when you choose a setup action.</p>
      </section>
            {/* Camera Setup Modal */}
            {cameraModalOpen && (
                <ModalFrame label="Camera setup" onClose={closeCameraSetup} busy={consentLoading !== null}>
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <h3 className="text-xl font-semibold text-gray-900">
                                    Camera setup
                                </h3>

                                <p className="mt-2 text-sm text-gray-600">
                                    SAIV requires
                                    camera access
                                    for identity
                                    verification
                                    and liveness
                                    detection during
                                    attendance
                                    check-in.
                                </p>
                            </div>

                            <button
                                type="button"
                                onClick={
                                    closeCameraSetup
                                }
                                className="rounded-lg px-3 py-1 text-xl text-gray-500 hover:bg-gray-100"
                                aria-label="Close camera setup"
                            >
                                ×
                            </button>
                        </div>

                        <div className="mt-5 rounded-lg bg-blue-50 p-4 text-sm text-blue-800">
                            Your camera is only
                            used when needed
                            for verification.
                            You will control
                            when browser camera
                            access is requested.
                        </div>

                        {cameraStatus ===
                            'idle' && (
                                <div className="mt-6">
                                    <p className="text-sm text-gray-600">
                                        When you
                                        continue,
                                        your browser
                                        may ask
                                        whether
                                        this site is
                                        allowed to
                                        use your
                                        camera.
                                    </p>

                                    <button
                                        type="button"
                                        onClick={
                                            requestCameraAccess
                                        }
                                        className="btn-primary mt-4 w-full"
                                    >
                                        Request camera
                                        access
                                    </button>
                                </div>
                            )}

                        {cameraStatus ===
                            'requesting' && (
                                <div className="mt-6 rounded-lg border border-gray-200 p-5 text-center">
                                    <p className="font-medium text-gray-900">
                                        Waiting for
                                        camera
                                        permission...
                                    </p>

                                    <p className="mt-2 text-sm text-gray-500">
                                        Please respond
                                        to your
                                        browser&apos;s
                                        permission
                                        request.
                                    </p>
                                </div>
                            )}

                        <video
                            ref={videoRef}
                            autoPlay
                            muted
                            playsInline
                            className={
                                cameraStatus ===
                                    'ready'
                                    ? 'mt-6 aspect-[4/3] w-full rounded-lg bg-black object-cover'
                                    : 'hidden'
                            }
                        />

                        {cameraStatus ===
                            'ready' && (
                                <div className="mt-4">
                                    <div className="rounded-lg bg-green-50 p-3 text-sm text-green-700">
                                        Camera access
                                        is working.
                                        You can see
                                        the live
                                        preview above.
                                    </div>

                                    <button
                                        type="button"
                                        disabled={
                                            consentLoading ===
                                            'camera'
                                        }
                                        onClick={
                                            confirmCameraConsent
                                        }
                                        className="btn-primary mt-4 w-full"
                                    >
                                        {consentLoading ===
                                            'camera'
                                            ? 'Saving...'
                                            : 'Confirm camera consent'}
                                    </button>
                                </div>
                            )}

                        {cameraStatus ===
                            'error' && (
                                <div className="mt-6">
                                    <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">
                                        {
                                            cameraError
                                        }
                                    </div>

                                    <button
                                        type="button"
                                        onClick={
                                            requestCameraAccess
                                        }
                                        className="btn-secondary mt-4 w-full"
                                    >
                                        Try again
                                    </button>
                                </div>
                            )}

                        <button
                            type="button"
                            onClick={
                                closeCameraSetup
                            }
                            className="mt-3 w-full rounded-lg px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100"
                        >
                            Cancel
                        </button>
                </ModalFrame>
            )}

            {/* Location Setup Modal */}
            {locationModalOpen && (
                <ModalFrame label="Location setup" onClose={closeLocationSetup} busy={consentLoading !== null}>
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <h3 className="text-xl font-semibold text-gray-900">
                                    Location setup
                                </h3>

                                <p className="mt-2 text-sm text-gray-600">
                                    SAIV requires
                                    your location
                                    during
                                    attendance
                                    check-in to
                                    verify that
                                    you are within
                                    the allowed
                                    venue area.
                                </p>
                            </div>

                            <button
                                type="button"
                                onClick={
                                    closeLocationSetup
                                }
                                className="rounded-lg px-3 py-1 text-xl text-gray-500 hover:bg-gray-100"
                                aria-label="Close location setup"
                            >
                                ×
                            </button>
                        </div>

                        <div className="mt-5 rounded-lg bg-blue-50 p-4 text-sm text-blue-800">
                            Your location will
                            be requested only
                            when it is needed
                            for attendance
                            verification.
                        </div>

                        {locationStatus ===
                            'idle' && (
                                <div className="mt-6">
                                    <p className="text-sm text-gray-600">
                                        When you
                                        continue,
                                        your browser
                                        may ask
                                        whether
                                        this site is
                                        allowed to
                                        access your
                                        location.
                                    </p>

                                    <button
                                        type="button"
                                        onClick={
                                            requestLocationAccess
                                        }
                                        className="btn-primary mt-4 w-full"
                                    >
                                        Request
                                        location
                                        access
                                    </button>
                                </div>
                            )}

                        {locationStatus ===
                            'requesting' && (
                                <div className="mt-6 rounded-lg border border-gray-200 p-5 text-center">
                                    <p className="font-medium text-gray-900">
                                        Waiting for
                                        location
                                        permission...
                                    </p>

                                    <p className="mt-2 text-sm text-gray-500">
                                        Please respond
                                        to your
                                        browser&apos;s
                                        permission
                                        request.
                                    </p>
                                </div>
                            )}

                        {locationStatus ===
                            'ready' &&
                            locationReading && (
                                <div className="mt-6">
                                    <div className="rounded-lg bg-green-50 p-4 text-sm text-green-700">
                                        Location
                                        access is
                                        working.
                                    </div>

                                    <div className="mt-4 rounded-lg border border-gray-200 p-4 text-sm">
                                        <div className="flex justify-between gap-4">
                                            <span className="text-gray-500">
                                                Latitude
                                            </span>

                                            <span className="font-medium text-gray-900">
                                                {locationReading.latitude.toFixed(
                                                    6
                                                )}
                                            </span>
                                        </div>

                                        <div className="mt-2 flex justify-between gap-4">
                                            <span className="text-gray-500">
                                                Longitude
                                            </span>

                                            <span className="font-medium text-gray-900">
                                                {locationReading.longitude.toFixed(
                                                    6
                                                )}
                                            </span>
                                        </div>

                                        <div className="mt-2 flex justify-between gap-4">
                                            <span className="text-gray-500">
                                                Accuracy
                                            </span>

                                            <span className="font-medium text-gray-900">
                                                {Math.round(
                                                    locationReading.accuracy
                                                )}{' '}
                                                m
                                            </span>
                                        </div>
                                    </div>

                                    <button
                                        type="button"
                                        disabled={
                                            consentLoading ===
                                            'location'
                                        }
                                        onClick={
                                            confirmLocationConsent
                                        }
                                        className="btn-primary mt-4 w-full"
                                    >
                                        {consentLoading ===
                                            'location'
                                            ? 'Saving...'
                                            : 'Confirm location consent'}
                                    </button>
                                </div>
                            )}

                        {locationStatus ===
                            'error' && (
                                <div className="mt-6">
                                    <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">
                                        {
                                            locationError
                                        }
                                    </div>

                                    <button
                                        type="button"
                                        onClick={
                                            requestLocationAccess
                                        }
                                        className="btn-secondary mt-4 w-full"
                                    >
                                        Try again
                                    </button>
                                </div>
                            )}

                        <button
                            type="button"
                            onClick={
                                closeLocationSetup
                            }
                            className="mt-3 w-full rounded-lg px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100"
                        >
                            Cancel
                        </button>
                </ModalFrame>
            )}

            {/* Face Enrollment Modal */}
            {faceEnrollmentMode && (
                <FaceEnrollmentModal
                    mode={faceEnrollmentMode}
                    onClose={() => setFaceEnrollmentMode(null)}
                    onEnrolled={async () => {
                        const updatedUser = await getCurrentUser();
                        if (updatedUser.role !== 'student' || updatedUser.is_active !== true) {
                            clearAuthSession();
                            return;
                        }
                        applyUserUpdate(updatedUser);
                        // The approval has just been spent, so the card must
                        // go back to offering a fresh request rather than
                        // another capture.
                        await loadReenrollRequests();
                    }}
                    onUnauthorized={() => {
                        clearAuthSession();
                    }}
                />
            )}
    </>;
}