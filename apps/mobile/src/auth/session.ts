import * as SecureStore from "expo-secure-store";

const SESSION_KEY = "work-station.owner-session";
const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export async function readSecureSession(): Promise<string | null> {
  return SecureStore.getItemAsync(SESSION_KEY, OPTIONS);
}

export async function writeSecureSession(token: string): Promise<void> {
  if (token.length === 0 || token.length > 512) throw new Error("A valid session is required.");
  await SecureStore.setItemAsync(SESSION_KEY, token, OPTIONS);
}

export async function clearSecureSession(): Promise<void> {
  await SecureStore.deleteItemAsync(SESSION_KEY, OPTIONS);
}
