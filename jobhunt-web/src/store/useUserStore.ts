import { create } from "zustand";

interface UserState {
  name: string;
  setName: (name: string) => void;
}

export const useUserStore = create<UserState>((set) => ({
  name: "求职者",
  setName: (name) => set({ name }),
}));
