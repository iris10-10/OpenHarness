import { create } from "zustand";

interface SettingsState {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  collapsed: false,
  setCollapsed: (collapsed) => set({ collapsed }),
}));
