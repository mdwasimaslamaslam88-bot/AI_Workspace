import Constants from "expo-constants";

type PrivateNotificationsModule = Pick<
  typeof import("expo-notifications"),
  "getPermissionsAsync" | "requestPermissionsAsync" | "scheduleNotificationAsync"
>;

type PrivateNotificationsLoader = () => Promise<PrivateNotificationsModule | null>;
type PrivateNotificationsImporter = () => Promise<PrivateNotificationsModule>;

export async function loadPrivateNotifications(
  isExpoGo = typeof Constants.expoVersion === "string",
  importNotifications: PrivateNotificationsImporter = () => import("expo-notifications"),
): Promise<PrivateNotificationsModule | null> {
  // Expo Go intentionally throws while evaluating expo-notifications on
  // Android. Detect it before import so the exception cannot reach Metro's
  // development overlay. Native development and production builds expose no
  // Expo Go version and continue through the real module.
  if (isExpoGo) return null;
  try {
    return await importNotifications();
  } catch {
    return null;
  }
}

export async function requestPrivateNotificationPermission(
  loadNotifications: PrivateNotificationsLoader = loadPrivateNotifications,
): Promise<boolean> {
  const notifications = await loadNotifications();
  if (notifications === null) return false;
  try {
    const current = await notifications.getPermissionsAsync();
    if (current.granted) return true;
    return (await notifications.requestPermissionsAsync()).granted;
  } catch {
    return false;
  }
}

export function privateTaskNotificationContent(succeeded: boolean) {
  return {
    title: succeeded ? "WORK STATION task finished" : "WORK STATION task needs attention",
    body: "Open WORK STATION to review the private result.",
    data: { route: "/" },
  } as const;
}

export async function notifyTaskFinished(
  succeeded: boolean,
  loadNotifications: PrivateNotificationsLoader = loadPrivateNotifications,
): Promise<void> {
  // Notification previews intentionally contain no prompts, model output,
  // conversation names, filenames, or other private content.
  const notifications = await loadNotifications();
  if (notifications === null) return;
  await notifications.scheduleNotificationAsync({
    content: privateTaskNotificationContent(succeeded),
    trigger: null,
  });
}
