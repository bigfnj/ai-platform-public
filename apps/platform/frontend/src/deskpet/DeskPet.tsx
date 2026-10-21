import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import eSheep from "./esheep.js";
import defaultPet from "./animation.xml?raw";
import { pickArrive, pickIdle, pickWelcome } from "./lines";
import { BUBBLE_MARGIN, BUBBLE_MAX_W, placeBubble } from "./bubble";

const IDLE_MS = 75_000; // how often the pet *might* drop an idle quip (jittered by chance below)
const WELCOME_MS = 10_000; // delay before the personalized welcome, after the pet loads

/**
 * Platform desktop pet — a cosmetic web-esheep with a scripted, offline "brain".
 *
 * Mounted once in the shell (sibling of <AppShell>) so it roams across every rail. The pet
 * injects its own viewport-fixed DOM into document.body; this component adds a themed speech
 * bubble that tracks the pet and shows short scripted lines. It is rail-aware (the shell
 * passes the active rail) and lightly content-aware (it reads a visible heading from the DOM
 * and runs keyword triggers). No model, no broker, no network.
 */
export function DeskPet({ rail, railLabel, username }: { rail: string; railLabel: string; username: string }) {
  const petRef = useRef<InstanceType<typeof eSheep> | null>(null);
  const [bubble, setBubble] = useState<string | null>(null);
  // The pet's centre x and its top and bottom y, in viewport px.
  const [pos, setPos] = useState<{ x: number; top: number; bottom: number }>({ x: -999, top: -999, bottom: -999 });
  // The bubble's own size, measured: its width follows its text, not where it sits (bubble.ts).
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const boxRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<number | undefined>(undefined);
  const rafRef = useRef<number | undefined>(undefined);
  const firstArrive = useRef(true); // skip the very first rail greeting so the welcome lands first
  const railRef = useRef(rail); // read by the idle timer, which must NOT restart on rail switches

  const say = useCallback((text: string | null) => {
    if (!text) return;
    setBubble(text);
    window.clearTimeout(hideTimer.current);
    const ms = Math.min(9000, Math.max(3500, text.length * 90));
    hideTimer.current = window.setTimeout(() => setBubble(null), ms);
  }, []);

  // A cheap on-screen signal: the most prominent visible heading in the active rail.
  const snippet = useCallback((): string => {
    const el = document.querySelector<HTMLElement>(".pcontent h1, .pcontent h2, .pcontent h3");
    return (el?.textContent || "").trim().slice(0, 80);
  }, []);

  // Create the pet once; tear it down on unmount.
  useEffect(() => {
    const pet = new eSheep({ allowPopup: "no", allowPets: "none" });
    petRef.current = pet;
    pet.Start(defaultPet).catch(() => {});
    return () => {
      window.clearTimeout(hideTimer.current);
      try {
        pet.remove();
      } catch {
        /* already gone */
      }
      petRef.current = null;
    };
  }, []);

  // Follow the pet while a bubble is showing (its live viewport coords are imageX/Y/W).
  useEffect(() => {
    if (!bubble) return;
    const tick = () => {
      const p = petRef.current as unknown as { imageX?: number; imageY?: number; imageW?: number; imageH?: number };
      if (p && typeof p.imageX === "number" && typeof p.imageY === "number") {
        const x = p.imageX + (p.imageW || 64) / 2;
        const top = p.imageY;
        const bottom = p.imageY + (p.imageH || 64);
        // Bail when the pet hasn't moved: returning the previous object lets React skip the
        // render, instead of ~500 re-renders per bubble (60fps × 3.5-9s).
        setPos((prev) => (prev.x === x && prev.top === top && prev.bottom === bottom ? prev : { x, top, bottom }));
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [bubble]);

  // Personalized welcome ~10s after the pet loads — i.e. on each login / page load (a hard
  // refresh re-fires it). The [username] dep keeps it from firing on rail switches.
  useEffect(() => {
    if (!username) return;
    const t = window.setTimeout(() => say(pickWelcome(username)), WELCOME_MS);
    return () => window.clearTimeout(t);
  }, [username, say]);

  // Greet on rail change (small delay so the new rail has painted its heading). The first
  // invocation (initial mount) is skipped so it doesn't step on the welcome.
  useEffect(() => {
    if (!rail) return;
    if (firstArrive.current) {
      firstArrive.current = false;
      return;
    }
    const t = window.setTimeout(() => say(pickArrive(rail, railLabel)), 700);
    return () => window.clearTimeout(t);
  }, [rail, railLabel, say]);

  useEffect(() => {
    railRef.current = rail;
  }, [rail]);

  // Occasional idle quip, only while the tab is visible. `rail` is read through a ref so the
  // interval is NOT torn down and restarted on every rail switch — otherwise anyone moving
  // between rails more often than IDLE_MS never reaches a tick.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.hidden) return;
      if (Math.random() < 0.5) say(pickIdle(railRef.current, snippet()));
    }, IDLE_MS);
    return () => window.clearInterval(id);
  }, [say, snippet]);

  // Measured before paint, so the first frame is already placed. Only the text changes the size.
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
  }, [bubble]);

  if (!bubble) return null;
  const at = placeBubble(pos, size, { w: window.innerWidth, h: window.innerHeight });
  return (
    <div
      ref={boxRef}
      role="status"
      style={{
        position: "fixed",
        left: at.left,
        top: at.top,
        zIndex: 2001,
        // max-content: sized by the text, so a pet near the right edge no longer squeezes it
        // to one word per line; the cap is the old 220 px, or the window less its margins.
        width: "max-content",
        maxWidth: `min(${BUBBLE_MAX_W}px, calc(100vw - ${2 * BUBBLE_MARGIN}px))`,
        padding: "8px 12px",
        background: "var(--surface-1, #fff)",
        color: "var(--text-primary, #111)",
        border: "1px solid var(--border, #ccc)",
        borderRadius: "var(--radius, 12px)",
        boxShadow: "var(--shadow, 0 6px 20px rgba(0,0,0,.25))",
        font: "13px/1.35 system-ui, -apple-system, sans-serif",
        pointerEvents: "none",
        whiteSpace: "normal",
      }}
    >
      {bubble}
    </div>
  );
}
