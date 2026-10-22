import type { PointerEvent as ReactPointerEvent } from "react";

// Totals are the pointer's screen position minus where the button went down.
// The main process applies that total to the bounds it captured at
// pointer-down, and prefers the OS cursor when the cursor actually moves.
// clientX snaps back once the window follows the pointer. movementX on this
// session has included a multi-thousand-pixel stale sample. Neither is used.

export function trackPointer(
  event: ReactPointerEvent<HTMLElement>,
  onFrame: (dx: number, dy: number) => void | Promise<unknown>,
  onDone: () => void,
) {
  if (event.button !== 0) return false;
  event.preventDefault();
  const node = event.currentTarget;
  try {
    node.setPointerCapture(event.pointerId);
  } catch {
    // Listening on the node still receives the gesture when capture is refused.
  }
  const originX = event.screenX;
  const originY = event.screenY;
  let latest: PointerEvent | null = null;
  let frame = 0;
  let finishing = false;
  let ended = false;

  const finishNow = () => {
    if (ended) return;
    ended = true;
    onDone();
  };

  const emit = (ev: PointerEvent) => {
    const dx = ev.screenX - originX;
    const dy = ev.screenY - originY;
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return;
    if (dx === 0 && dy === 0) return;
    onFrame(dx, dy);
  };

  // One sample per frame. The previous path waited for each IPC reply, so
  // the window trailed the pointer.
  const flush = () => {
    frame = 0;
    const ev = latest;
    latest = null;
    if (ev) emit(ev);
    if (finishing) finishNow();
  };

  const move = (ev: PointerEvent) => {
    if (finishing || ended) return;
    latest = ev;
    if (frame) return;
    frame = requestAnimationFrame(flush);
  };
  const finish = () => {
    if (finishing || ended) return;
    finishing = true;
    node.removeEventListener("pointermove", move);
    node.removeEventListener("pointerup", finish);
    node.removeEventListener("pointercancel", finish);
    if (frame) cancelAnimationFrame(frame);
    flush();
  };
  node.addEventListener("pointermove", move);
  node.addEventListener("pointerup", finish);
  node.addEventListener("pointercancel", finish);
  return true;
}
