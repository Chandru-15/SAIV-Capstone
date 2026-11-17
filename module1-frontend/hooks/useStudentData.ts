'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { getActiveSessions, getMyCheckins, getMyCourses, getSession } from '../lib/student-api';
import { ClientDataError, parseApiError } from '../lib/api-error';
import { mergeCheckins } from '../lib/attendance';
import { checkinAttemptState } from '../lib/student-policy';
import type { CheckinAttemptState } from '../lib/student-policy';
import type { ActiveSession, CheckinHistoryItem, StudentCourse } from '../types/api';

const PAGE_SIZE = 50;
const MAX_DUPLICATE_SCAN_PAGES = 20;
interface HistoryState {
  items: CheckinHistoryItem[];
  total: number;
  nextOffset: number;
  complete: boolean;
}

export function useStudentData() {
  const [sessions, setSessions] = useState<ActiveSession[]>([]);
  const [courses, setCourses] = useState<StudentCourse[] | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState('');
  const [history, setHistory] = useState<HistoryState>({ items: [], total: 0, nextOffset: 0, complete: false });
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [attempts, setAttempts] = useState<ReadonlyMap<string, CheckinAttemptState>>(new Map());
  const mounted = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const historyRef = useRef(history);
  const historySequence = useRef(0);
  const historyBusy = useRef(false);
  const knownIds = useRef(new Map<string, CheckinAttemptState>());
  const allKnown = useRef(false);
  const scannedOffset = useRef(0);
  const labelCache = useRef(new Map<string, Promise<string>>());
  const labelQueue = useRef<Promise<void>>(Promise.resolve());

  const remember = useCallback((items: CheckinHistoryItem[]) => {
    items.forEach((item) => knownIds.current.set(item.session_id, checkinAttemptState(item)));
    if (mounted.current) setAttempts(new Map(knownIds.current));
  }, []);

  const loadLabels = useCallback((items: CheckinHistoryItem[]): Promise<void> => {
    // Only IDs from this user's returned page are requested. Three workers, at most
    // PAGE_SIZE distinct lookups per page; shared sessions and prior pages are cached.
    const run = async () => {
      const ids = Array.from(new Set(items.map((item) => item.session_id)));
      let next = 0;
      await Promise.all(Array.from({ length: Math.min(3, ids.length) }, async () => {
        while (next < ids.length && mounted.current) {
          const id = ids[next++];
          let pending = labelCache.current.get(id);
          if (!pending) {
            pending = getSession(id, controller.current?.signal).then((session) => session.name)
              .catch(() => 'Attendance session (details unavailable)');
            labelCache.current.set(id, pending);
          }
          const name = await pending;
          if (mounted.current) setLabels((previous) => ({ ...previous, [id]: name }));
        }
      }));
    };
    // Page loads can overlap metadata work; keep one three-worker queue per view.
    labelQueue.current = labelQueue.current.then(run, run);
    return labelQueue.current;
  }, []);

  const refreshSessions = useCallback(async () => {
    setSessionsLoading(true);
    setSessionsError('');
    try {
      const [open, enrolled] = await Promise.all([
        getActiveSessions(controller.current?.signal), getMyCourses(controller.current?.signal),
      ]);
      if (!mounted.current) return;
      setCourses(enrolled);
      const enrolledOpen = open.filter((session) => enrolled.some((course) => course.id === session.course_id));
      setSessions(enrolledOpen);
      enrolledOpen.forEach((session) => labelCache.current.set(session.id, Promise.resolve(session.name)));
    } catch (error) {
      const failure = parseApiError(error);
      if (mounted.current && failure.kind !== 'cancelled') {
        setCourses(null);
        setSessionsError(failure.message);
      }
    } finally {
      if (mounted.current) setSessionsLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async (reset: boolean) => {
    if (!reset && (historyBusy.current || historyRef.current.complete)) return;
    const sequence = reset ? ++historySequence.current : historySequence.current;
    historyBusy.current = true;
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const page = await getMyCheckins({ offset: reset ? 0 : historyRef.current.nextOffset, limit: PAGE_SIZE, signal: controller.current?.signal });
      if (!mounted.current || sequence !== historySequence.current) return;
      const nextOffset = page.offset + page.items.length;
      const nextHistory = {
        items: mergeCheckins(reset ? [] : historyRef.current.items, page.items),
        total: page.total, nextOffset, complete: nextOffset >= page.total,
      };
      historyRef.current = nextHistory;
      setHistory(nextHistory);
      if (reset) { knownIds.current.clear(); allKnown.current = false; scannedOffset.current = 0; }
      scannedOffset.current = Math.max(scannedOffset.current, nextOffset);
      remember(page.items);
      if (nextHistory.complete) allKnown.current = true;
      if (nextHistory.complete && nextHistory.items.length < page.total) {
        setHistoryError('Attendance records changed while loading. Refresh history to reload the complete list.');
        allKnown.current = false;
      }
      void loadLabels(page.items);
    } catch (error) {
      const failure = parseApiError(error);
      if (mounted.current && sequence === historySequence.current && failure.kind !== 'cancelled') setHistoryError(failure.message);
      throw error;
    } finally {
      if (mounted.current && sequence === historySequence.current) {
        historyBusy.current = false;
        setHistoryLoading(false);
      }
    }
  }, [loadLabels, remember]);

  // Returns how the session's existing check-in stands, or null when there is
  // none. A 'retryable' answer still permits an attempt.
  const findExistingAttempt = useCallback(async (sessionId: string): Promise<CheckinAttemptState | null> => {
    const known = knownIds.current.get(sessionId);
    if (known) return known;
    if (allKnown.current) return null;
    // /checkins/me has no session filter. Scan only when starting an otherwise
    // eligible attempt; never mistake the first page for a complete negative result.
    const sequence = historySequence.current;
    let offset = scannedOffset.current;
    for (let pageNumber = 0; pageNumber < MAX_DUPLICATE_SCAN_PAGES; pageNumber++) {
      const page = await getMyCheckins({ offset, limit: 100, signal: controller.current?.signal });
      if (!mounted.current) throw new ClientDataError('Attendance check was cancelled.');
      if (sequence !== historySequence.current) throw new ClientDataError('Attendance history refreshed. Please try again.');
      remember(page.items);
      scannedOffset.current = page.offset + page.items.length;
      const found = knownIds.current.get(sessionId);
      if (found) return found;
      offset = page.offset + page.items.length;
      if (offset >= page.total) { allKnown.current = true; return null; }
    }
    throw new ClientDataError('Unable to confirm your complete attendance history. Load more history or refresh before checking in.');
  }, [remember]);

  const refreshHistory = useCallback(async (record?: CheckinHistoryItem) => {
    if (record) remember([record]);
    await loadHistory(true);
    // Keep the confirmed submission even if a concurrently changing first page omits it.
    if (record) remember([record]);
  }, [loadHistory, remember]);

  useEffect(() => {
    mounted.current = true;
    controller.current = new AbortController();
    void refreshSessions();
    void loadHistory(true).catch(() => {});
    return () => {
      mounted.current = false;
      controller.current?.abort();
      historySequence.current++;
    };
  }, [loadHistory, refreshSessions]);

  return {
    sessions, courses, sessionsLoading, sessionsError, refreshSessions,
    history, historyLoading, historyError, labels, attempts, findExistingAttempt,
    refreshHistory, loadMore: () => loadHistory(false),
  };
}
