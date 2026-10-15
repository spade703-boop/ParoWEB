const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8");
const bootstrapOffset = source.lastIndexOf("\ninitialize().catch(");
assert.ok(bootstrapOffset > 0);
const storageKey = "paro:read-announcement-id";
const latest = {
  id: "latest", published_at: "2026-10-15", title: "Latest notice",
  summary: "Summary", details: ["Details"], tags: [],
};
const older = { ...latest, id: "older", title: "Older notice" };

function element() {
  const classes = new Set();
  return {
    events: {},
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
      toggle(name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
      },
    },
    addEventListener(name, handler) { this.events[name] = handler; },
    append(...children) { this.children = [...(this.children || []), ...children]; },
    replaceChildren(...children) { this.children = children; },
    showModal() { this.open = true; },
  };
}

function createApp(storage = new Map(), storageBlocked = false) {
  const nodes = new Map();
  const badge = element();
  const browserEvents = {};
  const documentEvents = {};
  const document = {
    visibilityState: "visible",
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, element());
      return nodes.get(selector);
    },
    querySelectorAll(selector) { return selector === ".nav-new" ? [badge] : []; },
    createElement: element,
    addEventListener(name, handler) { documentEvents[name] = handler; },
  };
  const window = {
    location: { hash: "#updates" },
    matchMedia: element,
    scrollTo() {},
    addEventListener(name, handler) { browserEvents[name] = handler; },
    localStorage: {
      getItem(key) {
        if (storageBlocked) throw new Error("Storage disabled");
        return storage.get(key) ?? null;
      },
      setItem(key, value) {
        if (storageBlocked) throw new Error("Storage disabled");
        storage.set(key, value);
      },
    },
  };
  const context = vm.createContext({ document, window });
  vm.runInContext(`${source.slice(0, bootstrapOffset)}
    globalThis.app = { state, el, bindEvents, route, renderAnnouncements, openAnnouncement, loadAnnouncements };`, context);
  const app = context.app;
  app.state.announcements = [latest, older];
  app.renderAnnouncements();
  return { ...app, storage, context, browserEvents, documentEvents };
}

function assertNotice(app, visible) {
  assert.equal(app.el.homeUpdate.classList.contains("hidden"), !visible);
  assert.equal(app.el.announcementBadges[0].classList.contains("hidden"), !visible);
}

test("opening the latest announcement hides both reminders across reloads", () => {
  const app = createApp();
  assertNotice(app, true);
  app.openAnnouncement(latest);
  assert.equal(app.el.updateDialog.open, true);
  assertNotice(app, false);
  assert.equal(app.storage.get(storageKey), latest.id);
  assertNotice(createApp(app.storage), false);
});

test("the homepage link opens details and marks the announcement read", () => {
  const app = createApp();
  app.bindEvents();
  let prevented = false;
  app.el.homeUpdateLink.events.click({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(app.el.updateDialogTitle.textContent, latest.title);
  assert.equal(app.el.updateDialog.open, true);
  assertNotice(app, false);
});

test("visiting the list or opening an older announcement keeps the latest unread", () => {
  const app = createApp();
  app.route();
  assertNotice(app, true);
  app.openAnnouncement(older);
  assertNotice(app, true);
  app.openAnnouncement(latest);
  app.openAnnouncement(older);
  assertNotice(app, false);
});

test("a new announcement ID restores reminders; edits to a read ID do not", async () => {
  const app = createApp();
  app.openAnnouncement(latest);
  app.context.fetch = async () => ({
    ok: true, json: async () => ({ items: [{ ...latest, title: "Edited title" }, older] }),
  });
  await app.loadAnnouncements();
  assertNotice(app, false);
  app.context.fetch = async () => ({
    ok: true, json: async () => ({ items: [{ ...latest, id: "newer" }, latest, older] }),
  });
  await app.loadAnnouncements();
  assertNotice(app, true);
});

test("empty announcements hide reminders and a failed refresh preserves valid content", async () => {
  const app = createApp();
  app.context.fetch = async () => { throw new Error("Offline"); };
  await assert.rejects(app.loadAnnouncements(), /Offline/);
  assertNotice(app, true);
  assert.equal(app.state.announcementsLoading, false);
  app.context.fetch = async () => ({ ok: true, json: async () => ({ items: [] }) });
  await app.loadAnnouncements();
  assertNotice(app, false);
});

test("blocked storage still allows reading and hides reminders for this page", () => {
  const app = createApp(new Map(), true);
  assertNotice(app, true);
  app.openAnnouncement(latest);
  assertNotice(app, false);
});

test("read state is synchronized from other tabs and clearing storage", () => {
  const app = createApp();
  app.bindEvents();
  app.storage.set(storageKey, latest.id);
  app.browserEvents.storage({ key: storageKey });
  assertNotice(app, false);
  app.storage.clear();
  app.browserEvents.storage({ key: null });
  assertNotice(app, true);
});

test("returning to a visible tab fetches announcements again", async () => {
  const app = createApp();
  app.bindEvents();
  app.openAnnouncement(latest);
  let requests = 0;
  app.context.fetch = async () => {
    requests += 1;
    return { ok: true, json: async () => ({ items: [{ ...latest, id: "newer" }] }) };
  };
  app.documentEvents.visibilitychange();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests, 1);
  assertNotice(app, true);
});
