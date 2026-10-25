import { useEffect, useRef, useState } from "react";

const IDLE_AFTER_MS = 60_000;

/**
 * Counts ACTIVE time only: time stops after 60 s without mouse or keyboard input.
 * Returns the active milliseconds for the current key (e.g. image) and whether the user is idle.
 * `onFlush(ms)` is called when the key changes or the component unmounts.
 */
export function useActiveTime(key: string, onFlush: (ms: number, key: string) => void) {
  const [ms, setMs] = useState(0);
  const [idle, setIdle] = useState(false);
  const lastInput = useRef(Date.now());
  const msRef = useRef(0);
  const flushRef = useRef(onFlush);
  flushRef.current = onFlush;

  useEffect(() => {
    const touch = () => {
      lastInput.current = Date.now();
    };
    window.addEventListener("pointermove", touch);
    window.addEventListener("pointerdown", touch);
    window.addEventListener("keydown", touch);
    window.addEventListener("wheel", touch);
    return () => {
      window.removeEventListener("pointermove", touch);
      window.removeEventListener("pointerdown", touch);
      window.removeEventListener("keydown", touch);
      window.removeEventListener("wheel", touch);
    };
  }, []);

  useEffect(() => {
    msRef.current = 0;
    setMs(0);
    lastInput.current = Date.now();
    const t = setInterval(() => {
      const isIdle = Date.now() - lastInput.current > IDLE_AFTER_MS || document.hidden;
      setIdle(isIdle);
      if (!isIdle) {
        msRef.current += 1000;
        setMs(msRef.current);
      }
    }, 1000);
    return () => {
      clearInterval(t);
      if (msRef.current > 0) flushRef.current(msRef.current, key);
    };
  }, [key]);

  return { ms, idle };
}
