import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      gcTime: 10 * 60 * 1000,
      retry: 2,
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 30000),
      refetchOnWindowFocus: false,
    },
    mutations: {
      // OpenMRS write endpoints (POST /patient, /encounter, /order, /visit)
      // are not idempotent. Auto-retrying on transient failures can create
      // duplicate patients/encounters when the first call actually succeeded
      // but the response was lost. Retry must be opt-in per call site.
      retry: false,
    },
  },
});
