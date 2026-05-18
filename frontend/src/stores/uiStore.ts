import { create } from "zustand";

interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: "default" | "success" | "warning" | "destructive";
}

interface UiState {
  sidebarOpen: boolean;
  toasts: Toast[];
  /**
   * In-session cache of the current user's selected location. The durable
   * source of truth is the OpenMRS user property `userProperties.defaultLocation`
   * — see `useCurrentUserDefaultLocation` / `useSetDefaultLocation`. We mirror
   * the value here so synchronous code paths (Sidebar, StartVisitDialog) can
   * read it without an extra round-trip.
   */
  defaultLocationUuid: string | null;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setDefaultLocationUuid: (uuid: string | null) => void;
  addToast: (toast: Omit<Toast, "id">) => void;
  removeToast: (id: string) => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  sidebarOpen: true,
  toasts: [],
  defaultLocationUuid: null,

  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setDefaultLocationUuid: (uuid) => set({ defaultLocationUuid: uuid }),

  addToast: (toast) => {
    const id = Math.random().toString(36).slice(2);
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
    setTimeout(() => get().removeToast(id), 5000);
  },

  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

// Helper hook
export const toast = {
  success: (title: string, description?: string) =>
    useUiStore.getState().addToast({ title, description, variant: "success" }),
  error: (title: string, description?: string) =>
    useUiStore.getState().addToast({ title, description, variant: "destructive" }),
  warning: (title: string, description?: string) =>
    useUiStore.getState().addToast({ title, description, variant: "warning" }),
  info: (title: string, description?: string) =>
    useUiStore.getState().addToast({ title, description, variant: "default" }),
};
