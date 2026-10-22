"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { tileForPoint, tileForDrag, tileForSeam, tileForWindowSeam } = require("./tile");

const left = { x: 0, y: 32, width: 1920, height: 1048 };
const right = { x: 1920, y: 32, width: 1920, height: 1048 };

test("left edge tiles the left half of that monitor", () => {
  const tile = tileForPoint({ x: 4, y: 400 }, left);
  assert.deepEqual(tile, { x: 0, y: 32, width: 960, height: 1048, maximized: false });
});

test("a title-bar grab under the top bar is still a side tile", () => {
  const tile = tileForPoint({ x: 4, y: 56 }, left);
  assert.equal(tile.height, 1048);
  assert.equal(tile.maximized, false);
});

test("right edge tiles the right half of that monitor", () => {
  const tile = tileForPoint({ x: 1910, y: 400 }, left);
  assert.equal(tile.x, 960);
  assert.equal(tile.width, 960);
});

test("the next monitor tiles itself, not the whole desktop", () => {
  const tile = tileForPoint({ x: 1924, y: 500 }, right);
  assert.deepEqual(tile, { x: 1920, y: 32, width: 960, height: 1048, maximized: false });
  const far = tileForPoint({ x: 3830, y: 500 }, right);
  assert.equal(far.x, 2880);
  assert.equal(far.width, 960);
});

test("top edge fills the monitor and corners take a quarter", () => {
  const top = tileForPoint({ x: 800, y: 40 }, left);
  assert.equal(top.maximized, true);
  assert.equal(top.width, 1920);
  assert.equal(top.height, 1048);
  const corner = tileForPoint({ x: 4, y: 40 }, left);
  assert.deepEqual(corner, { x: 0, y: 32, width: 960, height: 524, maximized: false });
});

test("bottom edge takes the bottom half", () => {
  const tile = tileForPoint({ x: 800, y: 1070 }, left);
  assert.equal(tile.y, 32 + 524);
  assert.equal(tile.height, 524);
  assert.equal(tile.width, 1920);
});

test("the middle of a monitor does not tile", () => {
  assert.equal(tileForPoint({ x: 400, y: 400 }, left), null);
  assert.equal(tileForPoint({ x: 2500, y: 400 }, right), null);
});

const liveRight = { x: 1920, y: 0, width: 1920, height: 1080 };

test("a title-bar drag along the top of a full-height window does not fill the monitor", () => {
  const tile = tileForDrag({
    cursor: { x: 2600, y: 14 },
    start: { x: 2500, y: 14 },
    bounds: { x: 2100, y: 0, width: 1056, height: 1080 },
    area: liveRight,
  });
  assert.equal(tile, null);
});

test("the window touching a side does not tile while the pointer is in the middle", () => {
  const tile = tileForDrag({
    cursor: { x: 2400, y: 200 },
    start: { x: 2600, y: 200 },
    bounds: { x: 1930, y: 0, width: 1056, height: 1080 },
    area: liveRight,
  });
  assert.equal(tile, null);
});

test("the pointer on the right edge tiles that half", () => {
  const tile = tileForDrag({
    cursor: { x: 3830, y: 240 },
    start: { x: 3000, y: 240 },
    area: liveRight,
  });
  assert.deepEqual(tile, { x: 2880, y: 0, width: 960, height: 1080, maximized: false });
});

test("pushing up into the top edge from below fills the monitor", () => {
  const tile = tileForDrag({
    cursor: { x: 2500, y: 8 },
    start: { x: 2500, y: 200 },
    bounds: { x: 2200, y: 80, width: 1056, height: 900 },
    area: liveRight,
  });
  assert.equal(tile.maximized, true);
  assert.equal(tile.width, 1920);
});

const pair = [
  { x: 0, y: 32, width: 1920, height: 1048 },
  { x: 1920, y: 0, width: 1920, height: 1080 },
];

test("the shared edge from the left monitor takes that monitor's right half", () => {
  const tile = tileForSeam({ x: 1940, y: 400 }, { x: 700, y: 400 }, pair);
  assert.deepEqual(tile, { x: 960, y: 32, width: 960, height: 1048, maximized: false });
});

test("the shared edge from the right monitor takes that monitor's left half", () => {
  const tile = tileForSeam({ x: 1888, y: 500 }, { x: 2600, y: 420 }, pair);
  assert.deepEqual(tile, { x: 1920, y: 0, width: 960, height: 1080, maximized: false });
});

test("pushing the window into the shared edge takes the right half of the left monitor", () => {
  const tile = tileForWindowSeam(
    { x: 1100, y: 140, width: 960, height: 936 },
    { x: 200, y: 140, width: 960, height: 936 },
    pair,
  );
  assert.deepEqual(tile, { x: 960, y: 32, width: 960, height: 1048, maximized: false });
});

test("a window still short of the shared edge is not snapped", () => {
  assert.equal(tileForWindowSeam(
    { x: 400, y: 140, width: 960, height: 936 },
    { x: 200, y: 140, width: 960, height: 936 },
    pair,
  ), null);
});

test("a pointer well past the shared edge is not pulled back", () => {
  assert.equal(tileForSeam({ x: 2300, y: 400 }, { x: 700, y: 400 }, pair), null);
});

test("a window that has crossed onto the next monitor is not snapped back", () => {
  const tile = tileForDrag({
    cursor: { x: 2100, y: 40 },
    start: { x: 2500, y: 40 },
    bounds: { x: 1700, y: 32, width: 1056, height: 960 },
    area: liveRight,
  });
  assert.equal(tile, null);
});
