import { useEffect, useState } from "react";

/**
 * Returns `value` after it has been stable for `delayMs`. Useful for
 * debouncing query inputs to avoid hitting the server on every keystroke.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
