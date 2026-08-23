import * as Notifications from "expo-notifications";

export async function requestPrivateNotificationPermission(): Promise<boolean> {
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  return (await Notifications.requestPermissionsAsync()).granted;
}

export async function notifyTaskFinished(succeeded: boolean): Promise<void> {
  // Notification previews intentionally contain no prompts, model output,
  // conversation names, filenames, or other private content.
  await Notifications.scheduleNotificationAsync({
    content: {
      title: succeeded ? "WORK STATION task finished" : "WORK STATION task needs attention",
      body: "Open WORK STATION to review the private result.",
      data: { route: "/" },
    },
    trigger: null,
  });
}
