"use strict";

// Edge tiling for one monitor's work area. The pointer has to be in the
// outer 16px. A title-bar grab sitting just under the top bar is not the
// top edge, so sliding along the top into the left side still means left half.

const EDGE = 16;
const SEAM = 64;

function rect(x, y, width, height, maximized) {
  return { x, y, width, height, maximized };
}

function tileForPoint(point, area) {
  if (!point || !area) return null;
  const x = Number(point.x);
  const y = Number(point.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const nearLeft = x <= area.x + EDGE;
  const nearRight = x >= area.x + area.width - EDGE;
  const nearTop = y <= area.y + EDGE;
  const nearBottom = y >= area.y + area.height - EDGE;
  const halfW = Math.round(area.width / 2);
  const halfH = Math.round(area.height / 2);
  if (nearLeft && nearTop) return rect(area.x, area.y, halfW, halfH, false);
  if (nearRight && nearTop) return rect(area.x + area.width - halfW, area.y, halfW, halfH, false);
  if (nearLeft && nearBottom) return rect(area.x, area.y + area.height - halfH, halfW, halfH, false);
  if (nearRight && nearBottom) {
    return rect(area.x + area.width - halfW, area.y + area.height - halfH, halfW, halfH, false);
  }
  if (nearLeft) return rect(area.x, area.y, halfW, area.height, false);
  if (nearRight) return rect(area.x + area.width - halfW, area.y, halfW, area.height, false);
  if (nearTop) return rect(area.x, area.y, area.width, area.height, true);
  if (nearBottom) return rect(area.x, area.y + area.height - halfH, area.width, halfH, false);
  return null;
}

function tilesForArea(area) {
  const halfW = Math.round(area.width / 2);
  const halfH = Math.round(area.height / 2);
  return [
    rect(area.x, area.y, halfW, area.height, false),
    rect(area.x + area.width - halfW, area.y, halfW, area.height, false),
    rect(area.x, area.y, area.width, area.height, true),
    rect(area.x, area.y + area.height - halfH, area.width, halfH, false),
    rect(area.x, area.y, halfW, halfH, false),
    rect(area.x + area.width - halfW, area.y, halfW, halfH, false),
    rect(area.x, area.y + area.height - halfH, halfW, halfH, false),
    rect(area.x + area.width - halfW, area.y + area.height - halfH, halfW, halfH, false),
  ];
}

// Same rules as a Fedora window: only the pointer hitting the monitor edge
// tiles it. A title bar that is already in the top band is not a fill.
function tileForDrag({ cursor, start, area }) {
  if (!cursor || !area) return null;
  if (start) {
    const moved = Math.abs(cursor.x - start.x) + Math.abs(cursor.y - start.y);
    if (moved < 8) return null;
  }
  const fromTopBand = Boolean(start) && start.y <= area.y + EDGE;
  let pointer = tileForPoint(cursor, area);
  if (pointer && fromTopBand && pointerUsesTop(pointer, area)) {
    pointer = tileForPoint({ x: cursor.x, y: area.y + EDGE + 1 }, area);
    if (pointer && pointer.maximized) pointer = null;
  }
  return pointer || null;
}

// Two monitors share an edge the pointer can cross. Treat a band around that
// edge as a side of each monitor: coming from the left takes the left
// monitor's right half, and coming from the right takes the right monitor's
// left half.
function tileForSeam(cursor, start, areas) {
  if (!cursor || !start || !Array.isArray(areas)) return null;
  const moved = Math.abs(cursor.x - start.x) + Math.abs(cursor.y - start.y);
  if (moved < 8) return null;
  for (let i = 0; i < areas.length; i += 1) {
    const left = areas[i];
    if (!left) continue;
    for (let j = 0; j < areas.length; j += 1) {
      const right = areas[j];
      if (!right || right === left) continue;
      const boundary = right.x;
      if (Math.abs(left.x + left.width - boundary) > 2) continue;
      const top = Math.max(left.y, right.y);
      const bottom = Math.min(left.y + left.height, right.y + right.height);
      if (bottom - top < 80) continue;
      if (cursor.y < top - 24 || cursor.y > bottom + 24) continue;
      if (Math.abs(cursor.x - boundary) > SEAM) continue;
      const halfLeft = Math.round(left.width / 2);
      const halfRight = Math.round(right.width / 2);
      if (start.x < boundary) {
        return rect(left.x + left.width - halfLeft, left.y, halfLeft, left.height, false);
      }
      return rect(right.x, right.y, halfRight, right.height, false);
    }
  }
  return null;
}

// The pointer stays on the title bar, so it often never reaches the shared
// edge. The window's leading edge does. Snap when that edge meets the join
// and most of the window is still on the monitor it came from.
function tileForWindowSeam(bounds, started, areas) {
  if (!bounds || !started || !Array.isArray(areas)) return null;
  const dx = bounds.x - started.x;
  if (Math.abs(dx) < 12) return null;
  for (let i = 0; i < areas.length; i += 1) {
    const left = areas[i];
    if (!left) continue;
    for (let j = 0; j < areas.length; j += 1) {
      const right = areas[j];
      if (!right || right === left) continue;
      const boundary = right.x;
      if (Math.abs(left.x + left.width - boundary) > 2) continue;
      const rightEdge = bounds.x + bounds.width;
      const center = bounds.x + bounds.width / 2;
      const halfLeft = Math.round(left.width / 2);
      const halfRight = Math.round(right.width / 2);
      if (dx > 0 && rightEdge >= boundary - 40 && center < boundary + 80) {
        return rect(left.x + left.width - halfLeft, left.y, halfLeft, left.height, false);
      }
      if (dx < 0 && bounds.x <= boundary + 40 && center > boundary - 80) {
        return rect(right.x, right.y, halfRight, right.height, false);
      }
    }
  }
  return null;
}

function pointerUsesTop(tile, area) {
  return tile.y <= area.y + 1 && (tile.maximized || tile.height < area.height);
}

function sameRect(a, b, slop) {
  const slack = slop || 0;
  return Math.abs(a.x - b.x) <= slack
    && Math.abs(a.y - b.y) <= slack
    && Math.abs(a.width - b.width) <= slack
    && Math.abs(a.height - b.height) <= slack;
}

module.exports = {
  EDGE, SEAM, tileForPoint, tileForDrag, tileForSeam, tileForWindowSeam, tilesForArea, sameRect,
};
