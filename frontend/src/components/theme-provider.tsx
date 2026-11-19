"use client";

import { usePathname } from "next/navigation";
import { ThemeProvider as NextThemesProvider } from "next-themes";
import { createContext, useContext, useEffect, useState } from "react";

// Keep the HypoClips dark palette across public and signed-in pages.
const APP_ROUTE = /^\/(list|tasks|settings|analytics|admin)(\/|$)/;

const AppThemeScope = createContext<(active: boolean) => void>(() => {});

/** Dark by default across the application. */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const [shellActive, setShellActive] = useState(false);
  const themed = shellActive || APP_ROUTE.test(pathname);

  return (
    <AppThemeScope.Provider value={setShellActive}>
      <NextThemesProvider
        attribute="class"
        defaultTheme="dark"
        enableSystem={false}
        forcedTheme={themed ? undefined : "dark"}
        disableTransitionOnChange
      >
        {children}
      </NextThemesProvider>
    </AppThemeScope.Provider>
  );
}

/**
 * Applies the saved theme while the signed-in app shell is mounted. "/" is
 * both the landing page and the signed-in Create page, so the route alone
 * cannot tell them apart.
 */
export function useAppTheme(active: boolean) {
  const setShellActive = useContext(AppThemeScope);
  useEffect(() => {
    if (!active) return;
    setShellActive(true);
    return () => setShellActive(false);
  }, [active, setShellActive]);
}
