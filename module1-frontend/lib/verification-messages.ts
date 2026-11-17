/** Presentation only. The backend remains authoritative for signals and decisions. */
const explanations: Record<string, string> = {
  liveness_failed: 'The required liveness challenge was not completed or verified.',
  liveness_low_confidence: 'Liveness verification returned a low confidence score.',
  geo_out_of_bounds: 'The submitted location was outside the permitted attendance area.',
  geo_accuracy_low: 'The submitted location had low reported accuracy.',
  impossible_travel: 'Location checks produced an unusual travel signal.',
  geo_ip_mismatch: 'The submitted location did not agree with the apparent location of the network address. VPN use is a common cause and this is not conclusive proof of a spoofed location.',
  attestation_failed: 'Device attestation was not established. Browser registration alone does not establish it.',
  device_unknown: 'The backend did not recognise a registered browser for this submission.',
  device_emulator: 'Device checks produced an emulator-related risk signal.',
  device_rooted: 'Device checks produced a modified-device risk signal.',
  vpn_detected: 'Network verification produced a VPN-related risk signal. This is not conclusive proof of VPN use.',
  proxy_detected: 'Network verification produced a proxy-related risk signal. This is not conclusive proof of proxy use.',
  tor_detected: 'Network verification produced an anonymous-network risk signal.',
  suspicious_ip: 'The network address produced a risk signal.',
  face_match_failed: 'The submitted face did not pass matching against your enrolled template.',
  face_match_low_confidence: 'Face matching returned a low confidence score.',
  replay_suspected: 'Verification produced a possible replay signal.',
  deepfake_suspected: 'Verification produced a possible manipulated-image signal.',
  rapid_succession: 'This record includes a repeated verification attempt.',
  unusual_time: 'The submission time produced a risk signal.',
  pattern_anomaly: 'Verification produced an unusual-pattern signal.',
};

export function riskSignalCodes(raw?: string | null): string[] {
  if (!raw) return [];
  try {
    const values: unknown = JSON.parse(raw);
    return Array.isArray(values) ? Array.from(new Set(values.filter((item): item is string =>
      typeof item === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(item)))).slice(0, 30) : [];
  } catch { return []; }
}

export function riskSignalExplanation(code: string): string {
  return explanations[code] || 'The backend recorded an additional verification signal.';
}
