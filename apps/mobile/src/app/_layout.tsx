import { Tabs } from "expo-router";
import { StatusBar } from "expo-status-bar";

import { WorkStationProvider } from "@/context/work-station";
import { AppearanceProvider, useWorkStationAppearance } from "@/theme/appearance";

function Navigation() {
  const { colors, scheme } = useWorkStationAppearance();
  return (
    <>
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
        <Tabs.Screen name="index" options={{ title: "Home", headerTitle: "AI Presence" }} />
        <Tabs.Screen name="calls" options={{ title: "Calls", headerTitle: "Communications" }} />
        <Tabs.Screen name="agents" options={{ title: "Missions", headerTitle: "Mission Control" }} />
        <Tabs.Screen name="studio" options={{ title: "Workspaces", headerTitle: "Universal Workspace" }} />
        <Tabs.Screen name="settings" options={{ title: "Command", headerTitle: "AI Command Center" }} />
      </Tabs>
    </>
  );
}

export default function RootLayout() {
  return (
    <AppearanceProvider>
      <WorkStationProvider>
        <Navigation />
      </WorkStationProvider>
    </AppearanceProvider>
  );
}
