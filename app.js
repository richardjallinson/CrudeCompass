/* Crude Compass v1A.
   A daily WTI crude decision-support app: morning briefing, event calendar,
   charts, and a model lean with its reasoning and track record shown.

   v1A runs entirely on SAMPLE DATA. Every number on every screen is a
   realistic placeholder so the design can be judged before a dollar is
   spent on data feeds. The live pipeline (EIA, FRED, CFTC, price feed,
   news) replaces sampleData in a later version; the interface reads one
   data object so the swap is contained.

   Crude Compass is decision support, not financial advice. It never tells
   anyone to buy or sell. */

const { useState, useEffect } = React;
const h = React.createElement;

const APP_VERSION = "v1B";

// ---- KNOWN LIMITATION OF v1B — read this before trusting the Scoreboard.
//
//   Free EIA and FRED data publishes end-of-day with a lag, so in the
//   local-script version a call is resolved the NEXT MORNING rather than
//   live at the 2:30 PM ET settlement. The intraday tracking strip — the
//   "locked 8:00 AM · now $83.40 · on side · 2h to settlement" line — stays
//   empty until a live price feed arrives in v1C.
//
//   The call itself is still locked at 8:00 AM and never revised. Only the
//   moment of resolution moves: next morning instead of same afternoon.
//
// This same text is carried in data.json (payload.limitation), shown on the
// Settings screen, and repeated in README-V1B.txt. Three places on purpose.

// ---- Palettes. Dark is the primary theme: this app is used at a trading
// screen, often before sunrise. The ground is a warm crude-brown black
// rather than a blue-black, the accent is instrument brass, and market
// green / red are reserved strictly for direction so they never dilute
// into decoration. Light mode is the daylight reading of the same idea.
const PALETTES = {
  dark: {
    bg: "#0E1526",
    card: "#16203A",
    // Directional tints stay inside the navy family: a card should read as
    // "this one leaned up" at a glance without turning into a green box.
    cardUp: "#152A2E",
    cardDown: "#2A1C24",
    line: "#253253",
    ink: "#E8ECF6",
    inkSoft: "#9AA6C2",
    heading: "#F2F5FC",
    header: "#080D1A",
    brass: "#D4AC55",
    brassSoft: "#1C2440",
    up: "#5FBF8F",
    down: "#E4796A",
    neutral: "#8FA0C4",
    neutralSoft: "#182137",
    amber: "#D9B45F",
    amberSoft: "#241E10",
    tabIdle: "#7A88A8",
    field: "#111930",
    btn2: "#1B2540",
    onAccent: "#0B1224",
    disabled: "#2E3A5C",
    onDisabled: "#A2ACC6"
  },
  light: {
    bg: "#F2F4F9",
    card: "#FFFFFF",
    cardUp: "#EDF6F0",
    cardDown: "#FBEFEC",
    line: "#D9DFEC",
    ink: "#1A2340",
    inkSoft: "#5B678A",
    heading: "#141C33",
    // The header bar is navy in BOTH themes. It is the brand, and a brand
    // that changes colour with the phone's appearance setting is not one.
    header: "#141A33",
    // Gold on white needs to be darker than gold on navy or it disappears.
    brass: "#8F701E",
    brassSoft: "#F4ECD6",
    up: "#2E7D53",
    down: "#B04A38",
    neutral: "#556180",
    neutralSoft: "#EDF0F7",
    amber: "#8A6714",
    amberSoft: "#FAF0D8",
    // Tab labels sit on the navy header in both themes, so the idle colour
    // must stay light here rather than following the page.
    tabIdle: "#8794B4",
    field: "#FFFFFF",
    btn2: "#EDF0F7",
    onAccent: "#FFFFFF",
    disabled: "#C6CDDD",
    onDisabled: "#4A5470"
  }
};
let T = PALETTES.dark;

// The header bar is navy in both themes, so anything drawn on it reads from
// this fixed pair rather than from T. Otherwise light mode paints the dark
// on-white gold onto a navy bar, where it vanishes.
const HDR = { gold: "#D4AC55", idle: "#8B99BC" };

const THEMES = [
  { id: "auto", label: "Follow the phone" },
  { id: "dark", label: "Night" },
  { id: "light", label: "Day" }
];
const THEME_KEY = "crude-compass-theme";
function systemPrefersDark() {
  try { return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches); } catch (e) { return true; }
}
function resolveTheme(id) {
  if (id === "light") return "light";
  if (id === "dark") return "dark";
  // auto: default to dark when the phone offers no opinion; this is a
  // trading-screen app and night is its native habitat.
  return systemPrefersDark() ? "dark" : "light";
}
function setThemeTokens(id) {
  T = PALETTES[resolveTheme(id)];
  try {
    document.body.style.background = T.bg;
    document.documentElement.style.colorScheme = resolveTheme(id);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", T.header);
  } catch (e) {}
}
function loadTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light" || saved === "auto") return saved;
  } catch (e) {}
  return "auto";
}
function saveTheme(id) { try { localStorage.setItem(THEME_KEY, id); } catch (e) {} }

const font = {
  display: "'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif",
  body: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  // Every price, probability and timestamp is set in the mono stack with
  // tabular figures: numbers a trader scans daily should sit in columns.
  mono: "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"
};

// ---------------------------------------------------------------------------
// DATA. The app reads web/data.json, written by the v1B pipeline. If that
// file is missing or malformed the app falls back to the sample below and
// says so in the banner — a failed pipeline run degrades to "sample data"
// rather than a blank screen or, worse, stale numbers presented as live.
//
// `D` is the live binding every screen reads.
// ---------------------------------------------------------------------------
const sampleData = {
  isLive: false,
  limitation: "",
  asOf: "Fri Aug 28 · locked 8:00 AM ET",
  spot: { last: 82.85, chg: -0.07, chgPct: -0.08 },

  // The Today card has three states by design: "up", "down", "none".
  // A model forced to pick a side every day is a coin flip in a costume;
  // the stand-down state is the feature that makes the other two credible.
  prediction: {
    state: "up",             // "up" | "down" | "none"
    probability: 0.58,       // calibrated probability of an up close
    rangeLow: 81.4,
    rangeHigh: 84.2,
    drivers: [
      { label: "Backwardation deepening", dir: "up", note: "M1–M3 spread widened to +$1.12, third session running. Physical tightness." },
      { label: "Overnight sessions firm", dir: "up", note: "Asia and Europe both bid; price held above yesterday's US close all night." },
      { label: "Managed money crowded long", dir: "down", note: "CFTC net length in the 88th percentile of 3 years. Unwind risk caps upside." },
      { label: "Dollar index flat", dir: "none", note: "DXY inside a 0.2% band overnight. No pull either way today." }
    ],
    caution: "Baker Hughes rig count 1:00 PM ET and CFTC positioning 3:30 PM ET land today. The model's read applies to the session as a whole, not to the minutes around a release."
  },

  briefing: {
    headline: "Firm overnight, crowded positioning",
    paragraphs: [
      "WTI held its ground overnight after Thursday's late fade, trading a narrow band either side of $82.90 through Asia and holding it through the European morning. That is constructive behaviour after a down day: sellers had the chance to press and didn't take it.",
      "The physical market keeps tightening quietly. The front of the futures curve steepened again — the March contract now pays over a dollar more than May — which is the market paying up for barrels now rather than later. Wednesday's EIA report showed a fourth consecutive draw at Cushing, and that is the delivery point this contract settles into.",
      "The caution flag is positioning. Funds are as long as they have been in a year, and crowded longs do not need bearish news to sell — they only need a reason to take profit. Today's CFTC print at 3:30 PM ET updates that picture after the close of the model's window."
    ],
    watching: [
      "Baker Hughes rig count, 1:00 PM ET",
      "CFTC Commitments of Traders, 3:30 PM ET",
      "Weekend headline risk: OPEC+ ministers meet Tuesday"
    ]
  },

  events: [
    { day: "Fri", date: "Aug 28", time: "1:00 PM ET", name: "Baker Hughes rig count", impact: "low", note: "Weekly US drilling activity. Slow-moving supply signal." },
    { day: "Fri", date: "Aug 28", time: "3:30 PM ET", name: "CFTC Commitments of Traders", impact: "medium", note: "Managed-money positioning as of Tuesday. The crowdedness gauge." },
    { day: "Tue", date: "Sep 1", time: "All day", name: "OPEC+ ministerial meeting", impact: "high", note: "Production policy. Headline risk from the first leak onward." },
    { day: "Tue", date: "Sep 1", time: "4:30 PM ET", name: "API weekly inventories", impact: "medium", note: "Industry preview of Wednesday's EIA report. After-hours mover." },
    { day: "Wed", date: "Sep 2", time: "10:30 AM ET", name: "EIA Weekly Petroleum Status", impact: "high", note: "The week's main event. The surprise vs. consensus moves price, not the level." },
    { day: "Fri", date: "Sep 4", time: "8:30 AM ET", name: "US jobs report", impact: "medium", note: "Macro risk tone. On payrolls day, oil trades as a risk asset." }
  ],

  // Two sample series, both ending at the same last price so the toggle
  // reads as one instrument at two zoom levels rather than two charts.
  chart1D: [83.34, 83.30, 83.21, 83.25, 83.11, 82.98, 83.02, 82.88, 82.71, 82.64, 82.75, 82.69, 82.55, 82.40, 82.47, 82.31, 82.20, 82.26, 82.41, 82.35, 82.52, 82.66, 82.58, 82.73, 82.88, 82.79, 82.94, 83.07, 82.99, 83.12, 83.05, 82.91, 82.84, 82.96, 83.10, 83.18, 83.06, 82.97, 82.85, 82.78, 82.90, 83.01, 82.93, 82.82, 82.74, 82.86, 82.95, 82.89, 82.80, 82.85],
  chart5D: [86.90, 86.75, 86.30, 85.95, 86.10, 85.60, 85.20, 85.45, 85.02, 84.60, 83.40, 82.10, 81.60, 81.85, 81.30, 80.75, 80.40, 80.62, 80.95, 81.40, 82.30, 82.85, 82.40, 81.95, 81.55, 81.05, 81.70, 82.20, 82.60, 82.95, 83.30, 83.60, 83.95, 84.15, 83.85, 83.50, 83.20, 83.42, 83.10, 82.80, 82.55, 82.95, 83.15, 82.90, 82.70, 82.98, 83.05, 82.88, 82.75, 82.85],

  scoreboard: {
    windowLabel: "Last 30 trading days",
    fired: 17,
    standDowns: 13,
    hits: 10,
    accuracy: 0.59,
    baseline: 0.53,
    baselineLabel: "always guess up",
    calibrationNote: "Of days the model put 55–60% on a direction, 58% resolved that way. Small sample; treat with care.",
    log: [
      { date: "Aug 28", call: "up", prob: 0.58, outcome: "open" },
      { date: "Aug 27", call: "up", prob: 0.57, outcome: "hit" },
      { date: "Aug 26", call: "none", prob: 0.51, outcome: "stand" },
      { date: "Aug 25", call: "down", prob: 0.59, outcome: "miss" },
      { date: "Aug 24", call: "down", prob: 0.61, outcome: "hit" },
      { date: "Aug 21", call: "none", prob: 0.53, outcome: "stand" },
      { date: "Aug 20", call: "up", prob: 0.56, outcome: "hit" },
      { date: "Aug 19", call: "none", prob: 0.49, outcome: "stand" },
      { date: "Aug 18", call: "up", prob: 0.60, outcome: "hit" },
      { date: "Aug 17", call: "down", prob: 0.55, outcome: "miss" }
    ]
  },

  sources: [
    { name: "EIA (inventories, official WTI spot)", status: "Not connected · planned v1B" },
    { name: "FRED (historical price series)", status: "Not connected · planned v1B" },
    { name: "CFTC (positioning)", status: "Not connected · planned v1B" },
    { name: "Intraday price feed", status: "Not connected · planned v1C" },
    { name: "News scoring", status: "Not connected · planned v1C" }
  ]
};

// Live binding. Starts as the sample; replaced by data.json when it loads.
let D = sampleData;

// How old the data is allowed to be before the app calls it stale. Oil
// trades Sunday evening through Friday afternoon, so a Monday morning open
// legitimately sees Friday's close: three days, not one.
const STALE_DAYS = 4;

function dataAgeDays() {
  if (!D.dataThrough) return null;
  const then = new Date(D.dataThrough + "T00:00:00");
  if (isNaN(then.getTime())) return null;
  return Math.floor((Date.now() - then.getTime()) / 86400000);
}

function loadData() {
  // Cache-busted: a stale cached data.json shown as live is the one failure
  // mode this app must never have.
  return fetch("data.json?t=" + Date.now(), { cache: "no-store" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (json) {
      if (!json || !json.prediction || !json.spot) throw new Error("malformed data.json");
      D = json;
      return true;
    })
    .catch(function () {
      D = sampleData;
      return false;
    });
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function fmt(n, dp) { return n.toFixed(dp === undefined ? 2 : dp); }

function dirColor(dir) { return dir === "up" ? T.up : dir === "down" ? T.down : T.neutral; }
function dirArrow(dir) { return dir === "up" ? "\u25B2" : dir === "down" ? "\u25BC" : "\u25AC"; }

// The compass mark: ring, needle, north tick. Used in the header and as the
// seed of the app icon. Drawn, not imported, so it always matches T.
function CompassMark(props) {
  const s = props.size || 28;
  const c = props.color || T.brass;
  return h("svg", { width: s, height: s, viewBox: "0 0 40 40", "aria-hidden": "true" },
    h("circle", { cx: 20, cy: 20, r: 17, fill: "none", stroke: c, strokeWidth: 2.4 }),
    h("line", { x1: 20, y1: 3, x2: 20, y2: 8, stroke: c, strokeWidth: 2.4, strokeLinecap: "round" }),
    h("polygon", { points: "20,9 25,22 20,19 15,22", fill: c }),
    h("polygon", { points: "20,31 24,22 20,24.5 16,22", fill: c, opacity: 0.45 })
  );
}

// The signature element. A half-dial: needle at 12 o'clock is a full UP
// conviction, 6 o'clock full DOWN, 3 o'clock dead neutral. The probability
// sits in the middle in mono. Everything else on the screen stays quiet so
// this is the one thing the eye lands on.
function Dial(props) {
  const state = props.state;
  const p = props.probability;
  const color = state === "up" ? T.up : state === "down" ? T.down : T.neutral;
  // Map conviction to angle. 0.5 -> 0 (east). 0.70+ -> +/-90 (north/south).
  const conviction = Math.max(0, Math.min(1, (p - 0.5) / 0.2));
  const deg = state === "up" ? -90 * conviction : state === "down" ? 90 * conviction : 0;
  const rad = deg * Math.PI / 180;
  const cx = 110, cy = 110, r = 86;
  const nx = cx + r * 0.82 * Math.cos(rad);
  const ny = cy + r * 0.82 * Math.sin(rad);
  // Tick marks every 15 degrees along the east-facing half circle.
  const ticks = [];
  for (let a = -90; a <= 90; a += 15) {
    const t = a * Math.PI / 180;
    const big = a === -90 || a === 0 || a === 90;
    ticks.push(h("line", {
      key: "t" + a,
      x1: cx + (r - (big ? 12 : 6)) * Math.cos(t), y1: cy + (r - (big ? 12 : 6)) * Math.sin(t),
      x2: cx + r * Math.cos(t), y2: cy + r * Math.sin(t),
      stroke: big ? T.brass : T.line, strokeWidth: big ? 2.5 : 1.5, strokeLinecap: "round"
    }));
  }
  return h("svg", { width: "100%", viewBox: "0 0 220 220", role: "img",
    "aria-label": state === "none" ? "No edge today" : ("Model lean " + (state === "up" ? "up" : "down") + " at " + Math.round(p * 100) + " percent") },
    h("path", { d: "M " + cx + " " + (cy - r) + " A " + r + " " + r + " 0 0 1 " + cx + " " + (cy + r),
      fill: "none", stroke: T.line, strokeWidth: 3 }),
    ticks,
    h("text", { x: cx + r + 2, y: cy - r + 6, fill: T.up, fontFamily: font.body, fontSize: 13, fontWeight: 800, textAnchor: "end" }, "UP"),
    h("text", { x: cx + r + 2, y: cy + r - 1, fill: T.down, fontFamily: font.body, fontSize: 13, fontWeight: 800, textAnchor: "end" }, "DOWN"),
    // Needle
    h("line", { x1: cx - 14 * Math.cos(rad), y1: cy - 14 * Math.sin(rad), x2: nx, y2: ny,
      stroke: color, strokeWidth: 5, strokeLinecap: "round" }),
    h("circle", { cx: cx, cy: cy, r: 34, fill: T.card, stroke: T.line, strokeWidth: 1.5 }),
    h("circle", { cx: cx, cy: cy, r: 4, fill: T.brass }),
    h("text", { x: cx, y: cy - 6, fill: color, fontFamily: font.mono, fontSize: 26, fontWeight: 700, textAnchor: "middle" },
      state === "none" ? "\u2014" : Math.round(p * 100) + "%"),
    h("text", { x: cx, y: cy + 14, fill: T.inkSoft, fontFamily: font.body, fontSize: 10.5, fontWeight: 700, letterSpacing: 1, textAnchor: "middle" },
      state === "none" ? "NO EDGE" : (state === "up" ? "UP CLOSE" : "DOWN CLOSE"))
  );
}

function Card(props) {
  return h("div", {
    style: Object.assign({
      background: T.card, border: "1px solid " + T.line, borderRadius: 14,
      padding: "16px 16px", marginBottom: 12
    }, props.style || {})
  }, props.children);
}

function SectionLabel(text) {
  return h("div", {
    style: { fontFamily: font.body, fontSize: 11, fontWeight: 800, letterSpacing: 1.2,
      color: T.inkSoft, textTransform: "uppercase", marginBottom: 8 }
  }, text);
}

function Disclaimer() {
  return h("div", {
    style: { fontFamily: font.body, fontSize: 11.5, lineHeight: 1.55, color: T.inkSoft,
      borderTop: "1px solid " + T.line, paddingTop: 10, marginTop: 4 }
  }, "Crude Compass is decision support, not financial advice. It is one input among many, it can be wrong, and it does not know your position or risk. Never size a trade off this screen that you would not have taken without it.");
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

function TodayScreen() {
  const P = D.prediction;
  const S = D.spot;
  const stateColor = P.state === "up" ? T.up : P.state === "down" ? T.down : T.neutral;
  const rangeSpan = P.rangeHigh - P.rangeLow;
  const lastPos = Math.max(0, Math.min(1, (S.last - P.rangeLow) / rangeSpan));
  return h("div", null,
    // Price strip
    h(Card, { style: { display: "flex", alignItems: "baseline", gap: 10, padding: "13px 16px" } },
      h("div", { style: { fontFamily: font.body, fontSize: 12, fontWeight: 800, color: T.inkSoft } }, "WTI"),
      h("div", { style: { fontFamily: font.mono, fontSize: 26, fontWeight: 700, color: T.heading } }, fmt(S.last)),
      h("div", { style: { fontFamily: font.mono, fontSize: 13.5, fontWeight: 700, color: S.chg >= 0 ? T.up : T.down } },
        (S.chg >= 0 ? "+" : "") + fmt(S.chg) + " (" + (S.chgPct >= 0 ? "+" : "") + fmt(S.chgPct) + "%)"),
      h("div", { style: { marginLeft: "auto", fontFamily: font.body, fontSize: 10.5, color: T.inkSoft, textAlign: "right", lineHeight: 1.4 } }, D.asOf)
    ),

    // The dial card
    h(Card, { style: { background: P.state === "up" ? T.cardUp : P.state === "down" ? T.cardDown : T.card, textAlign: "center", paddingBottom: 10 } },
      h("div", { style: { maxWidth: 300, margin: "0 auto" } }, h(Dial, { state: P.state, probability: P.probability })),
      h("div", { style: { fontFamily: font.display, fontSize: 21, fontWeight: 700, color: T.heading, marginTop: -6 } },
        P.state === "none" ? "Standing down today" : (P.state === "up" ? "Leaning to an up close" : "Leaning to a down close")),
      h("div", { style: { fontFamily: font.body, fontSize: 12.5, color: T.inkSoft, marginTop: 5, lineHeight: 1.5 } },
        P.state === "none"
          ? "The signals disagree or sit too close to even. A model that forces a call every day is a coin flip; today it stays quiet."
          : "Locked before the session and never revised mid-day. A prediction that updates as price moves is just narrating.")
    ),

    // Expected range
    h(Card, null,
      SectionLabel("Expected range today"),
      h("div", { style: { display: "flex", justifyContent: "space-between", fontFamily: font.mono, fontSize: 14, fontWeight: 700, color: T.heading } },
        h("span", null, fmt(P.rangeLow)), h("span", null, fmt(P.rangeHigh))),
      h("div", { style: { position: "relative", height: 10, borderRadius: 6, background: T.btn2, border: "1px solid " + T.line, marginTop: 8 } },
        h("div", { style: { position: "absolute", left: (lastPos * 100) + "%", top: -4, transform: "translateX(-50%)",
          width: 3, height: 18, borderRadius: 2, background: T.brass } })),
      h("div", { style: { fontFamily: font.body, fontSize: 11.5, color: T.inkSoft, marginTop: 8, lineHeight: 1.5 } },
        "From realized and implied volatility. The range is the more reliable number on this screen: direction is a lean, the range is a forecast.")
    ),

    // Drivers
    h(Card, null,
      SectionLabel("Why"),
      P.drivers.map(function (d, i) {
        return h("div", { key: i, style: { display: "flex", gap: 10, alignItems: "flex-start", padding: "8px 0",
          borderTop: i === 0 ? "none" : "1px solid " + T.line } },
          h("div", { style: { color: dirColor(d.dir), fontSize: 11, marginTop: 3, width: 14, flex: "0 0 auto" } }, dirArrow(d.dir)),
          h("div", { style: { minWidth: 0 } },
            h("div", { style: { fontFamily: font.body, fontSize: 13.5, fontWeight: 700, color: T.ink } }, d.label),
            h("div", { style: { fontFamily: font.body, fontSize: 12, color: T.inkSoft, lineHeight: 1.5, marginTop: 2 } }, d.note))
        );
      })
    ),

    // Caution
    h(Card, { style: { background: T.amberSoft, borderColor: T.amber } },
      h("div", { style: { fontFamily: font.body, fontSize: 12.5, lineHeight: 1.55, color: T.ink } },
        h("span", { style: { fontWeight: 800, color: T.amber } }, "Event risk today. "), P.caution)
    ),
    h(Card, { style: { border: "none", background: "transparent", padding: "0 4px" } }, h(Disclaimer))
  );
}

function BriefingScreen() {
  const B = D.briefing;
  return h("div", null,
    h(Card, null,
      SectionLabel("Morning briefing"),
      h("div", { style: { fontFamily: font.display, fontSize: 21, fontWeight: 700, color: T.heading, lineHeight: 1.25, marginBottom: 10 } }, B.headline),
      B.paragraphs.map(function (p, i) {
        return h("p", { key: i, style: { fontFamily: font.body, fontSize: 14, lineHeight: 1.65, color: T.ink, margin: "0 0 12px" } }, p);
      })
    ),
    h(Card, null,
      SectionLabel("Watching today"),
      B.watching.map(function (w, i) {
        return h("div", { key: i, style: { display: "flex", gap: 10, padding: "7px 0", borderTop: i === 0 ? "none" : "1px solid " + T.line } },
          h("span", { style: { color: T.brass, flex: "0 0 auto" } }, "\u25C6"),
          h("span", { style: { fontFamily: font.body, fontSize: 13.5, color: T.ink } }, w));
      })
    )
  );
}

function CalendarScreen() {
  const impactColor = { high: T.down, medium: T.amber, low: T.inkSoft };
  return h("div", null,
    h(Card, { style: { padding: "6px 16px" } },
      D.events.map(function (e, i) {
        return h("div", { key: i, style: { display: "flex", gap: 12, padding: "12px 0", borderTop: i === 0 ? "none" : "1px solid " + T.line } },
          h("div", { style: { width: 48, flex: "0 0 auto", textAlign: "center" } },
            h("div", { style: { fontFamily: font.body, fontSize: 10.5, fontWeight: 800, color: T.brass, letterSpacing: 1 } }, e.day.toUpperCase()),
            h("div", { style: { fontFamily: font.mono, fontSize: 13, fontWeight: 700, color: T.heading } }, e.date)),
          h("div", { style: { minWidth: 0, flex: "1 1 auto" } },
            h("div", { style: { display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" } },
              h("span", { style: { fontFamily: font.body, fontSize: 13.5, fontWeight: 700, color: T.ink } }, e.name),
              h("span", { style: { fontFamily: font.body, fontSize: 10, fontWeight: 800, letterSpacing: 0.8, color: impactColor[e.impact], border: "1px solid " + impactColor[e.impact], borderRadius: 999, padding: "1px 8px" } }, e.impact.toUpperCase())),
            h("div", { style: { fontFamily: font.mono, fontSize: 11.5, color: T.inkSoft, marginTop: 2 } }, e.time),
            h("div", { style: { fontFamily: font.body, fontSize: 12, color: T.inkSoft, lineHeight: 1.5, marginTop: 3 } }, e.note))
        );
      })
    ),
    h(Card, { style: { border: "none", background: "transparent", padding: "0 4px" } },
      h("div", { style: { fontFamily: font.body, fontSize: 11.5, color: T.inkSoft, lineHeight: 1.55 } },
        "Times are Eastern. Releases move price through the surprise against expectations, not the number itself."))
  );
}

function priceChart(series) {
  const w = 340, hgt = 170, padL = 8, padR = 52, padT = 12, padB = 14;
  let min = Infinity, max = -Infinity;
  for (let i = 0; i < series.length; i++) { if (series[i] < min) min = series[i]; if (series[i] > max) max = series[i]; }
  const span = (max - min) || 1;
  min -= span * 0.08; max += span * 0.08;
  const X = function (i) { return padL + (w - padL - padR) * (i / (series.length - 1)); };
  const Y = function (v) { return padT + (hgt - padT - padB) * (1 - (v - min) / (max - min)); };
  let line = "";
  for (let i = 0; i < series.length; i++) { line += (i === 0 ? "M " : " L ") + fmt(X(i), 1) + " " + fmt(Y(series[i]), 1); }
  const area = line + " L " + fmt(X(series.length - 1), 1) + " " + hgt + " L " + padL + " " + hgt + " Z";
  const last = series[series.length - 1];
  // Three round-ish gridlines inside the visible span.
  const grid = [];
  const step = span > 3 ? 2 : span > 1.2 ? 1 : 0.5;
  for (let g = Math.ceil(min / step) * step; g <= max; g += step) {
    grid.push(h("g", { key: "g" + g },
      h("line", { x1: padL, y1: Y(g), x2: w - padR + 6, y2: Y(g), stroke: T.line, strokeWidth: 1 }),
      h("text", { x: w - padR + 10, y: Y(g) + 4, fill: T.inkSoft, fontFamily: font.mono, fontSize: 10.5 }, fmt(g, step < 1 ? 1 : 0))));
  }
  return h("svg", { width: "100%", viewBox: "0 0 " + w + " " + hgt, role: "img", "aria-label": "WTI price chart, last " + fmt(last) },
    grid,
    h("path", { d: area, fill: T.brass, opacity: 0.10 }),
    h("path", { d: line, fill: "none", stroke: T.brass, strokeWidth: 2 }),
    h("circle", { cx: X(series.length - 1), cy: Y(last), r: 3.2, fill: T.brass }),
    h("rect", { x: w - padR + 4, y: Y(last) - 10, width: padR - 6, height: 19, rx: 4, fill: T.brass }),
    h("text", { x: w - padR + 8, y: Y(last) + 4, fill: T.onAccent, fontFamily: font.mono, fontSize: 11, fontWeight: 700 }, fmt(last))
  );
}

function ChartsScreen(props) {
  const range = props.range, setRange = props.setRange;
  const series = range === "1D" ? D.chart1D : D.chart5D;
  return h("div", null,
    h(Card, null,
      h("div", { style: { display: "flex", alignItems: "center", marginBottom: 10 } },
        SectionLabel("WTI crude"),
        h("div", { role: "group", "aria-label": "Chart range", style: { marginLeft: "auto", display: "flex", border: "1px solid " + T.line, borderRadius: 999, overflow: "hidden" } },
          ["1D", "5D"].map(function (r) {
            return h("button", { key: r, onClick: function () { setRange(r); }, "aria-pressed": range === r ? "true" : "false",
              style: { cursor: "pointer", border: "none", background: range === r ? T.brassSoft : "transparent",
                color: range === r ? T.brass : T.inkSoft, fontFamily: font.body, fontSize: 12, fontWeight: 800,
                padding: "7px 14px", minHeight: 34 } }, r);
          }))),
      priceChart(series),
      h("div", { style: { fontFamily: font.body, fontSize: 11.5, color: T.inkSoft, marginTop: 8, lineHeight: 1.5 } },
        range === "1D" ? "Today's session, 5-minute closes." : "Five sessions, hourly closes. The Tuesday gap is why chart patterns alone don't predict oil: that move was a headline, not a formation.")
    )
  );
}

function ScoreboardScreen() {
  const S = D.scoreboard;
  const stat = function (label, value, color) {
    return h("div", { style: { flex: "1 1 0", textAlign: "center", padding: "4px 2px" } },
      h("div", { style: { fontFamily: font.mono, fontSize: 22, fontWeight: 700, color: color || T.heading } }, value),
      h("div", { style: { fontFamily: font.body, fontSize: 10.5, fontWeight: 700, color: T.inkSoft, marginTop: 2, lineHeight: 1.35 } }, label));
  };
  const outcome = function (o) {
    if (o === "hit") return h("span", { style: { color: T.up, fontWeight: 800 } }, "\u2713 hit");
    if (o === "miss") return h("span", { style: { color: T.down, fontWeight: 800 } }, "\u2717 miss");
    if (o === "open") return h("span", { style: { color: T.amber, fontWeight: 800 } }, "open");
    return h("span", { style: { color: T.neutral, fontWeight: 700 } }, "stood down");
  };
  return h("div", null,
    h(Card, null,
      SectionLabel(S.windowLabel),
      h("div", { style: { display: "flex", gap: 4 } },
        stat("model accuracy when it fired", Math.round(S.accuracy * 100) + "%", T.up),
        stat("baseline: " + S.baselineLabel, Math.round(S.baseline * 100) + "%"),
        stat("calls fired / stand-downs", S.fired + " / " + S.standDowns)),
      h("div", { style: { fontFamily: font.body, fontSize: 12, color: T.inkSoft, lineHeight: 1.55, marginTop: 10, borderTop: "1px solid " + T.line, paddingTop: 10 } },
        "The only comparison that matters: the model against \u201C" + S.baselineLabel + ".\u201D If this gap closes, ignore the Today card and keep the briefing. " + S.calibrationNote)
    ),

    // Live record, kept separate from the backtest on purpose. Walk-forward
    // numbers are a promise; this table is the receipt.
    S.liveResolved !== undefined && S.liveResolved !== null ? h(Card, null,
      SectionLabel("Live record since launch"),
      S.liveResolved > 0
        ? h("div", { style: { display: "flex", gap: 4 } },
            h("div", { style: { flex: "1 1 0", textAlign: "center" } },
              h("div", { style: { fontFamily: font.mono, fontSize: 22, fontWeight: 700, color: T.heading } },
                S.liveAccuracy === null || S.liveAccuracy === undefined ? "\u2014" : Math.round(S.liveAccuracy * 100) + "%"),
              h("div", { style: { fontFamily: font.body, fontSize: 10.5, fontWeight: 700, color: T.inkSoft, marginTop: 2 } }, "live accuracy")),
            h("div", { style: { flex: "1 1 0", textAlign: "center" } },
              h("div", { style: { fontFamily: font.mono, fontSize: 22, fontWeight: 700, color: T.heading } }, S.liveResolved),
              h("div", { style: { fontFamily: font.body, fontSize: 10.5, fontWeight: 700, color: T.inkSoft, marginTop: 2 } }, "calls resolved")),
            h("div", { style: { flex: "1 1 0", textAlign: "center" } },
              h("div", { style: { fontFamily: font.mono, fontSize: 22, fontWeight: 700, color: T.heading } }, S.liveStands || 0),
              h("div", { style: { fontFamily: font.body, fontSize: 10.5, fontWeight: 700, color: T.inkSoft, marginTop: 2 } }, "stood down")))
        : h("div", { style: { fontFamily: font.body, fontSize: 13, color: T.inkSoft, lineHeight: 1.6 } },
            "No calls resolved yet. The first one settles the morning after the pipeline's first run \u2014 see the note in Settings about why resolution is next-morning in this version."),
      S.brier ? h("div", { style: { fontFamily: font.body, fontSize: 11.5, color: T.inkSoft, lineHeight: 1.55, marginTop: 10, borderTop: "1px solid " + T.line, paddingTop: 10 } },
        "Brier score ", h("span", { style: { fontFamily: font.mono, fontWeight: 700, color: T.ink } }, fmt(S.brier, 4)),
        " against a baseline of ", h("span", { style: { fontFamily: font.mono, fontWeight: 700, color: T.ink } }, fmt(S.brierBaseline, 4)),
        ". This scores the probability itself, not just the direction \u2014 lower is better.") : null
    ) : null,

    // Calibration. A 58% that resolves 58% of the time is worth acting on;
    // a 58% that resolves 51% of the time is a number pretending to be one.
    S.calibration && S.calibration.length ? h(Card, null,
      SectionLabel("Calibration"),
      S.calibration.map(function (c, i) {
        const gap = Math.abs(c.predicted - c.actual);
        const gapColor = gap < 0.03 ? T.up : gap < 0.06 ? T.amber : T.down;
        return h("div", { key: i, style: { display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: i === 0 ? "none" : "1px solid " + T.line } },
          h("span", { style: { fontFamily: font.mono, fontSize: 12, color: T.inkSoft, width: 62, flex: "0 0 auto" } }, c.band),
          h("span", { style: { fontFamily: font.mono, fontSize: 12.5, color: T.ink } }, "said " + Math.round(c.predicted * 100) + "%"),
          h("span", { style: { fontFamily: font.mono, fontSize: 12.5, fontWeight: 700, color: gapColor } }, "\u2192 was " + Math.round(c.actual * 100) + "%"),
          h("span", { style: { fontFamily: font.mono, fontSize: 11, color: T.inkSoft, marginLeft: "auto" } }, "n=" + c.n));
      }),
      h("div", { style: { fontFamily: font.body, fontSize: 11.5, color: T.inkSoft, lineHeight: 1.55, marginTop: 8, borderTop: "1px solid " + T.line, paddingTop: 8 } },
        "Left column is what the model claimed; right is what actually happened. The closer these track, the more the percentage on the Today card means what it says.")
    ) : null,
    h(Card, { style: { padding: "6px 16px" } },
      S.log.map(function (r, i) {
        return h("div", { key: i, style: { display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: i === 0 ? "none" : "1px solid " + T.line } },
          h("span", { style: { fontFamily: font.mono, fontSize: 12, color: T.inkSoft, width: 52, flex: "0 0 auto" } }, r.date),
          h("span", { style: { color: dirColor(r.call === "none" ? "none" : r.call), fontSize: 11, width: 14 } }, dirArrow(r.call === "none" ? "none" : r.call)),
          h("span", { style: { fontFamily: font.mono, fontSize: 12.5, fontWeight: 700, color: T.ink, width: 44 } }, r.call === "none" ? "\u2014" : Math.round(r.prob * 100) + "%"),
          h("span", { style: { fontFamily: font.body, fontSize: 12.5, marginLeft: "auto" } }, outcome(r)));
      })
    ),
    h(Card, { style: { border: "none", background: "transparent", padding: "0 4px" } },
      h("div", { style: { fontFamily: font.body, fontSize: 11.5, color: T.inkSoft, lineHeight: 1.55 } },
        "Every call is timestamped at the 8:00 AM lock, before the session. Nothing here is ever edited after the fact \u2014 the model earns trust or loses it in this table."))
  );
}

function SettingsScreen(props) {
  const themeId = props.themeId, chooseTheme = props.chooseTheme;
  return h("div", null,
    h(Card, null,
      SectionLabel("Appearance"),
      THEMES.map(function (t) {
        const on = themeId === t.id;
        return h("button", { key: t.id, onClick: function () { chooseTheme(t.id); },
          "aria-pressed": on ? "true" : "false",
          style: { display: "flex", alignItems: "center", width: "100%", cursor: "pointer",
            background: on ? T.brassSoft : "transparent", color: T.ink,
            border: "1px solid " + (on ? T.brass : T.line), borderRadius: 10,
            padding: "12px 14px", marginBottom: 8, fontFamily: font.body, fontSize: 13.5, fontWeight: 700, minHeight: 44 } },
          t.label,
          on ? h("span", { style: { marginLeft: "auto", color: T.brass } }, "\u2713") : null);
      })
    ),
    h(Card, null,
      SectionLabel("Model"),
      h("div", { style: { fontFamily: font.body, fontSize: 13, color: T.ink, lineHeight: 1.6 } },
        "Daily lean locks at ", h("span", { style: { fontFamily: font.mono, fontWeight: 700 } }, "8:00 AM ET"),
        " and is never revised during the session. Stand-down band: probabilities inside ",
        h("span", { style: { fontFamily: font.mono, fontWeight: 700 } }, "45\u201355%"), " produce no call.")
    ),
    // The v1B limitation, stated plainly where anyone looking for it will
    // find it. Same text as data.json and README-V1B.txt.
    h(Card, { style: { background: T.amberSoft, borderColor: T.amber } },
      SectionLabel("Known limitation in this version"),
      h("div", { style: { fontFamily: font.body, fontSize: 13, color: T.ink, lineHeight: 1.6 } },
        D.limitation ||
        "Free EIA and FRED data publishes end-of-day with a lag, so in this local-script version a call is resolved the next morning rather than live at the 2:30 PM ET settlement. The intraday tracking strip stays empty until a live price feed arrives in v1C."),
      h("div", { style: { fontFamily: font.body, fontSize: 12, color: T.inkSoft, lineHeight: 1.6, marginTop: 8 } },
        "The call itself is still locked at 8:00 AM and never revised. Only the moment of resolution moves.")
    ),
    h(Card, null,
      SectionLabel("Data sources"),
      D.sources.map(function (s, i) {
        return h("div", { key: i, style: { display: "flex", gap: 10, padding: "9px 0", borderTop: i === 0 ? "none" : "1px solid " + T.line, alignItems: "baseline" } },
          h("span", { style: { fontFamily: font.body, fontSize: 13, color: T.ink, minWidth: 0, flex: "1 1 auto" } }, s.name),
          h("span", { style: { fontFamily: font.body, fontSize: 11, fontWeight: 700, color: T.amber, flex: "0 0 auto" } }, s.status));
      })
    ),
    h(Card, null,
      SectionLabel("About"),
      h("div", { style: { fontFamily: font.body, fontSize: 12.5, color: T.inkSoft, lineHeight: 1.6 } },
        "Crude Compass " + APP_VERSION + ". Built on the zero-build architecture of the Estate File template: bundled React, plain JavaScript, no code downloaded at runtime."),
      h(Disclaimer)
    )
  );
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

function App() {
  const [themeId, setThemeId] = useState(loadTheme());
  const [tab, setTab] = useState("today");
  const [chartRange, setChartRange] = useState("1D");
  const [loading, setLoading] = useState(true);
  const [, force] = useState(0);

  // Load data.json once on mount. Failure is not an error state: the app
  // falls back to sample data and the banner says so.
  useEffect(function () {
    let cancelled = false;
    loadData().then(function () {
      if (!cancelled) { setLoading(false); force(function (n) { return n + 1; }); }
    });
    return function () { cancelled = true; };
  }, []);

  useEffect(function () {
    setThemeTokens(themeId);
    force(function (n) { return n + 1; });
  }, [themeId]);

  useEffect(function () {
    if (themeId !== "auto" || !window.matchMedia) return undefined;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = function () { setThemeTokens("auto"); force(function (n) { return n + 1; }); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
    return function () {
      if (mq.removeEventListener) mq.removeEventListener("change", onChange);
      else if (mq.removeListener) mq.removeListener(onChange);
    };
  }, [themeId]);

  const chooseTheme = function (id) { saveTheme(id); setThemeId(id); };

  const TABS = [
    { id: "today", label: "Today" },
    { id: "briefing", label: "Briefing" },
    { id: "calendar", label: "Calendar" },
    { id: "charts", label: "Charts" },
    { id: "scoreboard", label: "Scoreboard" }
  ];

  return h("div", { style: { fontFamily: font.body, color: T.ink, background: T.bg, minHeight: "100vh" } },

    // Header: compass mark, brand, settings cog. Same bones as Estate File,
    // different instrument on the flag pole.
    h("div", { style: { background: T.header, color: "#EDF1FA", padding: "calc(10px + env(safe-area-inset-top)) 16px 0", borderBottom: "2px solid " + HDR.gold } },
      h("div", { style: { display: "flex", alignItems: "center", gap: 10 } },
        h("div", { style: { width: 42, height: 42, borderRadius: 999, border: "1.5px solid " + HDR.gold, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 auto" } },
          h(CompassMark, { size: 27, color: HDR.gold })),
        h("div", { style: { minWidth: 0, flex: "1 1 auto" } },
          h("div", { style: { fontFamily: font.display, fontWeight: 700, fontSize: "clamp(20px, 6vw, 28px)", lineHeight: 1.05, letterSpacing: -0.25 } }, "Crude Compass"),
          h("div", { style: { fontFamily: font.body, fontSize: 10.5, fontWeight: 700, letterSpacing: 1.4, color: HDR.gold, marginTop: 2 } }, "DAILY WTI TREND")),
        h("button", {
          onClick: function () { setTab("settings"); },
          "aria-label": "Settings", "aria-current": tab === "settings" ? "page" : undefined, title: "Settings",
          style: { marginLeft: "auto", flex: "0 0 auto", cursor: "pointer", width: 44, height: 44, padding: 0,
            background: tab === "settings" ? "rgba(201,162,75,0.14)" : "transparent",
            border: "1.5px solid " + HDR.gold, borderRadius: 999, display: "inline-flex", alignItems: "center", justifyContent: "center",
            color: HDR.gold, fontFamily: font.body, fontSize: 25, fontWeight: 800, lineHeight: 1 }
        }, h("span", { "aria-hidden": "true", style: { transform: "translateY(-1px)" } }, "\u2699\uFE0E"))),

      // Tab strip with the right-edge fade cue, straight from the template.
      h("div", { style: { position: "relative", marginTop: 9 } },
        h("div", { className: "hscroll", style: { display: "flex", gap: 16 } },
          TABS.map(function (tb) {
            return h("button", {
              key: tb.id,
              onClick: function () { setTab(tb.id); },
              "aria-current": tab === tb.id ? "page" : undefined,
              style: { flex: "0 0 auto", border: "none", background: "transparent", cursor: "pointer",
                padding: "14px 2px 11px", fontFamily: font.body, fontSize: 13, letterSpacing: 0.2,
                fontWeight: tab === tb.id ? 800 : 600,
                color: tab === tb.id ? HDR.gold : HDR.idle,
                borderBottom: "3px solid " + (tab === tb.id ? HDR.gold : "transparent") }
            }, tb.label);
          })),
        h("div", { "aria-hidden": "true", style: { position: "absolute", top: 0, right: -16, width: 26, bottom: 0, pointerEvents: "none",
          background: "linear-gradient(to right, transparent, " + T.header + ")" } }))),

    // Data-state banner, three states. The app must never show stale or
    // sample numbers without saying so — that is the one dishonesty a
    // decision-support tool cannot afford.
    (function () {
      if (loading) {
        return h("div", { style: { background: T.neutralSoft, borderBottom: "1px solid " + T.line, padding: "8px 16px",
          fontFamily: font.body, fontSize: 11.5, fontWeight: 700, color: T.inkSoft, textAlign: "center" } }, "Loading\u2026");
      }
      if (!D.isLive) {
        return h("div", { style: { background: T.amberSoft, borderBottom: "1px solid " + T.amber, padding: "8px 16px",
          fontFamily: font.body, fontSize: 11.5, fontWeight: 700, color: T.ink, textAlign: "center" } },
          "Sample data \u2014 every number is a placeholder. Run the v1B pipeline to populate data.json.");
      }
      const age = dataAgeDays();
      if (age !== null && age > STALE_DAYS) {
        return h("div", { style: { background: T.cardDown, borderBottom: "1px solid " + T.down, padding: "8px 16px",
          fontFamily: font.body, fontSize: 11.5, fontWeight: 700, color: T.down, textAlign: "center" } },
          "Stale \u2014 data is " + age + " days old. The pipeline has not run. Do not act on this screen.");
      }
      return h("div", { style: { background: T.brassSoft, borderBottom: "1px solid " + T.line, padding: "7px 16px",
        fontFamily: font.body, fontSize: 11, fontWeight: 700, color: T.brass, textAlign: "center", letterSpacing: 0.3 } },
        "LIVE \u00b7 data through " + D.dataThrough + " \u00b7 " + (D.version || APP_VERSION));
    })(),

    h("div", { style: { padding: "14px 14px calc(30px + env(safe-area-inset-bottom))", maxWidth: 560, margin: "0 auto" } },
      tab === "today" ? h(TodayScreen) : null,
      tab === "briefing" ? h(BriefingScreen) : null,
      tab === "calendar" ? h(CalendarScreen) : null,
      tab === "charts" ? h(ChartsScreen, { range: chartRange, setRange: setChartRange }) : null,
      tab === "scoreboard" ? h(ScoreboardScreen) : null,
      tab === "settings" ? h(SettingsScreen, { themeId: themeId, chooseTheme: chooseTheme }) : null)
  );
}

setThemeTokens(loadTheme());
ReactDOM.createRoot(document.getElementById("root")).render(h(App));
