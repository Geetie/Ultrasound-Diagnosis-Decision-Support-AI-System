import { create } from 'zustand';

interface UiState {
  isDarkRoomMode: boolean;
  sidebarCollapsed: boolean;
  activePanel: 'diagnosis' | 'session' | 'settings' | null;
  toggleDarkRoomMode: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setActivePanel: (panel: 'diagnosis' | 'session' | 'settings' | null) => void;
}

export const useUiStore = create<UiState>((set) => ({
  isDarkRoomMode: false,
  sidebarCollapsed: false,
  activePanel: null,
  toggleDarkRoomMode: () => set((state) => ({ isDarkRoomMode: !state.isDarkRoomMode })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  setActivePanel: (panel) => set({ activePanel: panel }),
}));
