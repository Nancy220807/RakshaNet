/**
 * sos.js — Smart SOS Frontend Module (RakshaNet Phase 3 Milestone 6)
 *
 * Adheres strictly to frontend safety rules:
 * - All global names, variables, IDs and classes begin with "sos-" or "sos".
 * - All initialization and handlers wrapped in try/catch.
 * - Does not alter Phase 1/2 route planner, report tab or verify tab.
 * - Gracefully hides everything if SOS_ENABLED is false.
 */

(function () {
  "use strict";

  // ── Global Module State ───────────────────────────────────────────────────
  let sosConfig = null;
  let sosCurrentUser = null;
  let sosActiveAlert = null;
  let sosCountdownTimer = null;
  let sosCountdownRemaining = 5;
  let sosLocationStreamTimer = null;
  let sosRespondersPollTimer = null;
  let sosInboxPollTimer = null;
  let sosMiniMap = null;
  let sosUserMarker = null;
  let sosResponderMarkers = [];
  let sosCurrentLat = 28.5433;
  let sosCurrentLon = 77.2065;
  let sosWatchId = null;

  // ── Toast Helper (Non-intrusive) ──────────────────────────────────────────
  function sosShowToast(msg, type = "info") {
    try {
      // Use existing showToast if present in window/app.js
      if (typeof window.showToast === "function") {
        window.showToast(msg, type);
        return;
      }
      const existing = document.getElementById("sos-toast-msg");
      if (existing) existing.remove();

      const toast = document.createElement("div");
      toast.id = "sos-toast-msg";
      toast.style.cssText = `
        position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
        background: #1e1f2b; color: #ffffff; border: 1px solid ${type === "error" ? "#ef4444" : "#E91E8C"};
        padding: 12px 20px; border-radius: 10px; z-index: 99999;
        font-family: inherit; font-size: 0.9rem; font-weight: 600;
        box-shadow: 0 8px 24px rgba(0,0,0,0.6);
      `;
      toast.textContent = msg;
      document.body.appendChild(toast);
      setTimeout(() => { toast.remove(); }, 4000);
    } catch (_) {}
  }

  // ── Initialization & Config Check ─────────────────────────────────────────
  async function sosInit() {
    try {
      const resp = await fetch("/api/sos/config");
      if (!resp.ok) {
        sosDisableUI();
        return;
      }
      sosConfig = await resp.json();
      if (!sosConfig.sos_enabled) {
        sosDisableUI();
        return;
      }

      await sosFetchCurrentUser();
      sosWireFloatingButton();
      sosWireSafetyButton();

      if (sosConfig.demo_mode) {
        sosWireDemoSwitcher();
        sosInitResponderInbox();
      }

      // Check if user has an existing active alert to restore
      await sosCheckAndRestoreActiveAlert();
    } catch (err) {
      console.warn("[Smart SOS] Feature disabled or init failed:", err);
      sosDisableUI();
    }
  }

  function sosDisableUI() {
    try {
      const safetyBtn = document.getElementById("sos-btn-safety");
      if (safetyBtn) safetyBtn.classList.add("sos-hidden");
      const inboxRoot = document.getElementById("sos-inbox-root");
      if (inboxRoot) inboxRoot.innerHTML = "";
      const overlayRoot = document.getElementById("sos-overlay-root");
      if (overlayRoot) overlayRoot.innerHTML = "";
    } catch (_) {}
  }

  async function sosFetchCurrentUser() {
    try {
      const res = await fetch("/api/auth/me");
      if (res.ok) {
        const data = await res.json();
        sosCurrentUser = data.user;
      } else {
        sosCurrentUser = null;
      }
    } catch (_) {
      sosCurrentUser = null;
    }
  }

  // ── Floating Button Click Interception ────────────────────────────────────
  function sosWireFloatingButton() {
    const sosBtn = document.getElementById("sos-btn");
    if (!sosBtn) return;

    // Capture phase event listener to intercept click before old demo toast runs
    sosBtn.addEventListener("click", async (e) => {
      try {
        e.stopImmediatePropagation();
        e.preventDefault();

        await sosFetchCurrentUser();
        if (!sosCurrentUser) {
          // Open existing auth modal
          const authBackdrop = document.getElementById("auth-modal-backdrop");
          if (authBackdrop) authBackdrop.classList.remove("hidden");
          sosShowToast("Please log in to trigger an SOS alert.", "error");
          return;
        }

        sosStartCountdownFlow();
      } catch (err) {
        console.error("[Smart SOS] Button error:", err);
        sosShowToast("Failed to open SOS countdown.", "error");
      }
    }, true);
  }

  // ── Countdown Overlay Flow ────────────────────────────────────────────────
  function sosStartCountdownFlow() {
    sosCleanupTimers();
    const overlayRoot = document.getElementById("sos-overlay-root");
    if (!overlayRoot) return;

    sosCountdownRemaining = (sosConfig && sosConfig.countdown_seconds) || 5;
    const isDemo = Boolean(sosConfig && sosConfig.demo_mode);

    overlayRoot.innerHTML = `
      <div class="sos-overlay-backdrop" id="sos-countdown-backdrop">
        <div class="sos-countdown-card">
          <div class="sos-countdown-title">EMERGENCY SOS</div>
          <div class="sos-countdown-desc">Dispatching alert to your trusted contacts & nearby responders in:</div>

          <div class="sos-countdown-number" id="sos-countdown-num">${sosCountdownRemaining}</div>

          <div class="sos-countdown-bar-wrap">
            <div class="sos-countdown-bar-fill" id="sos-countdown-bar" style="width: 100%;"></div>
          </div>

          ${isDemo ? `
          <div class="sos-loc-choice-box">
            <span style="font-weight: 700; color: #a5b4fc; font-size: 0.75rem; text-transform: uppercase;">Demo Mode Location</span>
            <label>
              <input type="radio" name="sos-loc-opt" value="simulated" checked />
              <span>Simulated: Hauz Khas Village (28.5433, 77.2065)</span>
            </label>
            <label>
              <input type="radio" name="sos-loc-opt" value="real" />
              <span>My Real Location (Browser GPS)</span>
            </label>
          </div>
          ` : ""}

          <button class="sos-cancel-btn" id="sos-btn-cancel-countdown">Cancel SOS</button>
        </div>
      </div>
    `;

    const cancelBtn = document.getElementById("sos-btn-cancel-countdown");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => {
        sosCleanupTimers();
        overlayRoot.innerHTML = "";
        sosShowToast("SOS alert cancelled.", "info");
      });
    }

    const totalSeconds = sosCountdownRemaining;
    const barEl = document.getElementById("sos-countdown-bar");
    const numEl = document.getElementById("sos-countdown-num");

    sosCountdownTimer = setInterval(async () => {
      sosCountdownRemaining--;
      if (numEl) numEl.textContent = sosCountdownRemaining;
      if (barEl) {
        const pct = Math.max(0, (sosCountdownRemaining / totalSeconds) * 100);
        barEl.style.width = pct + "%";
      }

      if (sosCountdownRemaining <= 0) {
        clearInterval(sosCountdownTimer);
        sosCountdownTimer = null;
        await sosTriggerStart();
      }
    }, 1000);
  }

  // ── Trigger SOS Start API ─────────────────────────────────────────────────
  async function sosTriggerStart() {
    const overlayRoot = document.getElementById("sos-overlay-root");
    let isSimulated = false;

    // Check location preference
    const locRadio = document.querySelector('input[name="sos-loc-opt"]:checked');
    if (locRadio && locRadio.value === "simulated") {
      isSimulated = true;
      sosCurrentLat = 28.5433;
      sosCurrentLon = 77.2065;
    } else {
      // Attempt real geolocation
      if (navigator.geolocation) {
        try {
          const pos = await new Promise((res, rej) => {
            navigator.geolocation.getCurrentPosition(res, rej, { timeout: 4000 });
          });
          sosCurrentLat = pos.coords.latitude;
          sosCurrentLon = pos.coords.longitude;
        } catch (_) {
          // If in demo mode and GPS fails, fallback to simulated point
          if (sosConfig && sosConfig.demo_mode) {
            isSimulated = true;
            sosCurrentLat = 28.5433;
            sosCurrentLon = 77.2065;
          } else {
            sosShowToast("Location permission denied. Cannot start SOS.", "error");
            if (overlayRoot) overlayRoot.innerHTML = "";
            return;
          }
        }
      } else if (sosConfig && sosConfig.demo_mode) {
        isSimulated = true;
        sosCurrentLat = 28.5433;
        sosCurrentLon = 77.2065;
      }
    }

    try {
      const resp = await fetch("/api/sos/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: sosCurrentLat,
          lon: sosCurrentLon,
          simulated: isSimulated,
          countdown: false,
        }),
      });

      const data = await resp.json();

      if (!resp.ok) {
        if (overlayRoot) overlayRoot.innerHTML = "";
        if (resp.status === 400 && data.error && data.error.includes("No trusted contacts")) {
          sosShowToast("Please add trusted contacts before triggering SOS.", "error");
          sosOpenSafetyModal();
        } else {
          sosShowToast(data.error || "Failed to trigger SOS alert.", "error");
        }
        return;
      }

      // Success: render Active Panel
      sosActiveAlert = data.alert;
      sosRenderActivePanel(data);
      sosStartLocationStreaming(data.alert.id, isSimulated);
      sosStartRespondersPolling(data.alert.id);
    } catch (err) {
      console.error("[Smart SOS] Start call error:", err);
      if (overlayRoot) overlayRoot.innerHTML = "";
      sosShowToast("Network error triggering SOS.", "error");
    }
  }

  // ── Active SOS Panel ──────────────────────────────────────────────────────
  function sosRenderActivePanel(startData) {
    const overlayRoot = document.getElementById("sos-overlay-root");
    if (!overlayRoot) return;

    const alert = (startData && startData.alert) || sosActiveAlert;
    const notifications = (startData && startData.notifications) || [];
    const streetName = alert.street_name || "Emergency Location";
    const trackToken = alert.track_token;
    const trackUrl = `${window.location.origin}/track/${trackToken}`;

    const hasFailedSMS = notifications.some(n => n.status === "failed");

    overlayRoot.innerHTML = `
      <div class="sos-overlay-backdrop" id="sos-active-backdrop">
        <div class="sos-active-panel">
          <!-- Header -->
          <div class="sos-panel-header">
            <div>
              <div class="sos-alert-badge">
                <span class="sos-pulse-circle"></span>
                <span>EMERGENCY ALERT SENT</span>
              </div>
              <div style="font-size: 1.1rem; font-weight: 800; margin-top: 6px; color: #fff;">Near ${streetName}</div>
              ${sosConfig && sosConfig.demo_mode ? `
              <div style="display:flex; align-items:center; gap:6px; margin-top:8px;">
                <label for="sos-panel-user-select" style="font-size:0.75rem; color:var(--sos-text-muted);">Acting as:</label>
                <select id="sos-panel-user-select" class="sos-input-field" style="width:auto; padding:3px 8px; font-size:0.78rem;">
                  <option value="user_asha" ${sosCurrentUser && sosCurrentUser.id === 'user_asha' ? 'selected' : ''}>Asha</option>
                  <option value="user_riya" ${sosCurrentUser && sosCurrentUser.id === 'user_riya' ? 'selected' : ''}>Riya</option>
                  <option value="user_meera" ${sosCurrentUser && sosCurrentUser.id === 'user_meera' ? 'selected' : ''}>Meera</option>
                  <option value="user_kabir" ${sosCurrentUser && sosCurrentUser.id === 'user_kabir' ? 'selected' : ''}>Kabir</option>
                  <option value="user_zoya" ${sosCurrentUser && sosCurrentUser.id === 'user_zoya' ? 'selected' : ''}>Zoya</option>
                </select>
              </div>
              ` : ""}
            </div>
            <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
              <button class="sos-copy-link-btn" id="sos-btn-copy-track" title="Copy public tracking link">
                🔗 Copy Live Link
              </button>
              <button id="sos-btn-minimize" class="sos-btn-secondary" style="padding:4px 10px; font-size:0.75rem;">🗕 Minimize</button>
            </div>
          </div>

          <!-- Prominent Call 112 Button -->
          <a href="tel:112" class="sos-call-112-btn" id="sos-btn-call-112">
            <span>🚨</span>
            <span>Call Emergency 112</span>
          </a>

          <!-- SMS Failed Warning if needed -->
          ${hasFailedSMS ? `
          <div class="sos-sms-fail-warning">
            ⚠️ Some SMS notifications could not be sent. Please use the "Send via WhatsApp" buttons below.
          </div>
          ` : ""}

          <!-- Notified Contacts Section -->
          <div class="sos-section-card">
            <div class="sos-section-title">
              <span>Trusted Contacts Notified</span>
              <span style="font-size: 0.72rem; color: #a5b4fc;">${notifications.length} contact(s)</span>
            </div>
            <div class="sos-contact-list" id="sos-contacts-notified-list">
              ${notifications.map(n => `
                <div class="sos-contact-item">
                  <div class="sos-contact-info">
                    <span class="sos-contact-name">${n.recipient_name || "Contact"}</span>
                    <span class="sos-contact-meta">${n.phone || ""} · <span class="sos-status-tag sos-tag-${n.status}">${n.status}</span></span>
                  </div>
                  ${n.wa_link ? `
                    <a href="${n.wa_link}" target="_blank" rel="noopener" class="sos-wa-btn">
                      💬 Send WhatsApp
                    </a>
                  ` : ""}
                </div>
              `).join("")}
            </div>
          </div>

          <!-- Community Responders Section -->
          <div class="sos-section-card">
            <div class="sos-section-title">
              <span>Community Responders Status</span>
              <span id="sos-responders-count" style="font-size: 0.72rem; color: #a5b4fc;">Searching nearby...</span>
            </div>
            <div class="sos-responders-list" id="sos-responders-list">
              <div style="font-size: 0.8rem; color: var(--sos-text-muted);">Finding opted-in community volunteers within 800m...</div>
            </div>
          </div>

          <!-- Mini Map of Current Position -->
          <div class="sos-section-card" style="padding: 10px;">
            <div class="sos-mini-map-wrap">
              <div class="sos-sharing-indicator">
                <span class="sos-pulse-circle" style="width: 6px; height: 6px;"></span>
                <span>Sharing live location</span>
              </div>
              <div id="sos-mini-map"></div>
            </div>
          </div>

          <!-- Resolve Buttons -->
          <div class="sos-resolve-row">
            <button class="sos-btn-safe" id="sos-btn-resolve-safe">✓ I'm Safe</button>
            <button class="sos-btn-false-alarm" id="sos-btn-resolve-false">✕ False Alarm</button>
          </div>
        </div>
      </div>
    `;

    // Wire Copy Link
    const copyBtn = document.getElementById("sos-btn-copy-track");
    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(trackUrl);
          copyBtn.textContent = "✓ Link Copied!";
          setTimeout(() => { copyBtn.textContent = "🔗 Copy Live Link"; }, 2500);
        } else {
          sosShowToast(`Tracking link: ${trackUrl}`, "info");
        }
      });
    }

    // Wire Panel User Select (Demo Mode)
    const panelUserSelect = document.getElementById("sos-panel-user-select");
    if (panelUserSelect) {
      panelUserSelect.addEventListener("change", async () => {
        const newUid = panelUserSelect.value;
        try {
          await fetch("/api/auth/switch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: newUid }),
          });
          const mainSel = document.getElementById("demo-user-select");
          if (mainSel) mainSel.value = newUid;
          sosCleanupTimers();
          sosActiveAlert = null;
          overlayRoot.innerHTML = "";
          await sosFetchCurrentUser();
          if (sosConfig && sosConfig.demo_mode) {
            sosStartInboxPolling();
          }
          await sosCheckAndRestoreActiveAlert();
        } catch (_) {}
      });
    }

    // Wire Minimize Button
    const minBtn = document.getElementById("sos-btn-minimize");
    if (minBtn) {
      minBtn.addEventListener("click", () => {
        overlayRoot.innerHTML = `
          <div id="sos-minimized-badge" style="position:fixed; top:16px; right:16px; z-index:10001; background:#181a26; border:2px solid #E91E8C; border-radius:12px; padding:10px 16px; display:flex; align-items:center; gap:10px; box-shadow:0 6px 24px rgba(0,0,0,0.7); cursor:pointer;">
            <span class="sos-pulse-circle"></span>
            <span style="font-weight:700; color:#fff; font-size:0.85rem;">Active SOS (Near ${streetName})</span>
            <span style="color:#E91E8C; font-weight:800; font-size:0.85rem;">Expand ↗</span>
          </div>
        `;
        const badge = document.getElementById("sos-minimized-badge");
        if (badge) {
          badge.addEventListener("click", () => {
            sosRenderActivePanel(startData);
          });
        }
      });
    }

    // Wire Safe & False Alarm
    const safeBtn = document.getElementById("sos-btn-resolve-safe");
    if (safeBtn) {
      safeBtn.addEventListener("click", () => sosResolveActiveAlert("safe"));
    }
    const falseBtn = document.getElementById("sos-btn-resolve-false");
    if (falseBtn) {
      falseBtn.addEventListener("click", () => sosResolveActiveAlert("false_alarm"));
    }

    // Init Leaflet Mini Map
    setTimeout(() => {
      sosInitMiniMap(alert.last_lat || alert.start_lat || 28.5433, alert.last_lon || alert.start_lon || 77.2065);
    }, 100);
  }

  function sosInitMiniMap(lat, lon) {
    try {
      const mapDiv = document.getElementById("sos-mini-map");
      if (!mapDiv || typeof L === "undefined") return;

      if (sosMiniMap) {
        sosMiniMap.remove();
        sosMiniMap = null;
      }

      sosMiniMap = L.map("sos-mini-map", {
        center: [lat, lon],
        zoom: 16,
        zoomControl: false,
        attributionControl: false,
      });

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
      }).addTo(sosMiniMap);

      // User location marker
      const userIcon = L.divIcon({
        className: "sos-user-pos-marker",
        html: '<div style="width:16px;height:16px;background:#E91E8C;border:2px solid white;border-radius:50%;box-shadow:0 0 10px #E91E8C;"></div>',
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      sosUserMarker = L.marker([lat, lon], { icon: userIcon }).addTo(sosMiniMap);
    } catch (e) {
      console.warn("[Smart SOS] Mini map init error:", e);
    }
  }

  // ── Responders Polling ────────────────────────────────────────────────────
  function sosStartRespondersPolling(alertId) {
    if (sosRespondersPollTimer) clearInterval(sosRespondersPollTimer);

    async function poll() {
      try {
        const res = await fetch(`/api/sos/${alertId}/responders`);
        if (!res.ok) return;
        const data = await res.json();
        sosRenderRespondersList(data.responders || []);
      } catch (_) {}
    }

    poll();
    sosRespondersPollTimer = setInterval(poll, 3500);
  }

  function sosRenderRespondersList(responders) {
    const listEl = document.getElementById("sos-responders-list");
    const countEl = document.getElementById("sos-responders-count");
    if (!listEl) return;

    if (!responders || responders.length === 0) {
      listEl.innerHTML = `<div style="font-size: 0.8rem; color: var(--sos-text-muted);">No community responders notified yet.</div>`;
      if (countEl) countEl.textContent = "0 notified";
      return;
    }

    const onWayCount = responders.filter(r => r.status === "on_my_way").length;
    if (countEl) {
      countEl.textContent = `${responders.length} notified (${onWayCount} on way)`;
    }

    listEl.innerHTML = responders.map(r => {
      let statusHtml = "";
      if (r.status === "on_my_way") {
        statusHtml = `<span class="sos-responder-status sos-status-on-way">✓ On the way (${r.approx_distance_m || 200}m away)</span>`;
      } else if (r.status === "cant_help" || r.status === "cannot_help") {
        statusHtml = `<span class="sos-responder-status sos-status-declined">Cannot assist</span>`;
      } else {
        statusHtml = `<span class="sos-responder-status sos-status-notified">Notified (${r.approx_distance_m || 400}m away)</span>`;
      }

      return `
        <div class="sos-responder-item">
          <span style="font-weight: 700;">${r.first_name}</span>
          ${statusHtml}
        </div>
      `;
    }).join("");

    // Draw responder pins on map for accepted ("on_my_way") responders
    sosUpdateResponderMapPins(responders);
  }

  function sosUpdateResponderMapPins(responders) {
    if (!sosMiniMap || typeof L === "undefined") return;

    // Clear old responder pins
    sosResponderMarkers.forEach(m => m.remove());
    sosResponderMarkers = [];

    const accepted = responders.filter(r => r.status === "on_my_way" && r.lat && r.lon);
    accepted.forEach(r => {
      const pinIcon = L.divIcon({
        className: "sos-resp-pin",
        html: `<div class="sos-responder-pin" title="${r.first_name} (Responder)">🛡️</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      const marker = L.marker([r.lat, r.lon], { icon: pinIcon }).addTo(sosMiniMap);
      marker.bindPopup(`<b>${r.first_name}</b> is on the way!`);
      sosResponderMarkers.push(marker);
    });
  }

  // ── Location Streaming ────────────────────────────────────────────────────
  function sosStartLocationStreaming(alertId, isSimulated) {
    if (sosLocationStreamTimer) clearInterval(sosLocationStreamTimer);

    const intervalSec = (sosConfig && sosConfig.location_update_seconds) || 5;

    sosLocationStreamTimer = setInterval(async () => {
      if (!isSimulated && navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
          pos => {
            sosCurrentLat = pos.coords.latitude;
            sosCurrentLon = pos.coords.longitude;
            sosPostLocation(alertId, sosCurrentLat, sosCurrentLon);
          },
          () => {}
        );
      } else {
        // Small simulated drift for demo realism
        sosCurrentLat += (Math.random() - 0.5) * 0.0001;
        sosCurrentLon += (Math.random() - 0.5) * 0.0001;
        sosPostLocation(alertId, sosCurrentLat, sosCurrentLon);
      }
    }, intervalSec * 1000);
  }

  async function sosPostLocation(alertId, lat, lon) {
    try {
      if (sosUserMarker) {
        sosUserMarker.setLatLng([lat, lon]);
      }
      await fetch(`/api/sos/${alertId}/location`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat, lon }),
      });
    } catch (_) {}
  }

  // ── Resolve Alert ─────────────────────────────────────────────────────────
  async function sosResolveActiveAlert(outcome) {
    if (!sosActiveAlert) return;
    const alertId = sosActiveAlert.id;

    try {
      const res = await fetch(`/api/sos/${alertId}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outcome }),
      });

      if (res.ok) {
        sosCleanupTimers();
        const overlayRoot = document.getElementById("sos-overlay-root");
        if (overlayRoot) overlayRoot.innerHTML = "";
        sosActiveAlert = null;
        sosShowToast(
          outcome === "safe"
            ? "Alert resolved. Your contacts have been notified that you are safe."
            : "Alert marked as false alarm.",
          "info"
        );
      } else {
        const data = await res.json();
        sosShowToast(data.error || "Failed to resolve alert.", "error");
      }
    } catch (_) {
      sosShowToast("Error communicating with server.", "error");
    }
  }

  // ── Safety Settings Modal (Contacts & Responders) ─────────────────────────
  function sosWireSafetyButton() {
    const safetyBtn = document.getElementById("sos-btn-safety");
    if (safetyBtn) {
      safetyBtn.addEventListener("click", () => {
        sosOpenSafetyModal();
      });
    }
  }

  async function sosOpenSafetyModal() {
    await sosFetchCurrentUser();
    const modalRoot = document.getElementById("sos-safety-modal-root");
    if (!modalRoot) return;

    modalRoot.innerHTML = `
      <div class="sos-overlay-backdrop" id="sos-safety-backdrop">
        <div class="sos-modal-dialog">
          <button class="sos-modal-close" id="sos-btn-close-safety">✕</button>
          <div class="sos-modal-title">🛡️ Safety Settings</div>

          <!-- Contacts Section -->
          <div class="sos-contacts-box">
            <div style="font-weight: 700; font-size: 0.95rem; color: #fff;">Trusted SOS Contacts (Max 3)</div>
            <div id="sos-contacts-container" style="display:flex; flex-direction:column; gap:8px;">
              <div style="color:var(--sos-text-muted); font-size:0.8rem;">Loading contacts...</div>
            </div>

            <!-- Add Contact Form -->
            <form id="sos-form-add-contact" class="sos-add-contact-form">
              <span style="font-size:0.8rem; font-weight:700; color:#a5b4fc;">Add New Contact</span>
              <input type="text" id="sos-input-contact-name" class="sos-input-field" placeholder="Full Name (e.g. Riya)" required />
              <input type="tel" id="sos-input-contact-phone" class="sos-input-field" placeholder="10-digit Phone Number" required pattern="[0-9]{10}" />
              <select id="sos-input-contact-relation" class="sos-input-field">
                <option value="Sister">Sister</option>
                <option value="Mother">Mother</option>
                <option value="Father">Father</option>
                <option value="Brother">Brother</option>
                <option value="Friend">Friend</option>
                <option value="Partner">Partner</option>
                <option value="Other">Other</option>
              </select>
              <button type="submit" class="sos-btn-secondary" id="sos-btn-submit-contact">+ Save Contact</button>
            </form>
          </div>

          <!-- Community Responder Toggle -->
          <div class="sos-contacts-box" style="border-top: 1px solid var(--sos-border); padding-top: 14px;">
            <div style="font-weight: 700; font-size: 0.95rem; color: #fff;">Community Responder Network</div>
            <div class="sos-disclaimer-box">
              ⚠️ Responders are volunteers, not police. In an emergency, call 112.
            </div>
            <label style="display:flex; align-items:center; gap:10px; cursor:pointer; font-size:0.88rem; margin-top:6px;">
              <input type="checkbox" id="sos-cb-responder-optin" style="width:18px; height:18px; accent-color:var(--sos-accent);" />
              <span>Volunteer to receive nearby emergency SOS alerts</span>
            </label>
            <div id="sos-responder-status-text" style="font-size:0.75rem; color:var(--sos-text-muted);"></div>
          </div>
        </div>
      </div>
    `;

    // Close Button & Backdrop
    const closeBtn = document.getElementById("sos-btn-close-safety");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => { modalRoot.innerHTML = ""; });
    }
    const backdrop = document.getElementById("sos-safety-backdrop");
    if (backdrop) {
      backdrop.addEventListener("click", (e) => {
        if (e.target === backdrop) modalRoot.innerHTML = "";
      });
    }

    // Load and render contacts
    await sosLoadContacts();

    // Wire Add Contact Form
    const addForm = document.getElementById("sos-form-add-contact");
    if (addForm) {
      addForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = document.getElementById("sos-input-contact-name").value.trim();
        const phone = document.getElementById("sos-input-contact-phone").value.trim();
        const relation = document.getElementById("sos-input-contact-relation").value;

        try {
          const resp = await fetch("/api/contacts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, phone, relation }),
          });
          const resData = await resp.json();
          if (resp.ok) {
            sosShowToast("Contact added successfully!", "info");
            addForm.reset();
            await sosLoadContacts();
          } else {
            sosShowToast(resData.error || "Failed to add contact.", "error");
          }
        } catch (_) {
          sosShowToast("Network error saving contact.", "error");
        }
      });
    }

    // Load & wire responder opt-in
    const optinCb = document.getElementById("sos-cb-responder-optin");
    const statusText = document.getElementById("sos-responder-status-text");

    if (optinCb && sosCurrentUser) {
      optinCb.checked = Boolean(sosCurrentUser.is_responder);
      if (statusText) {
        statusText.textContent = sosCurrentUser.is_responder
          ? "Status: Active Community Volunteer"
          : "Status: Not opted in";
      }

      optinCb.addEventListener("change", async () => {
        try {
          const resp = await fetch("/api/responders/optin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
          const resData = await resp.json();
          if (resp.ok) {
            optinCb.checked = Boolean(resData.is_responder);
            if (statusText) {
              statusText.textContent = resData.is_responder
                ? "Status: Active Community Volunteer"
                : "Status: Not opted in";
            }
            sosShowToast(
              resData.is_responder
                ? "Enrolled as a Community Responder. Thank you!"
                : "Opted out of responder network.",
              "info"
            );
          } else {
            sosShowToast(resData.error || "Failed to update responder status.", "error");
            optinCb.checked = !optinCb.checked;
          }
        } catch (_) {
          sosShowToast("Network error updating responder setting.", "error");
        }
      });
    }
  }

  async function sosLoadContacts() {
    const container = document.getElementById("sos-contacts-container");
    if (!container) return;

    try {
      const resp = await fetch("/api/contacts");
      if (!resp.ok) {
        container.innerHTML = `<div style="color:var(--sos-text-muted); font-size:0.8rem;">Log in to manage contacts.</div>`;
        return;
      }
      const data = await resp.json();
      const contacts = data.contacts || [];

      if (contacts.length === 0) {
        container.innerHTML = `<div style="color:var(--sos-text-muted); font-size:0.8rem;">No trusted contacts added yet. Add at least one below.</div>`;
        return;
      }

      container.innerHTML = contacts.map(c => `
        <div class="sos-contact-row">
          <div>
            <span style="font-weight:700; color:#fff;">${c.name}</span>
            <span style="font-size:0.75rem; color:var(--sos-text-muted); margin-left:6px;">(${c.relation}) · ${c.phone}</span>
          </div>
          <button class="sos-contact-del-btn" data-id="${c.id}" title="Remove contact">🗑️</button>
        </div>
      `).join("");

      // Wire delete buttons
      container.querySelectorAll(".sos-contact-del-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          const cid = btn.getAttribute("data-id");
          try {
            const dResp = await fetch(`/api/contacts/${cid}`, { method: "DELETE" });
            if (dResp.ok) {
              sosShowToast("Contact removed.", "info");
              await sosLoadContacts();
            } else {
              sosShowToast("Failed to remove contact.", "error");
            }
          } catch (_) {
            sosShowToast("Error removing contact.", "error");
          }
        });
      });
    } catch (_) {
      container.innerHTML = `<div style="color:var(--sos-danger); font-size:0.8rem;">Failed to load contacts.</div>`;
    }
  }

  // ── DEMO_MODE Responder Inbox ─────────────────────────────────────────────
  function sosInitResponderInbox() {
    const inboxRoot = document.getElementById("sos-inbox-root");
    if (!inboxRoot) return;

    inboxRoot.innerHTML = `
      <div class="sos-inbox-container" id="sos-inbox-container">
        <button class="sos-inbox-toggle-btn" id="sos-btn-inbox-toggle">
          <span>🛡️ Responder Inbox</span>
          <span class="sos-inbox-count-badge sos-hidden" id="sos-inbox-count">0</span>
        </button>
        <div class="sos-inbox-popup sos-hidden" id="sos-inbox-popup">
          <div style="font-size:0.85rem; font-weight:800; border-bottom:1px solid var(--sos-border); padding-bottom:8px;">
            Incoming Community Alerts
          </div>
          <div id="sos-inbox-items" style="display:flex; flex-direction:column; gap:8px;">
            <div style="font-size:0.78rem; color:var(--sos-text-muted);">No active emergency alerts nearby.</div>
          </div>
        </div>
      </div>
    `;

    const toggleBtn = document.getElementById("sos-btn-inbox-toggle");
    const popup = document.getElementById("sos-inbox-popup");
    if (toggleBtn && popup) {
      toggleBtn.addEventListener("click", () => {
        popup.classList.toggle("sos-hidden");
      });
    }

    sosStartInboxPolling();
  }

  function sosStartInboxPolling() {
    if (sosInboxPollTimer) clearInterval(sosInboxPollTimer);

    async function pollInbox() {
      try {
        const resp = await fetch("/api/notifications");
        if (!resp.ok) return;
        const data = await resp.json();
        sosRenderInboxItems(data.notifications || []);
      } catch (_) {}
    }

    pollInbox();
    sosInboxPollTimer = setInterval(pollInbox, 4000);
  }

  function sosRenderInboxItems(notifications) {
    const itemsEl = document.getElementById("sos-inbox-items");
    const countEl = document.getElementById("sos-inbox-count");
    if (!itemsEl) return;

    const unread = notifications.filter(n => !n.read || (n.payload && n.payload.status === "notified"));
    if (countEl) {
      if (unread.length > 0) {
        countEl.textContent = unread.length;
        countEl.classList.remove("sos-hidden");
      } else {
        countEl.classList.add("sos-hidden");
      }
    }

    if (!notifications || notifications.length === 0) {
      itemsEl.innerHTML = `<div style="font-size:0.78rem; color:var(--sos-text-muted);">No active emergency alerts nearby.</div>`;
      return;
    }

    itemsEl.innerHTML = notifications.map(n => {
      const p = n.payload || {};
      const street = p.street_name || "Nearby location";
      const dist = p.approx_distance_m ? `${p.approx_distance_m}m away` : "Nearby";
      const status = p.status || "notified";

      return `
        <div class="sos-inbox-card" id="sos-notif-${n.id}">
          <div style="font-weight:700; color:#fff;">🚨 SOS near ${street}</div>
          <div style="font-size:0.75rem; color:var(--sos-text-muted);">Distance: ${dist}</div>
          ${status === "on_my_way" ? `
            <div style="color:var(--sos-success); font-weight:700; font-size:0.78rem;">✓ You are on the way!</div>
            ${p.maps_link ? `<a href="${p.maps_link}" target="_blank" style="color:#60a5fa; font-size:0.75rem;">View Exact Location on Google Maps ↗</a>` : ""}
          ` : status === "cannot_help" ? `
            <div style="color:var(--sos-text-muted); font-size:0.75rem;">You declined this request.</div>
          ` : `
            <div class="sos-inbox-actions">
              <button class="sos-btn-respond-accept" data-id="${n.id}">On my way</button>
              <button class="sos-btn-respond-decline" data-id="${n.id}">Can't help</button>
            </div>
          `}
        </div>
      `;
    }).join("");

    // Wire Accept & Decline buttons
    itemsEl.querySelectorAll(".sos-btn-respond-accept").forEach(btn => {
      btn.addEventListener("click", async () => {
        const nid = btn.getAttribute("data-id");
        await sosRespondToNotification(nid, "on_my_way");
      });
    });

    itemsEl.querySelectorAll(".sos-btn-respond-decline").forEach(btn => {
      btn.addEventListener("click", async () => {
        const nid = btn.getAttribute("data-id");
        await sosRespondToNotification(nid, "cannot_help");
      });
    });
  }

  async function sosRespondToNotification(notifId, action) {
    try {
      const resp = await fetch(`/api/notifications/${notifId}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (resp.ok) {
        sosShowToast(
          action === "on_my_way" ? "Thank you! Alert owner has been notified." : "Response recorded.",
          "info"
        );
        // Refresh notifications immediately
        const nResp = await fetch("/api/notifications");
        if (nResp.ok) {
          const data = await nResp.json();
          sosRenderInboxItems(data.notifications || []);
        }
      } else {
        sosShowToast("Failed to record response.", "error");
      }
    } catch (_) {
      sosShowToast("Error responding to alert.", "error");
    }
  }

  // ── Restore Active Alert on Page Reload ───────────────────────────────────
  async function sosCheckAndRestoreActiveAlert() {
    try {
      const resp = await fetch("/api/sos/active");
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.active && data.alert) {
        sosActiveAlert = data.alert;
        sosRenderActivePanel({ alert: data.alert, notifications: [] });
        sosStartLocationStreaming(data.alert.id, true);
        sosStartRespondersPolling(data.alert.id);
      }
    } catch (_) {}
  }

  // ── Demo Switcher Change Listener ─────────────────────────────────────────
  function sosWireDemoSwitcher() {
    const demoSelect = document.getElementById("demo-user-select");
    if (!demoSelect) return;

    demoSelect.addEventListener("change", async () => {
      try {
        // Clear all timers and previous active overlays
        sosCleanupTimers();
        sosActiveAlert = null;
        const overlayRoot = document.getElementById("sos-overlay-root");
        if (overlayRoot) overlayRoot.innerHTML = "";

        // Wait a beat for session to switch on server
        setTimeout(async () => {
          await sosFetchCurrentUser();
          // Reload inbox notifications for new acting user
          if (sosConfig && sosConfig.demo_mode) {
            sosStartInboxPolling();
          }
          // Check if newly selected user has an active alert
          await sosCheckAndRestoreActiveAlert();
        }, 300);
      } catch (err) {
        console.warn("[Smart SOS] Error handling user switch:", err);
      }
    });
  }

  // ── Cleanup Helpers ───────────────────────────────────────────────────────
  function sosCleanupTimers() {
    if (sosCountdownTimer) {
      clearInterval(sosCountdownTimer);
      sosCountdownTimer = null;
    }
    if (sosLocationStreamTimer) {
      clearInterval(sosLocationStreamTimer);
      sosLocationStreamTimer = null;
    }
    if (sosRespondersPollTimer) {
      clearInterval(sosRespondersPollTimer);
      sosRespondersPollTimer = null;
    }
    if (sosWatchId !== null && navigator.geolocation) {
      navigator.geolocation.clearWatch(sosWatchId);
      sosWatchId = null;
    }
    if (sosMiniMap) {
      try {
        sosMiniMap.remove();
        sosMiniMap = null;
      } catch (_) {}
    }
    sosResponderMarkers = [];
    sosUserMarker = null;
  }

  // Boot when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sosInit);
  } else {
    sosInit();
  }
})();
