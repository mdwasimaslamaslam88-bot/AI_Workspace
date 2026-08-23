import {
  clearDesktopSessionToken,
  isDesktopRuntime,
  readDesktopSessionToken,
  writeDesktopSessionToken,
} from "../platform/desktop";
import {
  clearSessionToken,
  readSessionToken,
  writeSessionToken,
} from "./session";

export class SessionPersistenceError extends Error {
  constructor() {
    super("Secure session storage is unavailable.");
    this.name = "SessionPersistenceError";
  }
}

export async function readPersistedSessionToken(): Promise<string | null> {
  try {
    return isDesktopRuntime() ? await readDesktopSessionToken() : readSessionToken();
  } catch {
    throw new SessionPersistenceError();
  }
}

export async function writePersistedSessionToken(token: string): Promise<void> {
  if (isDesktopRuntime()) {
    try {
      await writeDesktopSessionToken(token);
    } catch {
      throw new SessionPersistenceError();
    }
    return;
  }
  try {
    writeSessionToken(token);
  } catch {
    throw new SessionPersistenceError();
  }
}

export async function clearPersistedSessionToken(): Promise<void> {
  if (isDesktopRuntime()) {
    try {
      await clearDesktopSessionToken();
    } catch {
      throw new SessionPersistenceError();
    }
    return;
  }
  clearSessionToken();
}
