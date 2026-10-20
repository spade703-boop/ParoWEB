const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "../app/static/index.html"), "utf8");

function functionSource(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} should exist`);
  const bodyStart = source.indexOf(") {", start) + 2;
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`${name} is incomplete`);
}

test("profile cooldown restores the draw button timer", () => {
  const started = [];
  const context = vm.createContext({
    startCooldown(seconds) { started.push(seconds); },
  });
  vm.runInContext(
    `${functionSource("syncCooldownFromProfile")}
     globalThis.syncCooldownFromProfile = syncCooldownFromProfile;`,
    context,
  );

  context.syncCooldownFromProfile({ cooldown_remaining_seconds: 4 });
  context.syncCooldownFromProfile({ cooldown_remaining_seconds: 0 });
  context.syncCooldownFromProfile({});

  assert.deepEqual(started, [4]);
  assert.match(functionSource("loadProfile"), /syncCooldownFromProfile\(profile\)/);
  assert.match(page, /id="draw-button" type="submit" disabled/);
  assert.match(
    functionSource("initialize"),
    /if \(!state\.cooldownActive\) el\.drawButton\.disabled = false/,
  );
});
