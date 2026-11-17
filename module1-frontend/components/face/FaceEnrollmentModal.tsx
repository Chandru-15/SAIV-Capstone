
'use client';

import ModalFrame from '../ui/ModalFrame';

import { useEffect, useRef, useState } from 'react';
import { ClientDataError, parseApiError } from '../../lib/api-error';

import { enrollFace, reenrollFace } from '../../lib/student-api';
import type { FaceEnrollResponse } from '../../types/api';

interface FaceEnrollmentModalProps {
  onClose: () => void;
  onEnrolled: () => Promise<void>;
  onUnauthorized: () => void;
  // 're-enroll' replaces an existing template and warns the user as much.
  mode?: 'enroll' | 're-enroll';
}

type CameraStatus = 'idle' | 'requesting' | 'ready' | 'error';

export default function FaceEnrollmentModal({
  onClose,
  onEnrolled,
  onUnauthorized,
  mode = 'enroll',
}: FaceEnrollmentModalProps) {
  const replacing = mode === 're-enroll';
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mountedRef = useRef(true);

  const [cameraStatus, setCameraStatus] =
    useState<CameraStatus>('idle');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [refreshWarning, setRefreshWarning] = useState('');
  const [result, setResult] = useState<FaceEnrollResponse | null>(
    null
  );

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
      streamRef.current?.getTracks().forEach((track) => {
        track.stop();
      });
      streamRef.current = null;
    };
  }, []);

  function stopCamera() {
    streamRef.current?.getTracks().forEach((track) => {
      track.stop();
    });
    streamRef.current = null;

    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }

  function closeModal() {
    if (submitting) return;
    stopCamera();
    onClose();
  }

  async function startCamera() {
    setError('');
    setCameraStatus('requesting');

    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new ClientDataError('Camera is not supported by this browser.');
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: 'user',
        },
        audio: false,
      });

      if (!mountedRef.current || !videoRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      streamRef.current = stream;
      videoRef.current.srcObject = stream;

      try {
        await videoRef.current.play();
      } catch {
        // The autoPlay attribute may handle playback.
      }

      if (mountedRef.current) {
        setCameraStatus('ready');
      }
    } catch (err) {
      stopCamera();
      if (!mountedRef.current) return;

      setCameraStatus('error');

      if (err instanceof DOMException) {
        if (err.name === 'NotAllowedError') {
          setError(
            'Camera access was denied. Allow camera permission and try again.'
          );
          return;
        }

        if (err.name === 'NotFoundError') {
          setError('No camera was found on this device.');
          return;
        }
      }

      setError(
        err instanceof Error
          ? err.message
          : 'Unable to access your camera.'
      );
    }
  }

  async function captureAndEnroll() {
    const video = videoRef.current;

    if (
      !video ||
      !streamRef.current ||
      video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA ||
      !video.videoWidth ||
      !video.videoHeight
    ) {
      setError('Camera is still starting. Please try again.');
      return;
    }

    setSubmitting(true);
    setError('');

    try {
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;

      const context = canvas.getContext('2d');

      if (!context) {
        throw new ClientDataError('Unable to capture a camera frame.');
      }

      context.drawImage(
        video,
        0,
        0,
        canvas.width,
        canvas.height
      );

      const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
      const imageBase64 = dataUrl.split(',')[1];

      if (!imageBase64) {
        throw new ClientDataError('Unable to encode the camera image.');
      }

      const enrollment = replacing
        ? await reenrollFace(imageBase64)
        : await enrollFace(imageBase64);
      if (!mountedRef.current) return;

      if (!enrollment.success || !enrollment.face_enrolled) {
        throw new ClientDataError(
          'Face enrollment was unsuccessful. Please try again.'
        );
      }

      setResult(enrollment);
      stopCamera();

      try {
        await onEnrolled();
      } catch {
        setRefreshWarning(
          'Enrollment succeeded, but the dashboard could not refresh. Reload the page to check your status.'
        );
      }
    } catch (err) {
      if (!mountedRef.current) return;
      const failure = parseApiError(err);
      if (failure.kind === 'cancelled') return;
      if (failure.status === 401) { onUnauthorized(); return; }
      setError(failure.message);
    } finally {
      if (mountedRef.current) {
        setSubmitting(false);
      }
    }
  }

  return (
    <ModalFrame labelledBy="face-enrollment-title" onClose={closeModal} busy={submitting}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2
              id="face-enrollment-title"
              className="text-xl font-semibold text-gray-900"
            >
              {replacing ? 'Re-enroll Face' : 'Face Enrollment'}
            </h2>
            <p className="mt-2 text-sm text-gray-500">
              {replacing
                ? 'Capture a new photo to replace your current face template. Your existing one keeps working until this succeeds.'
                : 'Capture your face to set up identity verification for attendance check-in.'}
            </p>
          </div>

          <button
            type="button"
            onClick={closeModal}
            disabled={submitting}
            aria-label="Close face enrollment"
            className="text-2xl text-gray-500 hover:text-gray-900 disabled:opacity-50"
          >
            ×
          </button>
        </div>

        {!result && (
          <>
            <p className="mt-5 text-sm text-gray-600">
              Make sure you are in a well-lit area, look directly
              at the camera, and keep your face visible. A photo taken
              with your head turned to one side will be rejected, because
              it produces a template your own face cannot match later.
            </p>

            {replacing && (
              <p className="notice mt-5">
                Your re-enrollment has been approved. Replacing your
                template changes what every future check-in is matched
                against, so the approval covers this one capture: if you need
                to change it again later, you will have to ask again.
              </p>
            )}

            {cameraStatus === 'idle' && (
              <button
                type="button"
                onClick={startCamera}
                className="btn-primary mt-5 w-full"
              >
                Open Camera
              </button>
            )}

            {cameraStatus === 'requesting' && (
              <p className="mt-5 text-sm text-gray-600">
                Waiting for camera permission...
              </p>
            )}

            <video
              ref={videoRef}
              autoPlay
              muted
              playsInline
              className={
                cameraStatus === 'ready'
                  ? 'mt-5 aspect-[4/3] w-full rounded-lg bg-black object-cover'
                  : 'hidden'
              }
            />

            {cameraStatus === 'ready' && (
              <button
                type="button"
                onClick={captureAndEnroll}
                disabled={submitting}
                className="btn-primary mt-4 w-full"
              >
                {submitting
                  ? 'Processing face enrollment...'
                  : replacing
                    ? 'Capture and Replace Template'
                    : 'Capture and Enroll Face'}
              </button>
            )}

            {cameraStatus === 'error' && (
              <button
                type="button"
                onClick={startCamera}
                className="btn-secondary mt-4 w-full"
              >
                Try Camera Again
              </button>
            )}
          </>
        )}

        {error && (
          <p
            role="alert"
            className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700"
          >
            {error}
          </p>
        )}

        {result && (
          <div className="mt-5">
            <div className="rounded-lg bg-green-50 p-4 text-green-800">
              <p className="font-semibold">
                {replacing
                  ? 'Face template replaced!'
                  : 'Face enrolled successfully!'}
              </p>
              <p className="mt-2 text-sm">
                Quality score:{' '}
                {(result.quality_score * 100).toFixed(1)}%
              </p>
            </div>

            {refreshWarning && (
              <p className="mt-3 text-sm text-amber-700">
                {refreshWarning}
              </p>
            )}
          </div>
        )}

        <button
          type="button"
          onClick={closeModal}
          disabled={submitting}
          className="mt-4 w-full rounded-lg border border-gray-300 px-4 py-3 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          {result ? 'Done' : 'Cancel'}
        </button>
    </ModalFrame>
  );
}
