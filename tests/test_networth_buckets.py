"""Offline protocol/DOM checks for the Networth-only account classification board."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "src/components/networth_buckets_v1/index.html"
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)


HARNESS = r'''
const vm = require("vm");
const assert = require("assert").strict;
let frameHeight = 180;
const messages = [];
const windowListeners = new Map();
const documentListeners = new Map();
const resizeObservers = [];
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null;
    this.attributes = {}; this.dataset = {}; this.listeners = new Map(); this.className = "";
    this.id = ""; this._text = ""; this.hidden = false; this.disabled = false; this.draggable = false;
    const element = this;
    this.classList = {
      contains(name) { return element.className.split(/\s+/).includes(name); },
      add(...names) { element.className = [...new Set([...element.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove(...names) { element.className = element.className.split(/\s+/).filter(name => name && !names.includes(name)).join(" "); },
      toggle(name, force) {
        const enabled = force === undefined ? !this.contains(name) : force;
        if (enabled) this.add(name); else this.remove(name);
        return enabled;
      }
    };
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this.children.forEach(child => child.parentElement = null); this.children = []; this._text = String(value); }
  set innerHTML(_) { throw Error("HTML injection is forbidden in this component"); }
  get scrollHeight() { return this.id === "networth-buckets" ? frameHeight : 0; }
  getBoundingClientRect() { return {height:this.id === "networth-buckets" ? frameHeight : 0}; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
  insertBefore(child, before) {
    if (child === before) return child;
    child.remove();
    const index = before ? this.children.indexOf(before) : this.children.length;
    if (index < 0) throw Error("Invalid insertion reference");
    this.children.splice(index, 0, child); child.parentElement = this; return child;
  }
  appendChild(child) { return this.insertBefore(child, null); }
  remove() {
    if (this.parentElement) {
      const siblings = this.parentElement.children;
      const index = siblings.indexOf(this); if (index >= 0) siblings.splice(index, 1);
      this.parentElement = null;
    }
  }
  contains(child) { return child === this || this.children.some(value => value.contains(child)); }
  focus() { document.activeElement = this; }
  emit(type, fields={}) {
    const event = {type, target:this, currentTarget:this, defaultPrevented:false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, ...fields};
    if (this.disabled && ["click", "keydown", "dragstart"].includes(type)) return event;
    (this.listeners.get(type) || []).forEach(callback => callback(event));
    return event;
  }
}
const root = new Element("main"); root.id = "networth-buckets";
for (const id of ["instructions", "save-status", "bucket-board"]) {
  const element = new Element(id === "bucket-board" ? "div" : "p"); element.id = id; root.appendChild(element);
}
function descendants(element=root) { return [element, ...element.children.flatMap(child => descendants(child))]; }
const document = {
  activeElement:null,
  getElementById(id) { return descendants().find(element => element.id === id) || null; },
  createElement(tag) { return new Element(tag); },
  addEventListener(type, callback) { if (!documentListeners.has(type)) documentListeners.set(type, []); documentListeners.get(type).push(callback); }
};
let sequence = 0;
class ResizeObserver {
  constructor(callback) { resizeObservers.push(callback); }
  observe() {}
}
const parent = {postMessage(value) { messages.push(JSON.parse(JSON.stringify(value))); }};
const window = {
  parent, ResizeObserver,
  crypto:{randomUUID() { sequence += 1; return "00000000-0000-4000-8000-" + String(sequence).padStart(12, "0"); }},
  requestAnimationFrame(callback) { callback(); },
  addEventListener(type, callback) { if (!windowListeners.has(type)) windowListeners.set(type, []); windowListeners.get(type).push(callback); }
};
const context = vm.createContext({window, document, ResizeObserver, Uint8Array, console});
vm.runInContext(SOURCE, context);
const env = {
  root, window, document, messages,
  render(args, source=parent) { (windowListeners.get("message") || []).forEach(callback => callback({source, data:{type:"streamlit:render", args}})); },
  card(id) { return descendants().find(element => element.dataset.accountId === id); },
  bucket(kind) { return descendants().find(element => element.dataset.className === kind); },
  moveButton(kind) { return this.bucket(kind).children.find(element => element.classList.contains("move-here")); },
  status() { return document.getElementById("save-status"); },
  values() { return messages.filter(value => value.type === "streamlit:setComponentValue").map(value => value.value); },
  frames() { return messages.filter(value => value.type === "streamlit:setFrameHeight"); },
  drag(id) { return this.card(id).emit("dragstart", {dataTransfer:{setData() {}}}); },
  drop(kind) { return this.bucket(kind).emit("drop"); },
  escape() { (documentListeners.get("keydown") || []).forEach(callback => callback({key:"Escape"})); },
  resize(height) { frameHeight = height; resizeObservers.forEach(callback => callback()); }
};
const base = [{account_id:"synthetic-a", label:"Household Cash", balance:1200, currency:"USD", classification:"Asset", stale:false},
              {account_id:"synthetic-b", label:"Synthetic Card", balance:500, currency:"USD", classification:"Liability", stale:true}];
function props(accounts=base, extra={}) { return {accounts, view_id:"synthetic-view", revision:"revision-1", ack:null, ...extra}; }
function placement(id) { return env.card(id).parentElement.parentElement.dataset.className; }
'''


@unittest.skipUnless(NODE, "Node is required for component protocol tests")
class NetworthBucketsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = COMPONENT.read_text()
        cls.source = re.search(r"<script>(.*?)</script>", cls.html, re.S).group(1)

    def run_node(self, scenario):
        program = "const SOURCE = " + json.dumps(self.source) + ";\n" + HARNESS + "\n" + scenario
        result = subprocess.run([NODE], input=program, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bootstrap_and_four_always_visible_targets_do_not_emit_moves(self):
        self.run_node(r'''
env.render(props());
assert(messages.some(message => message.type === "streamlit:componentReady" && message.apiVersion === 1));
assert(env.frames().every(message => message.height === 180));
for (const target of ["Asset", "Liability", "Equity", "Excluded"]) {
  assert(env.bucket(target)); assert(!env.bucket(target).hidden); assert(env.moveButton(target).disabled);
}
assert(env.bucket("Unclassified").hidden);
assert.equal(env.values().length, 0);
''')

    def test_intentional_drop_emits_exact_event_and_blocks_overlapping_moves(self):
        self.run_node(r'''
env.render(props()); env.drag("synthetic-a"); env.drop("Liability");
const event = env.values()[0];
assert.deepEqual(Object.keys(event).sort(), ["action","account_id","target_class","base_class","event_id","view_id","revision"].sort());
assert.equal(event.action, "classify"); assert.equal(event.account_id, "synthetic-a");
assert.equal(event.base_class, "Asset"); assert.equal(event.target_class, "Liability");
assert.equal(event.view_id, "synthetic-view"); assert.equal(event.revision, "revision-1");
assert.match(event.event_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
assert.equal(placement("synthetic-a"), "Asset");
assert(env.card("synthetic-a").disabled); assert(env.card("synthetic-b").disabled);
env.drag("synthetic-b"); env.drop("Equity"); env.moveButton("Equity").emit("click");
assert.equal(env.values().length, 1); assert.equal(env.root.getAttribute("aria-busy"), "true");
''')

    def test_hover_drag_cancel_same_bucket_and_external_drop_never_save(self):
        self.run_node(r'''
env.render(props());
env.bucket("Equity").emit("dragover", {dataTransfer:{}}); env.drop("Equity");
assert.equal(env.values().length, 0);
env.drag("synthetic-a"); env.bucket("Equity").emit("dragover", {dataTransfer:{}});
assert.equal(env.values().length, 0); env.card("synthetic-a").emit("dragend"); env.drop("Equity");
assert.equal(env.values().length, 0);
env.drag("synthetic-a"); env.drop("Asset"); assert.equal(env.values().length, 0);
env.drag("synthetic-a"); env.drop("Excluded"); assert.equal(env.values()[0].target_class, "Excluded");
''')

    def test_keyboard_and_touch_selection_offer_the_same_explicit_move(self):
        self.run_node(r'''
env.render(props()); env.card("synthetic-a").emit("keydown", {key:"Enter"});
assert.equal(env.card("synthetic-a").getAttribute("aria-pressed"), "true");
assert(env.moveButton("Asset").disabled); assert(!env.moveButton("Equity").disabled);
env.escape(); assert(env.moveButton("Equity").disabled); assert.equal(env.values().length, 0);
env.card("synthetic-a").emit("click"); env.moveButton("Equity").focus(); env.moveButton("Equity").emit("click");
assert.equal(env.values()[0].target_class, "Equity");
''')

    def test_repeated_render_preserves_nodes_focus_and_pending_until_matching_failure(self):
        self.run_node(r'''
env.render(props()); const card = env.card("synthetic-a"); card.focus();
env.render(props()); assert.equal(env.card("synthetic-a"), card); assert.equal(document.activeElement, card);
card.emit("click"); env.moveButton("Liability").emit("click"); const first = env.values()[0];
env.render(props(base, {ack:{event_id:"other-event",ok:true,message:"Not ours"}}));
assert(card.disabled); assert.equal(env.card("synthetic-a"), card);
env.render(props(base, {ack:{event_id:first.event_id,ok:false,message:"<b>Not saved</b>"}}));
assert(!card.disabled); assert.equal(placement("synthetic-a"), "Asset");
assert.equal(env.status().textContent, "<b>Not saved</b>"); assert.equal(env.status().getAttribute("role"), "alert");
assert(!descendants().some(element => element.tagName === "B"));
env.moveButton("Liability").emit("click"); assert.equal(env.values().length, 2);
assert.notEqual(env.values()[1].event_id, first.event_id);
''')

    def test_saved_props_unlock_move_without_ack_and_preserve_authoritative_placement(self):
        self.run_node(r'''
env.render(props()); env.drag("synthetic-a"); env.drop("Equity");
const saved = base.map(row => row.account_id === "synthetic-a" ? {...row,classification:"Equity"} : row);
env.render(props(saved, {revision:"revision-2"}));
assert.equal(placement("synthetic-a"), "Equity"); assert(!env.card("synthetic-a").disabled);
assert.equal(env.status().textContent, "Saved.");
env.card("synthetic-a").emit("click"); env.moveButton("Excluded").emit("click");
assert.equal(env.values()[1].base_class, "Equity"); assert.equal(env.values()[1].revision, "revision-2");
''')

    def test_success_ack_new_view_and_removed_accounts_do_not_replay_events(self):
        self.run_node(r'''
env.render(props()); env.drag("synthetic-a"); env.drop("Equity"); const first = env.values()[0];
const saved = base.map(row => row.account_id === "synthetic-a" ? {...row,classification:"Equity"} : row);
env.render(props(saved, {revision:"revision-2",ack:{event_id:first.event_id,ok:true,message:"Saved"}}));
assert.equal(placement("synthetic-a"), "Equity"); assert(!env.card("synthetic-a").disabled);
env.drag("synthetic-a"); env.drop("Excluded");
env.render(props(base, {view_id:"different-ledger",revision:"different-revision"}));
assert(!env.card("synthetic-a").disabled); assert.equal(placement("synthetic-a"), "Asset");
assert.equal(env.values().length, 2);
env.drag("synthetic-a"); env.drop("Equity");
env.render(props([base[1]], {view_id:"different-ledger",revision:"revision-3"}));
assert(!env.card("synthetic-a")); assert.equal(env.status().getAttribute("role"), "alert");
assert.equal(env.values().length, 3);
''')

    def test_labels_are_plain_text_unknown_values_and_unclassified_remain_visible(self):
        self.run_node(r'''
const malicious = '<img src="x" onerror="alert(1)"> & Synthetic';
env.render(props([{account_id:"unsafe",label:malicious,balance:NaN,currency:"<USD>",classification:"Invalid",stale:true},
                  {account_id:"unsafe",label:"Duplicate must not replace",balance:100,currency:"USD",classification:"Asset"}]));
assert.equal(env.card("unsafe").children[0].textContent, malicious);
assert(!descendants().some(element => element.tagName === "IMG"));
assert(env.card("unsafe").textContent.includes("Unavailable · <USD>"));
assert(env.card("unsafe").textContent.includes("Previous balance"));
assert.equal(placement("unsafe"), "Unclassified"); assert(!env.bucket("Unclassified").hidden);
assert.equal(env.values().length, 0);
''')

    def test_parent_source_and_valid_context_are_required_for_moves(self):
        self.run_node(r'''
env.render(props(), {}); assert(!env.card("synthetic-a"));
env.render(props(base, {view_id:"",revision:""})); env.drag("synthetic-a"); env.drop("Liability");
assert.equal(env.values().length, 0);
env.render(props()); window.crypto = null; env.card("synthetic-a").emit("click"); env.moveButton("Liability").emit("click");
assert.equal(env.values().length, 0); assert.equal(env.status().getAttribute("role"), "alert");
''')

    def test_frame_height_tracks_actual_content_without_duplicate_same_size_messages(self):
        self.run_node(r'''
env.render(props()); const before = env.frames().length;
env.render(props()); assert.equal(env.frames().length, before);
env.resize(321.4); assert.equal(env.frames().at(-1).height, 322);
env.resize(321.4); assert.equal(env.frames().length, before + 1);
assert(env.frames().every(message => message.isStreamlitMessage === true));
''')

    def test_assets_are_self_contained_and_mobile_and_reduced_motion_are_supported(self):
        self.assertNotRegex(self.html, r"https?://|localStorage|sessionStorage|innerHTML|fetch\(|XMLHttpRequest|WebSocket")
        self.assertNotRegex(self.source, r"(?:window\.)?parent\.document")
        self.assertIn("grid-template-columns:repeat(4,minmax(0,1fr))", self.html)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", self.html)
        self.assertIn("prefers-reduced-motion:reduce", self.html)


if __name__ == "__main__":
    unittest.main()
