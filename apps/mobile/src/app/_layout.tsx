import { Tabs } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useColorScheme } from "react-native";

import { WorkStationProvider } from "@/context/work-station";
import { workStationColors } from "@/theme/colors";

export default function RootLayout() {
  const scheme = useColorScheme();
  const colors = workStationColors(scheme);
  return (
    <WorkStationProvider>
      <StatusBar style={scheme === "light" ? "dark" : "light"} />
      <Tabs
        screenOptions={{
          sceneStyle: { backgroundColor: colors.background },
          headerStyle: { backgroundColor: colors.panel },
          headerTintColor: colors.text,
          headerTitleStyle: { fontWeight: "800" },
          tabBarStyle: { backgroundColor: colors.panel, borderTopColor: colors.line },
          tabBarActiveTintColor: colors.accent,
          tabBarInactiveTintColor: colors.muted,
        }}
      >
        <Tabs.Screen name="index" options={{ title: "Chats", headerTitle: "WORK STATION" }} />
        <Tabs.Screen name="settings" options={{ title: "Settings", headerTitle: "Settings & diagnostics" }} />
      </Tabs>
    </WorkStationProvider>
  );
}
