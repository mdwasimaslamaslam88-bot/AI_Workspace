import * as Clipboard from "expo-clipboard";

export async function copyPrivateMessageContent(content: string): Promise<void> {
  await Clipboard.setStringAsync(content);
}
