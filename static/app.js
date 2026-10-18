/**
 * app.js — RakshaNet frontend
 *
 * Phase 1 + Phase 2:
 *   - Route Planner (Shortest vs Safest side-by-side with divergent presets)
 *   - Typeable Autocomplete with local street index & Nominatim
 *   - Incident Reporting (with duplicate 409 detection & edge snapping)
 *   - Community Verification & Feed (progress meters, "I saw this too" voting)
 *   - Live Verified Pins with interactive popups & incremental route score updates
 *   - Demo Mode user switcher & simulated location
 */

"use strict";

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  map:             null,
  meta:            null,
  currentTab:      "route", // "route" | "report" | "verify"
  currentUser:     null,
  demoUsers:       [],

  // Route state
  startLatLon:     null,   // [lat, lon]
  endLatLon:       null,
  clickPhase:      "start",// "start" | "end" | "done"
  hour:            "now",
  markerStart:     null,
  markerEnd:       null,
  routeLayers:     [],
  heatmapLayer:    null,
  heatmapOn:       false,
  activeRouteTab:  "safest",
  lastRoute:       null,

  // Report state
  reportLatLon:    null,
  reportMarker:    null,
  reportType:      "poor_lighting",
  reportTimeVal:   "now",
  lastDuplicateId: null,

  // Map markers
  verifiedMarkers: [],
  pendingMarkers:  [],
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// Route elements
const elStartInput     = $("start-input");
const elEndInput       = $("end-input");
const elStartClear     = $("start-clear");
const elEndClear       = $("end-clear");
const elStartDropdown  = $("start-dropdown");
const elEndDropdown    = $("end-dropdown");
const elBtnSwap        = $("btn-swap");
const elBtnGeolocate   = $("btn-geolocate");
const elHintNext       = $("hint-next");
const elMapHint        = $("map-hint");
const elLoadingOverlay = $("loading-overlay");
const elLoadingText    = $("loading-text");
const elErrorToast     = $("error-toast");
const elResultsCard    = $("results-card");
const elVerdictText    = $("verdict-text");
const elTabSafest      = $("tab-safest");
const elTabShortest    = $("tab-shortest");
const elPanelSafest    = $("panel-safest");
const elPanelShortest  = $("panel-shortest");
const elStatsSafest    = $("stats-safest");
const elStatsShortest  = $("stats-shortest");
const elSegBreakdown   = $("segment-breakdown");
const elHourSlider     = $("hour-slider");
const elSliderVal      = $("slider-val-display");
const elToggleHeatmap  = $("toggle-heatmap");
const elSosBtn         = $("sos-btn");

// Phase 2 Nav Tab elements
const elTabBtnRoute    = $("tab-btn-route");
const elTabBtnReport   = $("tab-btn-report");
const elTabBtnVerify   = $("tab-btn-verify");
const elPaneRoute      = $("pane-route");
const elPaneReport     = $("pane-report");
const elPaneVerify     = $("pane-verify");

// User elements
const elUserAvatar     = $("user-avatar");
const elUserDispName   = $("user-display-name");
const elUserRoleLabel  = $("user-role-label");
const elDemoUserSelect = $("demo-user-select");
const elBtnAuthAction  = $("btn-auth-action");

// Report elements
const elReportLocName  = $("report-loc-name");
const elReportLocCoords= $("report-loc-coords");
const elReportLocBox   = $("report-loc-box");
const elBtnReportPick  = $("btn-report-pick-map");
const elReportNote     = $("report-note-input");
const elReportCharCnt  = $("report-char-count");
const elBtnSubmitReport= $("btn-submit-report");
const elReportSuccess  = $("report-success-box");
const elReportSuccessMsg= $("report-success-msg");
const elBtnReportAgain = $("btn-report-another");
const elDupConflictBox = $("duplicate-conflict-box");
const elDupConflictMsg = $("duplicate-conflict-msg");
const elBtnConfirmDup  = $("btn-confirm-duplicate");
const elCustomTimeRow  = $("custom-time-row");
const elCustomTimeInput= $("report-custom-time");

// Verify & Feed elements
const elPendingList    = $("pending-list-container");
const elVerifiedFeed   = $("verified-feed-container");
const elFeedTypeSelect = $("feed-type-select");

// Auth Modal elements
const elAuthModalBackdrop = $("auth-modal-backdrop");
const elBtnCloseAuthModal = $("btn-close-auth-modal");
const elBtnTabLogin       = $("btn-tab-login");
const elBtnTabRegister    = $("btn-tab-register");
const elFormLogin         = $("form-login");
const elFormRegister      = $("form-register");
const elLoginIdentifier   = $("login-identifier");
const elLoginPassword     = $("login-password");
const elRegName           = $("reg-name");
const elRegIdentifier     = $("reg-identifier");
const elRegPassword       = $("reg-password");

// ── Utility ───────────────────────────────────────────────────────────────────

function showLoading(msg = "Computing safest route…") {
  elLoadingText.textContent = msg;
  elLoadingOverlay.classList.remove("hidden");
}
function hideLoading() {
  elLoadingOverlay.classList.add("hidden");
}

let toastTimeout = null;
function showToast(msg, type = "error", duration = 4000) {
  elErrorToast.textContent = msg;
  elErrorToast.className   = `toast ${type}`;
  elErrorToast.classList.remove("hidden");
  clearTimeout(toastTimeout);
  if (duration > 0) toastTimeout = setTimeout(() => elErrorToast.classList.add("hidden"), duration);
}

function formatDist(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}
function updateClearBtn(inputEl, clearBtn) {
  clearBtn.classList.toggle("hidden", !inputEl.value.trim());
}

function timeAgo(dateString) {
  if (!dateString) return "Recently";
  const d = new Date(dateString.replace(" ", "T"));
  const now = new Date();
  const diffSec = Math.floor((now - d) / 1000);
  if (isNaN(diffSec) || diffSec < 60) return "Just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  const days = Math.floor(diffSec / 86400);
  return `${days}d ago`;
}

// ══════════════════════════════════════════════════════════════════════════════
// Autocomplete class
// ══════════════════════════════════════════════════════════════════════════════

class Autocomplete {
  constructor(inputEl, dropdownEl, clearBtn, onSelect) {
    this.input     = inputEl;
    this.dropdown  = dropdownEl;
    this.clearBtn  = clearBtn;
    this.onSelect  = onSelect;
    this.items     = [];
    this.activeIdx = -1;
    this.timer     = null;

    this.input.addEventListener("input",   () => this._onInput());
    this.input.addEventListener("keydown", e  => this._onKeydown(e));
    this.input.addEventListener("focus",   () => { if (this.items.length) this._showDropdown(); });
    this.clearBtn.addEventListener("click", () => this.clear());

    document.addEventListener("click", e => {
      if (!this.input.contains(e.target) && !this.dropdown.contains(e.target)) {
        this._hideDropdown();
      }
    });
  }

  _onInput() {
    clearTimeout(this.timer);
    updateClearBtn(this.input, this.clearBtn);
    const q = this.input.value.trim();
    if (!q) { this.items = []; this._hideDropdown(); return; }
    this.timer = setTimeout(() => this._fetchSuggestions(q), 300);
  }

  async _fetchSuggestions(q) {
    try {
      const res  = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      this.items     = data.results || [];
      this.activeIdx = -1;
      this._renderDropdown();
    } catch (_) {
      this.items = [];
      this._hideDropdown();
    }
  }

  _renderDropdown() {
    this.dropdown.innerHTML = "";

    if (this.items.length === 0) {
      const li = document.createElement("li");
      li.className = "ac-no-match";
      li.textContent = "No matches in this area";
      this.dropdown.appendChild(li);
      this._showDropdown();
      return;
    }

    this.items.forEach((item, idx) => {
      const li    = document.createElement("li");
      li.className = "ac-item";
      li.setAttribute("role", "option");

      const icon  = item.source === "street" ? "🛣️" : "📍";
      const badge = item.source === "street"  ? "street" : "place";

      li.innerHTML =
        `<span class="ac-icon">${icon}</span>` +
        `<span class="ac-label">${this._esc(item.label)}</span>` +
        `<span class="ac-source-badge">${badge}</span>`;

      li.addEventListener("mousedown", e => { e.preventDefault(); this.selectIdx(idx); });
      this.dropdown.appendChild(li);
    });

    this._showDropdown();
  }

  _onKeydown(e) {
    if (this.dropdown.classList.contains("hidden")) {
      if (e.key === "Enter" && this.input.value.trim()) {
        e.preventDefault();
        this._fetchSuggestions(this.input.value.trim()).then(() => {
          if (this.items.length) this.selectIdx(0);
        });
      }
      return;
    }

    const items = this.dropdown.querySelectorAll(".ac-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      this.activeIdx = (this.activeIdx + 1) % items.length;
      this._updateActive(items);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      this.activeIdx = (this.activeIdx - 1 + items.length) % items.length;
      this._updateActive(items);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (this.activeIdx >= 0 && this.activeIdx < this.items.length) {
        this.selectIdx(this.activeIdx);
      } else if (this.items.length > 0) {
        this.selectIdx(0);
      }
    } else if (e.key === "Escape") {
      this._hideDropdown();
    }
  }

  _updateActive(items) {
    items.forEach((el, i) => el.classList.toggle("ac-item-active", i === this.activeIdx));
    if (items[this.activeIdx]) items[this.activeIdx].scrollIntoView({ block: "nearest" });
  }

  selectIdx(idx) {
    const item = this.items[idx];
    if (!item) return;
    this.input.value = item.label;
    updateClearBtn(this.input, this.clearBtn);
    this._hideDropdown();
    this.onSelect(item);
  }

  setLabel(label) {
    this.input.value = label;
    updateClearBtn(this.input, this.clearBtn);
  }

  clear() {
    this.input.value = "";
    this.items = [];
    this._hideDropdown();
    updateClearBtn(this.input, this.clearBtn);
  }

  _showDropdown() { this.dropdown.classList.remove("hidden"); }
  _hideDropdown() { this.dropdown.classList.add("hidden"); this.activeIdx = -1; }

  _esc(str) {
    return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
}

// ── Instantiate autocomplete ──────────────────────────────────────────────────
const startAC = new Autocomplete(elStartInput, elStartDropdown, elStartClear, item => {
  state.startLatLon = [item.lat, item.lon];
  placeStartMarker(state.startLatLon);
  state.map.panTo(state.startLatLon, { animate: true });
  if (!state.endLatLon) {
    state.clickPhase = "end";
    elMapHint.classList.remove("hidden");
    updateHint();
  } else {
    state.clickPhase = "done";
    elMapHint.classList.add("hidden");
    maybeRoute();
  }
});

const endAC = new Autocomplete(elEndInput, elEndDropdown, elEndClear, item => {
  state.endLatLon = [item.lat, item.lon];
  placeEndMarker(state.endLatLon);
  state.map.panTo(state.endLatLon, { animate: true });
  state.clickPhase = "done";
  elMapHint.classList.add("hidden");
  maybeRoute();
});

// ── Map initialisation ────────────────────────────────────────────────────────

async function initMap() {
  try {
    const res  = await fetch("/api/meta");
    state.meta = await res.json();
  } catch (e) {
    showToast("Could not connect to RakshaNet backend.", "error");
    return;
  }

  const { center, bbox, radius_m } = state.meta;

  state.map = L.map("map", {
    center: [center.lat, center.lon],
    zoom: 14,
    zoomControl: true,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(state.map);

  L.circle([center.lat, center.lon], {
    radius: radius_m,
    color: "#E91E8C",
    weight: 1.5,
    dashArray: "4 6",
    fillColor: "#E91E8C",
    fillOpacity: 0.03,
  }).addTo(state.map);

  state.map.on("click", onMapClick);

  // Initialize Auth & Users
  await initAuth();

  // Load verified incident pins on map
  await loadVerifiedMapPins();

  // Initial hint
  updateHint();
}

function isOutsideArea(lat, lon) {
  if (!state.meta) return false;
  const { north, south, east, west } = state.meta.bbox;
  return lat < south || lat > north || lon < west || lon > east;
}

// ── Auth & User switching ─────────────────────────────────────────────────────

async function initAuth() {
  try {
    const res = await fetch("/api/auth/me");
    const data = await res.json();
    state.currentUser = data.user;
    state.demoUsers   = data.demo_users || [];

    if (data.user) {
      elUserAvatar.textContent = data.user.name.charAt(0).toUpperCase();
      elUserDispName.textContent = data.user.name;
      elBtnAuthAction.textContent = "Log Out";
    } else {
      elUserAvatar.textContent = "?";
      elUserDispName.textContent = "Guest";
      elBtnAuthAction.textContent = "Log In";
    }

    if (data.demo_mode && elDemoUserSelect) {
      elDemoUserSelect.innerHTML = "";
      state.demoUsers.forEach(u => {
        const opt = document.createElement("option");
        opt.value = u.id;
        opt.textContent = u.name;
        if (data.user && data.user.id === u.id) opt.selected = true;
        elDemoUserSelect.appendChild(opt);
      });
    }
  } catch (_) {}
}

if (elDemoUserSelect) {
  elDemoUserSelect.addEventListener("change", async () => {
    const selectedUserId = elDemoUserSelect.value;
    try {
      const res = await fetch("/api/auth/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: selectedUserId }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`Switched active user to ${data.user.name}`, "info", 2000);
        await initAuth();
        if (state.currentTab === "verify") {
          await fetchAndRenderPending();
          await fetchAndRenderVerifiedFeed();
        }
      } else {
        showToast(data.error || "Failed to switch user", "error");
      }
    } catch (_) {
      showToast("Error switching user", "error");
    }
  });
}

if (elBtnAuthAction) {
  elBtnAuthAction.addEventListener("click", async () => {
    if (state.currentUser) {
      // Logout
      await fetch("/api/auth/logout", { method: "POST" });
      showToast("Logged out.", "info", 2000);
      await initAuth();
    } else {
      openAuthModal();
    }
  });
}

function openAuthModal() {
  elAuthModalBackdrop.classList.remove("hidden");
}
function closeAuthModal() {
  elAuthModalBackdrop.classList.add("hidden");
}
if (elBtnCloseAuthModal) elBtnCloseAuthModal.addEventListener("click", closeAuthModal);

if (elBtnTabLogin && elBtnTabRegister) {
  elBtnTabLogin.addEventListener("click", () => {
    elBtnTabLogin.classList.add("active");
    elBtnTabRegister.classList.remove("active");
    elFormLogin.classList.remove("hidden");
    elFormRegister.classList.add("hidden");
  });
  elBtnTabRegister.addEventListener("click", () => {
    elBtnTabRegister.classList.add("active");
    elBtnTabLogin.classList.remove("active");
    elFormRegister.classList.remove("hidden");
    elFormLogin.classList.add("hidden");
  });
}

if (elFormLogin) {
  elFormLogin.addEventListener("submit", async e => {
    e.preventDefault();
    const identifier = elLoginIdentifier.value.trim();
    const password = elLoginPassword.value;
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier, password }),
      });
      const data = await res.json();
      if (res.ok) {
        closeAuthModal();
        showToast(`Welcome back, ${data.user.name}!`, "info", 2500);
        await initAuth();
      } else {
        showToast(data.error || "Login failed", "error");
      }
    } catch (_) {
      showToast("Connection error during login", "error");
    }
  });
}

if (elFormRegister) {
  elFormRegister.addEventListener("submit", async e => {
    e.preventDefault();
    const name = elRegName.value.trim();
    const identifier = elRegIdentifier.value.trim();
    const password = elRegPassword.value;
    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, identifier, password }),
      });
      const data = await res.json();
      if (res.ok) {
        closeAuthModal();
        showToast(`Account created for ${data.user.name}!`, "info", 2500);
        await initAuth();
      } else {
        showToast(data.error || "Registration failed", "error");
      }
    } catch (_) {
      showToast("Connection error during registration", "error");
    }
  });
}

// ── Tab Navigation (Route | Report | Verify & Feed) ──────────────────────────

function switchMainTab(tab) {
  state.currentTab = tab;

  [elTabBtnRoute, elTabBtnReport, elTabBtnVerify].forEach(btn => {
    if (btn) btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  [elPaneRoute, elPaneReport, elPaneVerify].forEach(pane => {
    if (pane) pane.classList.toggle("hidden", pane.id !== `pane-${tab}`);
  });

  // Manage pending markers visibility (only shown when Verify tab is active)
  if (tab === "verify") {
    state.pendingMarkers.forEach(m => m.addTo(state.map));
    fetchAndRenderPending();
    fetchAndRenderVerifiedFeed();
  } else {
    state.pendingMarkers.forEach(m => m.remove());
  }

  // Update map hint message
  updateHint();
}

if (elTabBtnRoute)  elTabBtnRoute.addEventListener("click",  () => switchMainTab("route"));
if (elTabBtnReport) elTabBtnReport.addEventListener("click", () => switchMainTab("report"));
if (elTabBtnVerify) elTabBtnVerify.addEventListener("click", () => switchMainTab("verify"));

// ── Marker creators ───────────────────────────────────────────────────────────

function makeIcon(label, bg, border = "#fff") {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:28px;height:28px;border-radius:50%;
      background:${bg};border:2px solid ${border};
      color:#fff;font-family:Inter,sans-serif;font-weight:700;font-size:12px;
      display:flex;align-items:center;justify-content:center;
      box-shadow:0 2px 8px rgba(0,0,0,0.5);">${label}</div>`,
    iconSize:   [28, 28],
    iconAnchor: [14, 14],
  });
}

function placeStartMarker(latlon) {
  if (state.markerStart) state.markerStart.setLatLng(latlon);
  else state.markerStart = L.marker(latlon, { icon: makeIcon("A", "#4CAF50") }).addTo(state.map);
}

function placeEndMarker(latlon) {
  if (state.markerEnd) state.markerEnd.setLatLng(latlon);
  else state.markerEnd = L.marker(latlon, { icon: makeIcon("B", "#F44336") }).addTo(state.map);
}

/** Fetch the nearest street name for a lat/lon from /api/nearest. */
async function reverseGeocode(lat, lon) {
  try {
    const res  = await fetch(`/api/nearest?lat=${lat}&lon=${lon}`);
    const data = await res.json();
    return data.street_name || data.name || "Dropped pin";
  } catch (_) {
    return "Dropped pin";
  }
}

// ── Map click handler ─────────────────────────────────────────────────────────

async function onMapClick(e) {
  const lat = e.latlng.lat;
  const lon = e.latlng.lng;

  if (isOutsideArea(lat, lon)) {
    showToast("Outside the pilot area — please click within the highlighted boundary.", "error");
    return;
  }

  // Handle Report Tab mode
  if (state.currentTab === "report") {
    state.reportLatLon = [lat, lon];
    if (state.reportMarker) {
      state.reportMarker.setLatLng([lat, lon]);
    } else {
      state.reportMarker = L.marker([lat, lon], {
        icon: makeIcon("📍", "#E91E8C")
      }).addTo(state.map);
    }
    const label = await reverseGeocode(lat, lon);
    elReportLocName.textContent = label;
    elReportLocCoords.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    elReportLocBox.classList.add("selected");
    return;
  }

  // Handle Route Tab mode
  if (state.currentTab === "route") {
    const label = await reverseGeocode(lat, lon);
    if (state.clickPhase === "start" || state.clickPhase === "done") {
      state.startLatLon = [lat, lon];
      startAC.setLabel(label);
      placeStartMarker([lat, lon]);
      state.clickPhase = "end";
      elMapHint.classList.remove("hidden");
      updateHint();
      if (state.clickPhase !== "done") clearRoutesAndResults();
    } else {
      state.endLatLon = [lat, lon];
      endAC.setLabel(label);
      placeEndMarker([lat, lon]);
      state.clickPhase = "done";
      elMapHint.classList.add("hidden");
      maybeRoute();
    }
  }
}

function updateHint() {
  if (state.currentTab === "report") {
    elMapHint.classList.remove("hidden");
    $("map-hint").innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg> Click map to set <strong>Hazard Location</strong>`;
  } else if (state.currentTab === "verify") {
    elMapHint.classList.remove("hidden");
    $("map-hint").innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> Click <strong>"I saw this too"</strong> on cards to confirm reports`;
  } else {
    $("map-hint").innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg> Click map to set <strong id="hint-next">${state.clickPhase === "end" ? "End" : "Start"}</strong> point`;
  }
}

// ── Clear helpers ─────────────────────────────────────────────────────────────

function clearRoutesAndResults() {
  clearRoutes();
  elResultsCard.classList.add("hidden");
}

function clearRoutes() {
  state.routeLayers.forEach(l => l.remove());
  state.routeLayers = [];
}

function clearHeatmap() {
  if (state.heatmapLayer) { state.heatmapLayer.remove(); state.heatmapLayer = null; }
}

// ── Swap button ───────────────────────────────────────────────────────────────

elBtnSwap.addEventListener("click", () => {
  if (!state.startLatLon && !state.endLatLon) return;

  const tmpLatLon = state.startLatLon;
  state.startLatLon = state.endLatLon;
  state.endLatLon   = tmpLatLon;

  const newStartLabel = elEndInput.value;
  const newEndLabel   = elStartInput.value;

  if (state.startLatLon) {
    startAC.setLabel(newStartLabel);
    placeStartMarker(state.startLatLon);
  } else {
    startAC.clear();
    if (state.markerStart) { state.markerStart.remove(); state.markerStart = null; }
  }

  if (state.endLatLon) {
    endAC.setLabel(newEndLabel);
    placeEndMarker(state.endLatLon);
  } else {
    endAC.clear();
    if (state.markerEnd) { state.markerEnd.remove(); state.markerEnd = null; }
  }

  updateClearVisibility();
  maybeRoute();
});

// ── Geolocation button ────────────────────────────────────────────────────────

elBtnGeolocate.addEventListener("click", () => {
  if (!navigator.geolocation) {
    showToast("Geolocation is not supported by your browser.", "error");
    return;
  }
  showToast("Getting your location…", "info", 2000);
  navigator.geolocation.getCurrentPosition(
    async pos => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;

      if (isOutsideArea(lat, lon)) {
        showToast("Your location is outside the RakshaNet pilot area (Hauz Khas / Green Park, Delhi).", "error");
        return;
      }

      state.startLatLon = [lat, lon];
      const label = await reverseGeocode(lat, lon);
      startAC.setLabel(label);
      placeStartMarker([lat, lon]);
      state.map.panTo([lat, lon], { animate: true });
      updateClearVisibility();

      if (!state.endLatLon) {
        state.clickPhase = "end";
        elMapHint.classList.remove("hidden");
        updateHint();
      } else {
        state.clickPhase = "done";
        elMapHint.classList.add("hidden");
        maybeRoute();
      }
    },
    err => {
      const msgs = {
        1: "Location access denied — please allow location in your browser.",
        2: "Could not determine your location. Try again.",
        3: "Location request timed out.",
      };
      showToast(msgs[err.code] || "Geolocation error.", "error");
    },
    { timeout: 8000 }
  );
});

// ── Demo route presets ────────────────────────────────────────────────────────

document.querySelectorAll(".preset-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".preset-btn").forEach(b => b.classList.remove("active-preset"));
    btn.classList.add("active-preset");

    const presetId = btn.dataset.preset;
    const meta = state.meta;
    let preset = (meta?.demo_routes || []).find(r => r.id === presetId);

    let startLatLon, endLatLon, startName, endName;
    if (preset) {
      startLatLon = [preset.start[0], preset.start[1]];
      endLatLon   = [preset.end[0],   preset.end[1]];
      const parts = preset.name.split("→").map(s => s.trim());
      startName   = parts[0] || "Start";
      endName     = parts[1] || "Destination";
    } else {
      if (!meta) return;
      startLatLon = [meta.sample_start.lat, meta.sample_start.lon];
      endLatLon   = [meta.sample_end.lat,   meta.sample_end.lon];
      startName   = "Hauz Khas Village";
      endName     = "Green Park Metro";
    }

    state.startLatLon = startLatLon;
    state.endLatLon   = endLatLon;

    startAC.setLabel(startName);
    endAC.setLabel(endName);

    placeStartMarker(state.startLatLon);
    placeEndMarker(state.endLatLon);

    state.clickPhase = "done";
    elMapHint.classList.add("hidden");
    updateClearVisibility();

    await maybeRoute();
  });
});

// ── Clear button visibility ───────────────────────────────────────────────────

function updateClearVisibility() {
  updateClearBtn(elStartInput, elStartClear);
  updateClearBtn(elEndInput,   elEndClear);
}

elStartClear.addEventListener("click", () => {
  state.startLatLon = null;
  startAC.clear();
  if (state.markerStart) { state.markerStart.remove(); state.markerStart = null; }
  state.clickPhase = "start";
  elMapHint.classList.remove("hidden");
  updateHint();
  clearRoutesAndResults();
});

elEndClear.addEventListener("click", () => {
  state.endLatLon = null;
  endAC.clear();
  if (state.markerEnd) { state.markerEnd.remove(); state.markerEnd = null; }
  if (state.startLatLon) state.clickPhase = "end";
  elMapHint.classList.remove("hidden");
  updateHint();
  clearRoutesAndResults();
});

// ── Time controls ─────────────────────────────────────────────────────────────

function updateSliderDisplay(h) {
  elSliderVal.textContent = `${String(h).padStart(2,"0")}:00`;
}

document.querySelectorAll("#time-chips .chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#time-chips .chip").forEach(c => c.classList.remove("chip-active"));
    chip.classList.add("chip-active");
    const val = chip.dataset.value;
    if (val === "now") {
      state.hour = "now";
      const h = new Date().getHours();
      elHourSlider.value = h;
      updateSliderDisplay(h);
    } else {
      state.hour = parseInt(val, 10);
      elHourSlider.value = state.hour;
      updateSliderDisplay(state.hour);
    }
    maybeRoute();
    if (state.heatmapOn) loadHeatmap();
  });
});

elHourSlider.addEventListener("input", e => {
  const h = parseInt(e.target.value, 10);
  state.hour = h;
  updateSliderDisplay(h);
  document.querySelectorAll("#time-chips .chip").forEach(c => c.classList.remove("chip-active"));
  document.querySelectorAll("#time-chips .chip").forEach(c => {
    if (parseInt(c.dataset.value, 10) === h) c.classList.add("chip-active");
  });
  maybeRoute();
  if (state.heatmapOn) loadHeatmap();
});

// ── Heatmap ───────────────────────────────────────────────────────────────────

elToggleHeatmap.addEventListener("change", e => {
  state.heatmapOn = e.target.checked;
  if (state.heatmapOn) loadHeatmap();
  else clearHeatmap();
});

async function loadHeatmap() {
  clearHeatmap();
  try {
    const h    = state.hour;
    const res  = await fetch(`/api/segments?hour=${h}`);
    const data = await res.json();
    const group = L.layerGroup();

    data.segments.forEach(seg => {
      L.polyline(seg.coords, {
        color:       seg.color,
        weight:      3.5,
        opacity:     0.5,
        lineCap:     "round",
        interactive: false,
      }).addTo(group);
    });

    group.addTo(state.map);
    state.heatmapLayer = group;
  } catch (_) {
    showToast("Could not load safety heatmap.", "error");
  }
}

// ── Route computation ─────────────────────────────────────────────────────────

async function maybeRoute() {
  if (!state.startLatLon || !state.endLatLon) return;

  showLoading("Finding the safest path…");
  clearRoutes();

  const [sLat, sLon] = state.startLatLon;
  const [eLat, eLon] = state.endLatLon;
  const h = state.hour;

  try {
    const url = `/api/route?start_lat=${sLat}&start_lon=${sLon}&end_lat=${eLat}&end_lon=${eLon}&hour=${h}`;
    const res = await fetch(url);
    const data = await res.json();

    if (!res.ok) {
      showToast(data.error || "Could not compute route.", "error");
      return;
    }

    state.lastRoute = data;
    renderResults(data.shortest, data.safest);
    drawRoutes(data.shortest, data.safest);

    if (state.activeRouteTab === "shortest") {
      elPanelShortest.classList.remove("hidden");
      elPanelSafest.classList.add("hidden");
    } else {
      elPanelSafest.classList.remove("hidden");
      elPanelShortest.classList.add("hidden");
    }
  } catch (err) {
    showToast("Failed to fetch route. Check backend connection.", "error");
  } finally {
    hideLoading();
  }
}

// ── Route rendering ───────────────────────────────────────────────────────────

function drawRoutes(shortest, safest) {
  clearRoutes();
  const allCoords = [];

  // Shortest route (dashed)
  if (shortest && shortest.segments) {
    for (const seg of shortest.segments) {
      const pl = L.polyline(seg.coords, {
        color:     "#8B949E",
        weight:    3,
        dashArray: "5 7",
        opacity:   0.8,
      }).bindPopup(makePopup(seg, "Shortest")).addTo(state.map);
      state.routeLayers.push(pl);
      allCoords.push(...seg.coords);
    }
  }

  // Safest route (color-coded, thicker solid line)
  if (safest && safest.segments) {
    for (const seg of safest.segments) {
      const pl = L.polyline(seg.coords, {
        color:   seg.color,
        weight:  6,
        opacity: 0.95,
        lineCap: "round",
        lineJoin:"round",
      }).bindPopup(makePopup(seg, "Safest")).addTo(state.map);
      state.routeLayers.push(pl);
      allCoords.push(...seg.coords);
    }
  }

  if (allCoords.length) state.map.fitBounds(L.latLngBounds(allCoords), { padding: [40, 40] });
}

function makePopup(seg, routeName) {
  const emoji = seg.band === "safe" ? "✅" : seg.band === "caution" ? "⚠️" : "🚨";
  return `<div style="min-width:160px;font-family:Inter,sans-serif">
    <div style="font-weight:700;margin-bottom:6px;">${routeName} Route</div>
    <span class="popup-score popup-${seg.band}">${emoji} ${seg.band.toUpperCase()} — ${seg.score}/100</span>
    <div style="margin-top:6px;color:#8B949E;font-size:0.72rem;">
      ${seg.highway} · ${formatDist(seg.length_m)}
    </div>
  </div>`;
}

// ── Results panel ─────────────────────────────────────────────────────────────

function renderResults(shortest, safest) {
  elResultsCard.classList.remove("hidden");
  elVerdictText.textContent = safest.verdict;
  renderStatGrid(elStatsSafest,   safest);
  renderStatGrid(elStatsShortest, shortest);
  renderSegmentBar(elSegBreakdown, safest);
}

function renderStatGrid(el, route) {
  const cls = s => s >= 70 ? "score-safe" : s >= 40 ? "score-caution" : "score-avoid";
  el.innerHTML = `
    <div class="stat-item"><span class="stat-label">Distance</span><span class="stat-value accent">${formatDist(route.total_distance_m)}</span></div>
    <div class="stat-item"><span class="stat-label">Walk ETA</span><span class="stat-value">${route.eta_min} min</span></div>
    <div class="stat-item"><span class="stat-label">Avg Safety</span><span class="stat-value ${cls(route.avg_score)}">${route.avg_score}/100</span></div>
    <div class="stat-item"><span class="stat-label">Min Score</span><span class="stat-value ${cls(route.min_score)}">${route.min_score}/100</span></div>
    <div class="stat-item"><span class="stat-label">Segments</span><span class="stat-value">${route.segments.length}</span></div>
    <div class="stat-item"><span class="stat-label">Risky Segs</span><span class="stat-value ${route.avoid_count > 0 ? "score-avoid" : "score-safe"}">${route.avoid_count}</span></div>`;
}

function renderSegmentBar(el, route) {
  const segs    = route.segments;
  const safe    = segs.filter(s => s.band === "safe").length;
  const caution = segs.filter(s => s.band === "caution").length;
  const avoid   = segs.filter(s => s.band === "avoid").length;
  const total   = segs.length || 1;
  const pct     = n => (n / total * 100).toFixed(1);
  el.innerHTML = `
    <div class="seg-bar-label">Safest route segment breakdown</div>
    <div class="seg-bar-track">
      <div class="seg-bar-fill" style="background:#4CAF50;width:${pct(safe)}%"></div>
      <div class="seg-bar-fill" style="background:#FF9800;width:${pct(caution)}%"></div>
      <div class="seg-bar-fill" style="background:#F44336;width:${pct(avoid)}%"></div>
    </div>
    <div class="seg-bar-legend">
      <div class="seg-legend-item"><span class="seg-dot" style="background:#4CAF50"></span>${safe} Safe</div>
      <div class="seg-legend-item"><span class="seg-dot" style="background:#FF9800"></span>${caution} Caution</div>
      <div class="seg-legend-item"><span class="seg-dot" style="background:#F44336"></span>${avoid} Avoid</div>
    </div>`;
}

elTabSafest.addEventListener("click", () => {
  state.activeRouteTab = "safest";
  elTabSafest.classList.add("active");   elTabShortest.classList.remove("active");
  elPanelSafest.classList.remove("hidden"); elPanelShortest.classList.add("hidden");
});
elTabShortest.addEventListener("click", () => {
  state.activeRouteTab = "shortest";
  elTabShortest.classList.add("active"); elTabSafest.classList.remove("active");
  elPanelShortest.classList.remove("hidden"); elPanelSafest.classList.add("hidden");
});

// ══════════════════════════════════════════════════════════════════════════════
// Phase 2: Incident Reporting
// ══════════════════════════════════════════════════════════════════════════════

// Pick map location button
if (elBtnReportPick) {
  elBtnReportPick.addEventListener("click", () => {
    showToast("Click anywhere on the map to set incident location.", "info", 3000);
  });
}

// Incident type selector
document.querySelectorAll(".type-picker-grid .type-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".type-picker-grid .type-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.reportType = btn.dataset.type;
  });
});

// Time chips
document.querySelectorAll("#report-time-chips .chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#report-time-chips .chip").forEach(c => c.classList.remove("chip-active"));
    chip.classList.add("chip-active");
    state.reportTimeVal = chip.dataset.val;
    if (state.reportTimeVal === "custom") {
      elCustomTimeRow.classList.remove("hidden");
    } else {
      elCustomTimeRow.classList.add("hidden");
    }
  });
});

// Character count for note
if (elReportNote) {
  elReportNote.addEventListener("input", () => {
    const len = elReportNote.value.length;
    elReportCharCnt.textContent = `${len}/200`;
  });
}

function getReportIncidentTime() {
  const now = new Date();
  if (state.reportTimeVal === "now") {
    return now.toISOString().replace("T", " ").substring(0, 19);
  } else if (state.reportTimeVal === "1h") {
    const d = new Date(now.getTime() - 60 * 60 * 1000);
    return d.toISOString().replace("T", " ").substring(0, 19);
  } else if (state.reportTimeVal === "today") {
    const d = new Date(now.getTime() - 3 * 60 * 60 * 1000);
    return d.toISOString().replace("T", " ").substring(0, 19);
  } else if (state.reportTimeVal === "custom" && elCustomTimeInput.value) {
    return elCustomTimeInput.value.replace("T", " ") + ":00";
  }
  return now.toISOString().replace("T", " ").substring(0, 19);
}

// Submit Incident Report
if (elBtnSubmitReport) {
  elBtnSubmitReport.addEventListener("click", async () => {
    if (!state.reportLatLon) {
      showToast("Please click on the map to select where this occurred.", "error");
      return;
    }

    const payload = {
      lat: state.reportLatLon[0],
      lon: state.reportLatLon[1],
      type: state.reportType,
      incident_time: getReportIncidentTime(),
      note: elReportNote.value.trim(),
    };

    elBtnSubmitReport.disabled = true;
    elBtnSubmitReport.textContent = "Submitting…";

    try {
      const res = await fetch("/api/incidents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (res.status === 201) {
        // Success
        elDupConflictBox.classList.add("hidden");
        elReportSuccess.classList.remove("hidden");
        elReportSuccessMsg.textContent = "Submitted. Needs 2 more confirmations to go live.";
        elBtnSubmitReport.classList.add("hidden");
        showToast("Report submitted successfully!", "info", 3000);
      } else if (res.status === 409) {
        // Duplicate warning within 50m
        state.lastDuplicateId = data.duplicate_incident_id;
        elDupConflictBox.classList.remove("hidden");
        elDupConflictMsg.textContent = `${data.error} (${data.distance_m}m away)`;
        showToast("A similar report already exists nearby.", "error", 4000);
      } else {
        showToast(data.error || "Submission failed.", "error");
      }
    } catch (_) {
      showToast("Network error submitting report.", "error");
    } finally {
      elBtnSubmitReport.disabled = false;
      elBtnSubmitReport.textContent = "Submit Incident Report";
    }
  });
}

// Confirm duplicate report offer
if (elBtnConfirmDup) {
  elBtnConfirmDup.addEventListener("click", async () => {
    if (!state.lastDuplicateId) return;
    await voteIncident(state.lastDuplicateId);
    elDupConflictBox.classList.add("hidden");
  });
}

// Reset form to report another
if (elBtnReportAgain) {
  elBtnReportAgain.addEventListener("click", () => {
    state.reportLatLon = null;
    if (state.reportMarker) { state.reportMarker.remove(); state.reportMarker = null; }
    elReportLocName.textContent = "Click map to pick location";
    elReportLocCoords.textContent = "Lat/Lon will snap to nearest street";
    elReportNote.value = "";
    elReportCharCnt.textContent = "0/200";
    elReportSuccess.classList.add("hidden");
    elDupConflictBox.classList.add("hidden");
    elBtnSubmitReport.classList.remove("hidden");
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// Phase 2: Community Verification & Feed
// ══════════════════════════════════════════════════════════════════════════════

async function voteIncident(incidentId) {
  const voterCoords = state.reportLatLon || (state.meta ? [state.meta.center.lat, state.meta.center.lon] : [28.5494, 77.2001]);
  try {
    const res = await fetch(`/api/incidents/${incidentId}/vote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat: voterCoords[0],
        lon: voterCoords[1],
        simulated: true, // In DEMO_MODE, allows instant indoor verification
      }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || "Confirmation recorded!", "info", 3500);
      await fetchAndRenderPending();
      await fetchAndRenderVerifiedFeed();
      await loadVerifiedMapPins();
      // If a route is on screen, re-run to show changed scores!
      if (state.startLatLon && state.endLatLon) {
        maybeRoute();
      }
    } else {
      showToast(data.error || "Could not vote on incident.", "error");
    }
  } catch (_) {
    showToast("Network error submitting confirmation.", "error");
  }
}

// Render Pending Reports
async function fetchAndRenderPending() {
  try {
    const res = await fetch("/api/incidents/pending");
    const data = await res.json();
    const items = data.pending_incidents || [];

    // Clear old pending markers
    state.pendingMarkers.forEach(m => m.remove());
    state.pendingMarkers = [];

    if (items.length === 0) {
      elPendingList.innerHTML = `<div class="empty-state">No pending reports nearby right now.</div>`;
      return;
    }

    elPendingList.innerHTML = "";
    const typeEmojis = { harassment: "🗣️", poor_lighting: "💡", unsafe_area: "⚠️", other: "❓" };
    const typeNames  = { harassment: "Harassment", poor_lighting: "Poor Lighting", unsafe_area: "Unsafe Area", other: "Safety Risk" };

    items.forEach(item => {
      const card = document.createElement("div");
      card.className = "pending-card";

      const emoji = typeEmojis[item.type] || "⚠️";
      const name  = typeNames[item.type] || item.type;
      const confirmations = item.confirmations || 1;
      const needed = item.confirmations_needed || 3;
      const pct = Math.min(100, Math.round((confirmations / needed) * 100));

      card.innerHTML = `
        <div class="item-header">
          <span class="type-pill ${item.type}">${emoji} ${name}</span>
          <span class="item-time-dist">${timeAgo(item.reported_at)}</span>
        </div>
        <div class="item-street">📍 ${item.street_name || "Nearby street"}</div>
        ${item.note ? `<p class="item-note">"${item.note}"</p>` : ""}
        <div class="verify-footer">
          <div class="progress-wrap">
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
            <span class="progress-text">${confirmations}/${needed} confirmed</span>
          </div>
          <button class="btn-saw-too" data-id="${item.id}">I saw this too</button>
        </div>
      `;

      card.querySelector(".btn-saw-too").addEventListener("click", () => voteIncident(item.id));
      elPendingList.appendChild(card);

      // Create map marker for pending incident
      const marker = L.marker([item.lat, item.lon], {
        icon: L.divIcon({
          className: "incident-marker-pending",
          html: "⚠️",
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        })
      }).bindPopup(`
        <div class="raksha-popup-content">
          <div class="popup-title">⚠️ Pending Community Alert</div>
          <div class="popup-street">${name} · ${item.street_name || ""}</div>
          <div class="popup-time">${timeAgo(item.reported_at)}</div>
          <div class="popup-confirmations">${confirmations}/${needed} Community Confirmations</div>
          ${item.note ? `<div class="popup-note">"${item.note}"</div>` : ""}
        </div>
      `);

      if (state.currentTab === "verify") {
        marker.addTo(state.map);
      }
      state.pendingMarkers.push(marker);
    });
  } catch (_) {}
}

// Render Verified Feed
async function fetchAndRenderVerifiedFeed() {
  const activeHoursChip = document.querySelector("#feed-time-chips .chip-active");
  const hours = activeHoursChip ? activeHoursChip.dataset.hours : "24";
  const typeFilter = elFeedTypeSelect ? elFeedTypeSelect.value : "";

  let url = `/api/incidents?status=verified&hours=${hours}`;
  if (typeFilter) url += `&type=${typeFilter}`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    const items = data.incidents || [];

    if (items.length === 0) {
      elVerifiedFeed.innerHTML = `<div class="empty-state">No verified incidents in this time window.</div>`;
      return;
    }

    elVerifiedFeed.innerHTML = "";
    const typeEmojis = { harassment: "🗣️", poor_lighting: "💡", unsafe_area: "⚠️", other: "❓" };
    const typeNames  = { harassment: "Harassment", poor_lighting: "Poor Lighting", unsafe_area: "Unsafe Area", other: "Safety Risk" };

    items.forEach(item => {
      const card = document.createElement("div");
      card.className = "feed-item-card";

      const emoji = typeEmojis[item.type] || "🛡️";
      const name  = typeNames[item.type] || item.type;
      const confirmations = item.confirmations || item.upvotes || 3;

      card.innerHTML = `
        <div class="item-header">
          <span class="type-pill ${item.type}">${emoji} ${name}</span>
          <span class="item-time-dist">${timeAgo(item.reported_at)}</span>
        </div>
        <div class="item-street">📍 ${item.street_name || "Area road"}</div>
        ${item.note ? `<p class="item-note">"${item.note}"</p>` : ""}
        <div class="verify-footer">
          <span class="progress-text" style="color:#81C784">✓ Verified (${confirmations} confirmations)</span>
          <button class="btn-saw-too" data-id="${item.id}">I saw this too</button>
        </div>
      `;

      card.querySelector(".btn-saw-too").addEventListener("click", () => voteIncident(item.id));
      elVerifiedFeed.appendChild(card);
    });
  } catch (_) {}
}

// Feed Filter Events
document.querySelectorAll("#feed-time-chips .chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#feed-time-chips .chip").forEach(c => c.classList.remove("chip-active"));
    chip.classList.add("chip-active");
    fetchAndRenderVerifiedFeed();
  });
});

if (elFeedTypeSelect) {
  elFeedTypeSelect.addEventListener("change", () => {
    fetchAndRenderVerifiedFeed();
  });
}

// ── Verified Pins on Main Map ─────────────────────────────────────────────────

async function loadVerifiedMapPins() {
  try {
    const res = await fetch("/api/incidents?status=verified");
    const data = await res.json();
    const items = data.incidents || [];

    // Clear old pins
    state.verifiedMarkers.forEach(m => m.remove());
    state.verifiedMarkers = [];

    const typeEmojis = { harassment: "🗣️", poor_lighting: "💡", unsafe_area: "⚠️", other: "❓" };
    const typeNames  = { harassment: "Harassment Alert", poor_lighting: "Poor Lighting Alert", unsafe_area: "Unsafe Area Alert", other: "Safety Concern" };

    items.forEach(item => {
      const emoji = typeEmojis[item.type] || "⚠️";
      const name  = typeNames[item.type] || "Safety Alert";
      const confirmations = item.confirmations || item.upvotes || 3;

      const marker = L.marker([item.lat, item.lon], {
        icon: L.divIcon({
          className: `incident-marker-verified ${item.type}`,
          html: emoji,
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        })
      }).bindPopup(`
        <div class="raksha-popup-content">
          <div class="popup-title">${name}</div>
          <div class="popup-street">${item.street_name || "Local street"}</div>
          <div class="popup-time">${timeAgo(item.reported_at)}</div>
          <div class="popup-confirmations">✓ Community Verified (${confirmations} confirmations)</div>
          ${item.note ? `<div class="popup-note">"${item.note}"</div>` : ""}
        </div>
      `, { className: "raksha-popup" });

      marker.addTo(state.map);
      state.verifiedMarkers.push(marker);
    });
  } catch (_) {}
}

// ── SOS ───────────────────────────────────────────────────────────────────────

elSosBtn.addEventListener("click", async () => {
  showToast("SOS feature: Twilio / live siren simulated in pilot.", "info", 4000);
  try { await fetch("/api/sos", { method: "POST" }); } catch (_) {}
});

// ── Boot ──────────────────────────────────────────────────────────────────────

initMap();
