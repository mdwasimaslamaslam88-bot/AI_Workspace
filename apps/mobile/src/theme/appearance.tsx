import * as SecureStore from "expo-secure-store";
import { createContext, type PropsWithChildren, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useColorScheme } from "react-native";

import {
  resolvedAppearanceScheme,
  workStationColors,
  type MobileAppearancePreference,
} from "@/theme/colors";
export type { MobileAppearancePreference } from "@/theme/colors";

const STORAGE_KEY = "work-station.appearance.v1";

interface AppearanceContextValue {
  preference: MobileAppearancePreference;
  setPreference: (preference: MobileAppearancePreference) => Promise<void>;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

function isPreference(value: string | null): value is MobileAppearancePreference {
  return value === "light" || value === "dark" || value === "system";
}

export function AppearanceProvider({ children }: PropsWithChildren) {
  const [preference, setPreferenceState] = useState<MobileAppearancePreference>("system");

  useEffect(() => {
    let active = true;
    void SecureStore.getItemAsync(STORAGE_KEY)
      .then((stored) => {
        if (active && isPreference(stored)) setPreferenceState(stored);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const setPreference = useCallback(async (next: MobileAppearancePreference) => {
    setPreferenceState(next);
    try {
      await SecureStore.setItemAsync(STORAGE_KEY, next);
    } catch (error) {
      setPreferenceState((current) => (current === next ? "system" : current));
      throw error;
    }
  }, []);

  const value = useMemo(() => ({ preference, setPreference }), [preference, setPreference]);
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

export function useWorkStationAppearance() {
  const system = useColorScheme();
  const context = useContext(AppearanceContext);
  const preference = context?.preference ?? "system";
  const scheme = resolvedAppearanceScheme(preference, system);
  return {
    preference,
    setPreference: context?.setPreference,
    scheme,
    colors: workStationColors(scheme),
  };
}
