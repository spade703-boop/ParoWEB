const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8");

function functionSource(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} should exist`);
  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`${name} is incomplete`);
}

function createScrollHarness() {
  const mediaQueries = new Map();
  let scrollOptions = null;
  const window = {
    matchMedia(query) {
      const result = { matches: false };
      mediaQueries.set(query, result);
      return result;
    },
  };
  const el = {
    resultArea: {
      scrollIntoView(options) { scrollOptions = options; },
    },
  };
  const mediaDefinitions = source.match(/^const mq(?:MobilePool|StackedDraw|ReducedMotion).*$/gm);
  assert.equal(mediaDefinitions.length, 3);
  const context = vm.createContext({ window, el });
  vm.runInContext(`${mediaDefinitions.join("\n")}
    ${functionSource("motionBehavior")}
    ${functionSource("revealResultsWhenStacked")}
    globalThis.revealResultsWhenStacked = revealResultsWhenStacked;`, context);
  return {
    mediaQueries,
    revealResultsWhenStacked: context.revealResultsWhenStacked,
    scrollOptions: () => scrollOptions,
  };
}

test("results scroll throughout the stacked draw layout", () => {
  const harness = createScrollHarness();
  harness.mediaQueries.get("(max-width: 899px)").matches = false;
  harness.mediaQueries.get("(max-width: 1199px)").matches = true;
  harness.revealResultsWhenStacked();
  assert.equal(harness.scrollOptions().behavior, "smooth");
  assert.equal(harness.scrollOptions().block, "start");
});

test("results do not scroll in the two-column layout", () => {
  const harness = createScrollHarness();
  harness.mediaQueries.get("(max-width: 1199px)").matches = false;
  harness.revealResultsWhenStacked();
  assert.equal(harness.scrollOptions(), null);
});
