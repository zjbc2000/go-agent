// ============================================================
// UI store — sidebar collapse, theme, and global UI state
// ============================================================

import { create } from "zustand";

interface UIStore {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  // Mobile detection
  isMobile: boolean;
  setIsMobile: (v: boolean) => void;
}

export const useUIStore = create<UIStore>((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (sidebarOpen: boolean) => set({ sidebarOpen }),
  isMobile: false,
  setIsMobile: (isMobile: boolean) => set({ isMobile, sidebarOpen: !isMobile }),
}));
