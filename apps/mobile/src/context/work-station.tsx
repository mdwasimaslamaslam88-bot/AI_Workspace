import NetInfo from "@react-native-community/netinfo";
import type { CurrentUser } from "@work-station/shared";
import { AppState } from "react-native";
import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { MobileApiClient, MobileApiError } from "@/api/client";
import { clearSecureSession, readSecureSession, writeSecureSession } from "@/auth/session";

export type MobileConnectionState =
  | "connecting"
  | "connected"
  | "offline"
  | "backend_unavailable"
  | "authentication_required";

interface WorkStationContextValue {
  state: MobileConnectionState;
  client: MobileApiClient | null;
  user: CurrentUser | null;
  error: string | null;
  connect: (token: string) => Promise<void>;
  retry: () => Promise<void>;
  rotateSession: () => Promise<void>;
  logout: () => Promise<void>;
}

const WorkStationContext = createContext<WorkStationContextValue | null>(null);

export function WorkStationProvider({ children }: PropsWithChildren) {
  const [state, setState] = useState<MobileConnectionState>("connecting");
  const [client, setClient] = useState<MobileApiClient | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stateRef = useRef<MobileConnectionState>("connecting");
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const restore = useCallback(async () => {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setState("connecting");
    setError(null);
    let token: string | null;
    try {
      token = await readSecureSession();
    } catch {
      setState("authentication_required");
      setError("Secure device storage is unavailable.");
      return;
    }
    if (token === null) {
      setState("authentication_required");
      return;
    }
    const restored = new MobileApiClient(token);
    try {
      const current = await restored.getCurrentUser(controller.signal);
      if (controller.signal.aborted) return;
      setClient(restored);
      setUser(current);
      setState("connected");
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (cause instanceof MobileApiError && cause.kind === "authentication") {
        await clearSecureSession().catch(() => undefined);
        setClient(null);
        setUser(null);
        setError("The saved session is no longer valid.");
        setState("authentication_required");
        return;
      }
      const network = await NetInfo.fetch();
      setError("Your saved session is preserved. Reconnect when the backend is available.");
      setState(network.isConnected === false ? "offline" : "backend_unavailable");
    }
  }, []);

  useEffect(() => {
    const initialRestore = setTimeout(() => void restore(), 0);
    const networkSubscription = NetInfo.addEventListener((network) => {
      if (network.isConnected === false && stateRef.current === "connected") {
        setState("offline");
      } else if (
        network.isConnected !== false &&
        ["offline", "backend_unavailable"].includes(stateRef.current)
      ) {
        void restore();
      }
    });
    const appSubscription = AppState.addEventListener("change", (next) => {
      if (
        next === "active" &&
        ["offline", "backend_unavailable"].includes(stateRef.current)
      ) {
        void restore();
      }
    });
    return () => {
      clearTimeout(initialRestore);
      activeRequest.current?.abort();
      networkSubscription();
      appSubscription.remove();
    };
  }, [restore]);

  const connect = useCallback(async (token: string) => {
    setState("connecting");
    setError(null);
    const candidate = new MobileApiClient(token);
    try {
      const current = await candidate.getCurrentUser();
      await writeSecureSession(token);
      setClient(candidate);
      setUser(current);
      setState("connected");
    } catch (cause) {
      setClient(null);
      setUser(null);
      setError(
        cause instanceof MobileApiError && cause.kind === "authentication"
          ? "Authentication failed."
          : "WORK STATION could not connect. The credential was not saved.",
      );
      setState("authentication_required");
    }
  }, []);

  const logout = useCallback(async () => {
    activeRequest.current?.abort();
    try {
      await client?.revokeCurrentUserSession();
    } catch {
      // Local secure-session removal must remain available while offline.
    }
    await clearSecureSession();
    setClient(null);
    setUser(null);
    setError(null);
    setState("authentication_required");
  }, [client]);

  const rotateSession = useCallback(async () => {
    if (client === null) throw new Error("An authenticated session is required.");
    const rotated = await client.rotateAccessToken();
    const replacement = new MobileApiClient(rotated.access_token);
    setClient(replacement);
    try {
      await writeSecureSession(rotated.access_token);
      setError(null);
    } catch {
      setError("The rotated session could not be saved. Keep the app open and retry.");
      throw new Error("Secure device storage is unavailable.");
    }
  }, [client]);

  const value = useMemo<WorkStationContextValue>(
    () => ({
      state,
      client,
      user,
      error,
      connect,
      retry: restore,
      rotateSession,
      logout,
    }),
    [client, connect, error, logout, restore, rotateSession, state, user],
  );
  return <WorkStationContext.Provider value={value}>{children}</WorkStationContext.Provider>;
}

export function useWorkStation(): WorkStationContextValue {
  const context = useContext(WorkStationContext);
  if (context === null) throw new Error("WorkStationProvider is required.");
  return context;
}
