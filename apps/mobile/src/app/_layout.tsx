import { Tabs } from "expo-router";
import { StatusBar } from "expo-status-bar";

import { WorkStationProvider } from "@/context/work-station";

export default function RootLayout() {
  return (
    <WorkStationProvider>
      <StatusBar style="light" />
      <Tabs
        screenOptions={{
          headerStyle: { backgroundColor: "#07152f" },
          headerTintColor: "#e8edf4",
          headerTitleStyle: { fontWeight: "800" },
          tabBarStyle: { backgroundColor: "#07152f", borderTopColor: "#263b58" },
          tabBarActiveTintColor: "#68efc8",
          tabBarInactiveTintColor: "#9ba9ba",
        }}
      >
        <Tabs.Screen name="index" options={{ title: "Chats", headerTitle: "WORK STATION" }} />
        <Tabs.Screen name="settings" options={{ title: "Settings", headerTitle: "Settings & diagnostics" }} />
      </Tabs>
    </WorkStationProvider>
  );
}
