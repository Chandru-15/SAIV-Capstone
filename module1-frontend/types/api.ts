export type UserRole =
  | 'student'
  | 'ta'
  | 'instructor'
  | 'admin';

export interface User {
  browser_verification_required?: boolean;
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active?: boolean;
  camera_consent?: boolean;
  geolocation_consent?: boolean;
  face_enrolled?: boolean;
  created_at?: string;
}

export interface ConsentUpdateRequest {
  camera_consent?: boolean;
  geolocation_consent?: boolean;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
  user: User;
}

export interface RegisterRequest {
  email: string;
  password: string;
  full_name: string;
  role: UserRole;
}

export interface RefreshTokenRequest {
  refresh_token: string;
}

export interface RefreshTokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
}

export interface ActiveSession {
  id: string;
  course_id: string;
  instructor_id?: string | null;
  name: string;
  session_type: string;
  description?: string | null;
  scheduled_start: string;
  scheduled_end: string;
  checkin_opens_at: string;
  checkin_closes_at: string;
  status: string;
  actual_start?: string | null;
  actual_end?: string | null;
  venue_latitude?: number | null;
  venue_longitude?: number | null;
  venue_name?: string | null;
  geofence_radius_meters?: number | null;
  require_liveness_check: boolean;
  require_face_match: boolean;
  risk_threshold?: number | null;
  created_at: string;
}

export interface StudentCourse {
  id: string;
  code: string;
  name: string;
  require_face_recognition: boolean;
  require_device_binding: boolean;
}

export interface RegisteredDevice {
  browser_key_id?: string | null;
  browser_key_usable?: boolean | null;
  id: string;
  user_id: string;
  device_fingerprint: string;
  device_name?: string | null;
  platform?: string | null;
  browser?: string | null;
  os_version?: string | null;
  app_version?: string | null;
  is_trusted: boolean;
  trust_score: string;
  is_active: boolean;
  total_checkins: number;
  first_seen_at: string;
  last_seen_at: string;
}

export interface FacePolicy {
  faceMatchRequired: boolean;
  imageRequired: boolean;
}

export type CheckinStatus =
  | 'pending'
  | 'approved'
  | 'flagged'
  | 'rejected'
  | 'appealed';

export interface CheckinHistoryItem {
  id: string;
  session_id: string;
  student_id: string;
  device_id?: string | null;
  status: CheckinStatus;
  checked_in_at: string;
  verified_at?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  location_accuracy_meters?: number | null;
  distance_from_venue_meters?: number | null;
  liveness_passed?: boolean | null;
  liveness_score?: number | null;
  face_match_passed?: boolean | null;
  face_match_score?: number | null;
  risk_score: number;
  risk_factors?: string | null;
  qr_code_verified: boolean;
  // Set once a reviewer has decided: such a check-in is appealed, not retried.
  reviewed_by_id?: string | null;
}

export type FaceReEnrollStatus = 'pending' | 'approved' | 'denied';

export interface FaceReEnrollRequestRecord {
  id: string;
  user_id: string;
  status: FaceReEnrollStatus;
  reason: string | null;
  reviewed_by_id: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  // Stamped once the approval has been spent on a replacement. The status
  // stays 'approved' because it was, so this is the only thing that tells an
  // approval still worth using from one already used.
  used_at: string | null;
  created_at: string;
}

export interface PageResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface FaceEnrollResponse {
  success: boolean;
  message: string;
  face_enrolled: boolean;
  quality_score: number;
  // Only present on a re-enrolment: the previous template is now gone.
  previous_template_replaced?: boolean;
}

// Mirrors the backend enum, including the values the API can report but
// never issues as a challenge.
export type LivenessChallengeType =
  | 'blink'
  | 'head_turn'
  | 'speak'
  | 'passive'
  | 'colour_reflection';

// The subset the server actually issues. `passive` asks for nothing, so there
// is no challenge to issue, and `speak` is not implemented.
export type ActiveLivenessChallengeType = 'blink' | 'head_turn' | 'colour_reflection';

export interface ActiveLivenessChallenge {
  challenge_id: string;
  challenge_type: ActiveLivenessChallengeType;
  instruction: string;
  min_frames: number;
  recommended_frames: number;
  max_frames: number;
  expires_at: string;

  // colour_reflection only; absent or empty for the gesture challenges.
  //
  // The screen shows these colours in this order while the camera records,
  // and the backend checks that the face was really lit by them. Knowing the
  // colours is not the same as being able to answer: the frames still have to
  // show a three-dimensional face responding to them in this order, and the
  // server never accepts our opinion about which frame went with which
  // colour -- it works that out from the position of each frame in the list,
  // which is why the frame count has to be exact.
  colour_sequence?: string[];
  // How long each colour is held, and how long to wait after the change
  // before the first frame of that step. The settle delay is not politeness:
  // an LCD takes tens of milliseconds to finish a transition and the camera
  // starts adjusting to the new light immediately, so a frame grabbed too
  // early still shows the previous colour.
  hold_ms?: number;
  settle_ms?: number;
  frames_per_step?: number;
}

// The backend calculates face-match and liveness scores when an image
// is supplied. Never include client-computed scores in this request.
export interface CheckInCreateRequest {
  session_id: string;
  latitude?: number | null;
  longitude?: number | null;
  location_accuracy_meters?: number | null;
  device_fingerprint?: string | null;
  image?: string | null;
  challenge_id?: string;
  challenge_frames?: string[];
  liveness_challenge_type?: LivenessChallengeType | null;
  qr_code_secret?: string | null;
  /**
   * Re-verify an earlier failed attempt at this session instead of being
   * refused as a duplicate. The backend paces retries with an exponential
   * cooldown and answers 429 with Retry-After when one comes too soon.
   */
  retry?: boolean;
}

export type CheckInResponse = CheckinHistoryItem & {
  browser_key_verified?: boolean;
  reviewed_by_id?: string | null;
  reviewed_at?: string | null;
  review_notes?: string | null;
  appeal_reason?: string | null;
  appealed_at?: string | null;
};
