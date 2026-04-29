/**
 * Ecovacs GOAT Card
 * -----------------
 * No-build Lovelace card for the ECOVACS GOAT mower integration.
 *
 * YAML usage:
 *   type: custom:ecovacs-goat-card
 *   entity: lawn_mower.mower
 *   battery_entity: sensor.mower_battery_level
 *   area_entity: sensor.mower_mowing_area
 *   progress_entity: sensor.mower_mowing_progress
 *   error_entity: sensor.mower_error
 *   map_entity: sensor.mower_live_map
 *   direction_entity: number.mower_cut_direction
 *   stop_button: button.mower_end_mowing
 */

const DEFAULT_CONFIG = {
  entity: "lawn_mower.mower",
  battery_entity: "sensor.mower_battery_level",
  area_entity: "sensor.mower_mowing_area",
  progress_entity: "sensor.mower_mowing_progress",
  error_entity: "sensor.mower_error",
  map_entity: "sensor.mower_live_map",
  map_static_url: "/local/ecovacs_goat/map-info.json?v=202604272002",
  direction_entity: "number.mower_cut_direction",
  stop_button: "button.mower_end_mowing",
  name: "Mower",
};

const CARD_FORMAT_VERSION = "rounded-summary-v2";
const LIVE_STREAM_KEEPALIVE_MS = 65000;
const KEEPALIVE_DURATION_SECONDS = 600;
const KEEPALIVE_COUNTDOWN_TICK_MS = 1000;

const STATE_META = {
  mowing: { label: "Mowing", className: "active", icon: "mdi:robot-mower" },
  paused: { label: "Paused", className: "paused", icon: "mdi:pause-circle" },
  docked: { label: "Docked", className: "docked", icon: "mdi:home-lightning-bolt" },
  returning: { label: "Returning", className: "returning", icon: "mdi:home-import-outline" },
  idle: { label: "Idle", className: "paused", icon: "mdi:map-marker-alert-outline" },
  error: { label: "Error", className: "error", icon: "mdi:alert-circle" },
  unavailable: { label: "Unavailable", className: "muted", icon: "mdi:cloud-alert" },
  unknown: { label: "Unknown", className: "muted", icon: "mdi:help-circle" },
};

const ACTION_OUTCOMES = {
  start: {
    domain: "lawn_mower",
    service: "start_mowing",
    target: "entity",
    optimisticState: "mowing",
    expectedStates: ["mowing"],
    retryAfterMs: 5000,
    timeoutMs: 18000,
    failureMessage: "Mower did not start mowing.",
  },
  pause: {
    domain: "lawn_mower",
    service: "pause",
    target: "entity",
    optimisticState: "paused",
    expectedStates: ["paused"],
    retryAfterMs: 5000,
    timeoutMs: 18000,
    failureMessage: "Mower did not pause.",
  },
  end: {
    domain: "button",
    service: "press",
    target: "stop_button",
    optimisticState: "idle",
    expectedStates: ["idle", "docked"],
    retryAfterMs: 5000,
    timeoutMs: 18000,
    failureMessage: "Mower did not end mowing.",
  },
  dock: {
    domain: "lawn_mower",
    service: "dock",
    target: "entity",
    optimisticState: "returning",
    expectedStates: ["docked"],
    settlingStates: ["returning"],
    retryAfterMs: 5000,
    timeoutMs: 240000,
    failureMessage: "Mower did not start returning to dock.",
  },
  cancel_dock: {
    domain: "lawn_mower",
    service: "dock",
    target: "entity",
    optimisticState: "paused",
    expectedStates: ["paused", "mowing", "idle"],
    retryAfterMs: 5000,
    timeoutMs: 30000,
    failureMessage: "Mower did not cancel docking.",
  },
};

const STYLE = `
  :host {
    display: block;
  }
  ha-card {
    padding: 16px;
  }
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .title {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }
  .title h2 {
    margin: 0;
    font-size: 1.15rem;
    font-weight: 500;
  }
  .title ha-icon {
    color: var(--primary-color);
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 0.85rem;
    font-weight: 600;
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    white-space: nowrap;
  }
  .chip.active {
    background: var(--success-color, #43a047);
    color: #fff;
  }
  .chip.paused,
  .chip.returning {
    background: var(--warning-color, #f9a825);
    color: #111;
  }
  .chip.docked {
    background: var(--info-color, #0288d1);
    color: #fff;
  }
  .chip.error {
    background: var(--error-color, #d32f2f);
    color: #fff;
  }
  .chip.muted {
    color: var(--secondary-text-color);
  }
  .chip.pending {
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.28) inset;
  }
  .summary {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin: 14px 0;
  }
  .metric {
    padding: 10px;
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    background: var(--secondary-background-color);
  }
  .metric .label {
    color: var(--secondary-text-color);
    font-size: 0.78rem;
    margin-bottom: 4px;
  }
  .metric .value {
    font-size: 1.05rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .error-line {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin: 8px 0 12px;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(211, 47, 47, 0.12);
    color: var(--error-color, #d32f2f);
  }
  .map {
    margin: 0 0 14px;
    border: 1px solid var(--divider-color);
    border-radius: 12px;
    overflow: hidden;
    background: var(--secondary-background-color);
  }
  .map-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 10px 12px;
    color: var(--secondary-text-color);
    font-size: 0.85rem;
  }
  .map-title {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--primary-text-color);
    font-weight: 600;
  }
  .map svg {
    display: block;
    width: 100%;
    min-height: 300px;
    background: #dfe5ec;
  }
  .map-empty {
    padding: 20px 12px;
    color: var(--secondary-text-color);
    text-align: center;
  }
  .map-trail {
    fill: none;
    stroke: #b9ee98;
    stroke-width: 3;
    stroke-linecap: round;
    stroke-linejoin: round;
    opacity: 0.55;
  }
  .map-mowed-area {
    fill: #b9ee98;
    opacity: 0.55;
  }
  .map-garden {
    fill: #4f9a43;
    opacity: 0.9;
  }
  .map-obstacle {
    fill: #dfe5ec;
    opacity: 0.95;
  }
  .map-station {
    fill: #263847;
    stroke: #fff;
    stroke-width: 2;
  }
  .map-beacon {
    fill: #5772e8;
    stroke: #fff;
    stroke-width: 3;
  }
  .map-mower {
    fill: #2196f3;
    stroke: #fff;
    stroke-width: 2;
  }
  .actions {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
  }
  .direction {
    margin: 14px 0 0;
    padding: 10px;
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    background: var(--secondary-background-color);
  }
  .direction-header {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
    color: var(--secondary-text-color);
    font-size: 0.85rem;
  }
  .direction-value {
    color: var(--primary-text-color);
    font-weight: 600;
  }
  .direction-presets {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 6px;
  }
  button {
    min-height: 46px;
    border: 0;
    border-radius: 10px;
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    font: inherit;
    position: relative;
    transition:
      background-color 120ms ease,
      box-shadow 120ms ease,
      filter 120ms ease,
      opacity 120ms ease,
      transform 80ms ease;
  }
  button:active:not(:disabled) {
    filter: brightness(0.82);
    transform: translateY(1px) scale(0.98);
  }
  button.pending {
    box-shadow: inset 0 0 0 999px rgba(255, 255, 255, 0.18);
    filter: saturate(1.15);
  }
  button.pending::after {
    content: "";
    width: 10px;
    height: 10px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: ecovacs-goat-spin 0.8s linear infinite;
  }
  @keyframes ecovacs-goat-spin {
    to {
      transform: rotate(360deg);
    }
  }
  .direction-presets button {
    min-height: 46px;
    padding: 8px 0;
    font-size: 0.95rem;
    font-weight: 600;
    line-height: 1;
  }
  .direction-presets button.selected {
    background: var(--primary-color);
    color: var(--text-primary-color);
    opacity: 1;
  }
  button.primary {
    background: var(--primary-color);
    color: var(--text-primary-color);
  }
  button.pause {
    background: var(--warning-color, #f9a825);
    color: #111;
  }
  button.stop {
    background: var(--error-color, #d32f2f);
    color: #fff;
  }
  button.keepalive-active {
    background: var(--primary-color);
    color: var(--text-primary-color);
  }
  button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .warning {
    color: var(--error-color, #d32f2f);
    padding: 8px 0 0;
  }
  @media (max-width: 450px) {
    .summary {
      grid-template-columns: 1fr;
    }
    .actions {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .direction-presets {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }
`;

class EcovacsGoatCard extends HTMLElement {
  connectedCallback() {
    this._setupVisibilityTracking();
    this._maybeRequestLivePositionStream("card_connected");
  }

  disconnectedCallback() {
    window.clearInterval(this._liveStreamTimer);
    this._liveStreamTimer = null;
    window.clearInterval(this._keepaliveCountdownTimer);
    this._keepaliveCountdownTimer = null;
    if (this._visibilityHandler) {
      document.removeEventListener("visibilitychange", this._visibilityHandler);
      this._visibilityHandler = null;
    }
    if (this._intersectionObserver) {
      this._intersectionObserver.disconnect();
      this._intersectionObserver = null;
    }
  }

  setConfig(config) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  set hass(hass) {
    this._hass = hass;
    this._loadStaticMap();
    this._checkPendingOutcome();
    if (!this.config || !this.querySelector("ha-card")) {
      this.render();
      return;
    }
    const signature = this._nonMapSignature();
    if (signature === this._nonMapSignatureValue) {
      this._updateMapOnly();
      return;
    }
    this.render();
  }

  getCardSize() {
    return 4;
  }

  render() {
    if (!this.config || !this._hass) {
      return;
    }

    const mower = this._state(this.config.entity);
    const actualState = mower?.state || "unavailable";
    const state = this._displayState(actualState);
    const meta = STATE_META[state] || {
      label: this._label(state),
      className: "muted",
      icon: "mdi:robot-mower-outline",
    };
    const pendingAction = this._pendingAction?.key;
    const chipClass = `${meta.className}${pendingAction ? " pending" : ""}`;

    const area = this._state(this.config.area_entity);
    const progress = this._state(this.config.progress_entity);
    const battery = this._state(this.config.battery_entity);
    const error = this._state(this.config.error_entity);
    const direction = this._state(this.config.direction_entity);
    const map = this._state(this.config.map_entity);
    const unavailable = !mower || state === "unavailable";
    const returning = state === "returning";
    const mowing = state === "mowing";
    const paused = state === "paused";
    const docked = state === "docked";
    const errorState = state === "error";
    const primaryDisabled = unavailable || returning;
    const endDisabled = unavailable || returning || (!mowing && !paused && !errorState);
    const dockDisabled = unavailable || docked;
    const primaryAction = mowing ? "pause" : "start";
    const primaryLabel = mowing ? "Pause" : paused ? "Resume" : "Start";
    const primaryIcon = mowing ? "mdi:pause" : "mdi:play";
    const primaryClass = mowing ? "pause" : "primary";
    const dockLabel = returning ? "Cancel Dock" : "Dock";
    const dockIcon = returning ? "mdi:home-export-outline" : "mdi:home-import-outline";
    const dockAction = returning ? "cancel_dock" : "dock";
    const keepaliveRemaining = this._keepaliveRemainingSeconds();
    const keepaliveActive = keepaliveRemaining > 0;
    const keepaliveClass = keepaliveActive ? "keepalive-active" : "";
    const keepaliveIcon = keepaliveActive ? "mdi:timer-sand" : "mdi:access-point-network";
    const keepaliveLabel = keepaliveActive
      ? this._formatKeepaliveRemaining(keepaliveRemaining)
      : "Keepalive";

    this.innerHTML = `
      <ha-card>
        <style>${STYLE}</style>
        <div class="header">
          <div class="title">
            <ha-icon icon="mdi:robot-mower-outline"></ha-icon>
            <h2>${this._escape(this.config.name || this._friendlyName(mower) || "Mower")}</h2>
          </div>
          <div class="chip ${chipClass}">
            <ha-icon icon="${meta.icon}"></ha-icon>
            ${this._escape(meta.label)}
          </div>
        </div>

        <div class="summary">
          ${this._metric("Mowing area", this._formatAreaState(area))}
          ${this._metric("Progress", this._formatRoundedState(progress))}
          ${this._metric("Battery", this._formatState(battery))}
        </div>

        ${this._errorLine(error)}

        <div class="map-slot">${this._mapPanel(map, state)}</div>

        <div class="actions">
          <button class="${this._buttonClass(primaryClass, primaryAction)}" data-action="${primaryAction}" ${primaryDisabled ? "disabled" : ""}>
            <ha-icon icon="${primaryIcon}"></ha-icon>
            <span>${primaryLabel}</span>
          </button>
          <button class="${this._buttonClass("stop", "end")}" data-action="end" ${endDisabled ? "disabled" : ""}>
            <ha-icon icon="mdi:stop"></ha-icon>
            <span>End</span>
          </button>
          <button class="${this._buttonClass("", dockAction)}" data-action="${dockAction}" ${dockDisabled ? "disabled" : ""}>
            <ha-icon icon="${dockIcon}"></ha-icon>
            <span>${dockLabel}</span>
          </button>
          <button class="${this._buttonClass(keepaliveClass, "keepalive")}" data-action="keepalive">
            <ha-icon icon="${keepaliveIcon}"></ha-icon>
            <span>${keepaliveLabel}</span>
          </button>
        </div>

        ${this._directionControl(direction)}
      </ha-card>
    `;

    this.querySelector('[data-action="start"]')?.addEventListener("click", () =>
      this._runStateAction("start")
    );
    this.querySelector('[data-action="pause"]')?.addEventListener("click", () =>
      this._runStateAction("pause")
    );
    this.querySelector('[data-action="end"]')?.addEventListener("click", () =>
      this._runStateAction("end")
    );
    this.querySelector('[data-action="dock"]')?.addEventListener("click", () =>
      this._runStateAction("dock")
    );
    this.querySelector('[data-action="cancel_dock"]')?.addEventListener("click", () =>
      this._runStateAction("cancel_dock")
    );
    this.querySelector('[data-action="keepalive"]')?.addEventListener("click", () =>
      this._runKeepaliveAction()
    );
    this.querySelectorAll("[data-direction]").forEach((button) =>
      button.addEventListener("click", () =>
        this._runDirectionAction(Number(button.dataset.direction))
      )
    );
    this._nonMapSignatureValue = this._nonMapSignature();
  }

  _state(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _displayState(actualState) {
    if (
      this._pendingAction?.type === "state" &&
      this._pendingAction.optimisticState &&
      !this._stateOutcomeReached(this._pendingAction)
    ) {
      return this._pendingAction.optimisticState;
    }
    return actualState;
  }

  _buttonClass(baseClass, key) {
    return [baseClass, this._pendingAction?.key === key ? "pending" : ""]
      .filter(Boolean)
      .join(" ");
  }

  _nonMapSignature() {
    const entityState = (entityId) => {
      const state = this._state(entityId);
      return [
        entityId,
        state?.state,
        state?.attributes?.unit_of_measurement,
        state?.attributes?.description,
        state?.attributes?.friendly_name,
      ];
    };
    return JSON.stringify([
      CARD_FORMAT_VERSION,
      entityState(this.config.entity),
      entityState(this.config.area_entity),
      entityState(this.config.progress_entity),
      entityState(this.config.battery_entity),
      entityState(this.config.error_entity),
      entityState(this.config.direction_entity),
      this._pendingAction?.key,
      this._pendingAction?.optimisticState,
      this._keepaliveRemainingSeconds(),
    ]);
  }

  _updateMapOnly() {
    const slot = this.querySelector(".map-slot");
    if (!slot) {
      this.render();
      return;
    }
    const mower = this._state(this.config.entity);
    const actualState = mower?.state || "unavailable";
    slot.innerHTML = this._mapPanel(this._state(this.config.map_entity), this._displayState(actualState));
  }

  _runStateAction(key) {
    const outcome = ACTION_OUTCOMES[key];
    const entityId = outcome?.target ? this.config[outcome.target] : undefined;
    if (!outcome || !entityId) {
      return;
    }

    this._beginPendingAction({
      key,
      type: "state",
      domain: outcome.domain,
      service: outcome.service,
      entityId,
      optimisticState: outcome.optimisticState,
      expectedStates: outcome.expectedStates,
      settlingStates: outcome.settlingStates,
      alreadySatisfied: outcome.expectedStates?.includes(this._state(this.config.entity)?.state),
      retryAfterMs: outcome.retryAfterMs,
      timeoutMs: outcome.timeoutMs,
      failureMessage: outcome.failureMessage,
      attempts: 1,
    });
    this._callAction(outcome.domain, outcome.service, entityId)
      .then(() => {
        if (["start", "end", "dock", "cancel_dock"].includes(key)) {
          this._startKeepalive(`card_${key}`);
        }
      })
      .catch(() => this._failPendingAction(key, outcome.failureMessage));
  }

  _runDirectionAction(value) {
    if (!Number.isFinite(value) || !this.config.direction_entity) {
      return;
    }
    this._beginPendingAction({
      key: `direction-${value}`,
      type: "direction",
      domain: "number",
      service: "set_value",
      entityId: this.config.direction_entity,
      serviceData: { value },
      expectedValue: value,
      retryAfterMs: 5000,
      timeoutMs: 16000,
      failureMessage: `Cut direction did not change to ${value} degrees.`,
      attempts: 1,
    });
    this._callAction("number", "set_value", this.config.direction_entity, { value }).catch(
      () => this._failPendingAction(`direction-${value}`, `Could not set cut direction to ${value} degrees.`)
    );
  }

  _runKeepaliveAction() {
    this._startKeepalive("card_keepalive_button", true);
  }

  _startKeepalive(reason, surfaceErrors = false) {
    this._keepaliveUntil = Date.now() + KEEPALIVE_DURATION_SECONDS * 1000;
    this._startKeepaliveCountdown();
    const request = this._maybeRequestLivePositionStream(
      reason,
      true,
      KEEPALIVE_DURATION_SECONDS,
      surfaceErrors
    );
    request?.catch(() => {
      this._keepaliveUntil = 0;
      this._stopKeepaliveCountdown();
      this._showToast("Could not start mower keepalive.");
      this.render();
    });
    this.render();
  }

  _beginPendingAction(action) {
    this._clearPendingAction();
    this._pendingAction = action;
    this.render();

    if (action.alreadySatisfied) {
      action.timeoutTimer = window.setTimeout(() => {
        if (this._pendingAction?.key === action.key) {
          this._clearPendingAction();
          this.render();
        }
      }, 900);
      return;
    }

    action.retryTimer = window.setTimeout(() => {
      if (!this._pendingAction || this._pendingAction.key !== action.key) {
        return;
      }
      if (
        this._pendingOutcomeReached(action) ||
        this._pendingActionIsSettling(action) ||
        action.attempts >= 2
      ) {
        return;
      }
      action.attempts += 1;
      this._callAction(action.domain, action.service, action.entityId, action.serviceData).catch(
        () => this._failPendingAction(action.key, action.failureMessage)
      );
    }, action.retryAfterMs);

    action.timeoutTimer = window.setTimeout(() => {
      if (!this._pendingAction || this._pendingAction.key !== action.key) {
        return;
      }
      if (this._pendingOutcomeReached(action)) {
        this._clearPendingAction();
        this.render();
        return;
      }
      this._failPendingAction(action.key, action.failureMessage);
    }, action.timeoutMs);
  }

  _checkPendingOutcome() {
    if (this._pendingAction && this._pendingOutcomeReached(this._pendingAction)) {
      this._clearPendingAction();
    }
  }

  _pendingOutcomeReached(action) {
    if (action.type === "state") {
      return this._stateOutcomeReached(action);
    }
    if (action.type === "direction") {
      return this._directionOutcomeReached(action);
    }
    return false;
  }

  _pendingActionIsSettling(action) {
    if (action.type !== "state") {
      return false;
    }
    return action.settlingStates?.includes(this._state(this.config.entity)?.state);
  }

  _stateOutcomeReached(action) {
    const state = this._state(this.config.entity)?.state;
    return action.expectedStates?.includes(state);
  }

  _directionOutcomeReached(action) {
    const value = Number(this._state(action.entityId)?.state);
    return Number.isFinite(value) && Math.round(value) === action.expectedValue;
  }

  _failPendingAction(key, message) {
    if (this._pendingAction?.key !== key) {
      return;
    }
    this._clearPendingAction();
    this._showToast(message);
    this.render();
  }

  _clearPendingAction() {
    if (!this._pendingAction) {
      return;
    }
    window.clearTimeout(this._pendingAction.retryTimer);
    window.clearTimeout(this._pendingAction.timeoutTimer);
    this._pendingAction = null;
  }

  _callAction(domain, service, entityId, data = {}) {
    return this._hass.callService(domain, service, {
      entity_id: entityId,
      ...data,
    });
  }

  _setupVisibilityTracking() {
    if (!this._visibilityHandler) {
      this._visibilityHandler = () =>
        this._maybeRequestLivePositionStream("card_visibility");
      document.addEventListener("visibilitychange", this._visibilityHandler);
    }
    if (!this._intersectionObserver && "IntersectionObserver" in window) {
      this._visibleOnPage = false;
      this._intersectionObserver = new IntersectionObserver((entries) => {
        this._visibleOnPage = entries.some((entry) => entry.isIntersecting);
        this._maybeRequestLivePositionStream("card_intersection");
      });
      this._intersectionObserver.observe(this);
    } else if (!("IntersectionObserver" in window)) {
      this._visibleOnPage = true;
    }
  }

  _maybeRequestLivePositionStream(
    reason,
    force = false,
    durationSeconds = 0,
    surfaceErrors = false
  ) {
    if (!this.config || !this._hass || (!force && !this._cardIsVisible())) {
      return Promise.resolve(false);
    }
    if (!force && this._state(this.config.entity)?.state !== "mowing") {
      return Promise.resolve(false);
    }
    const now = Date.now();
    if (
      !force &&
      this._lastLiveStreamRequestAt &&
      now - this._lastLiveStreamRequestAt < LIVE_STREAM_KEEPALIVE_MS
    ) {
      return Promise.resolve(false);
    }
    this._lastLiveStreamRequestAt = now;
    const serviceData = {
      entity_id: this.config.entity,
      reason,
      force,
    };
    if (durationSeconds > 0) {
      serviceData.duration_seconds = durationSeconds;
    }
    return this._hass
      .callService("ecovacs_goat_g1", "request_live_position_stream", serviceData)
      .catch((err) => {
        this._lastLiveStreamRequestAt = 0;
        if (surfaceErrors) {
          throw err;
        }
        return false;
      });
  }

  _startKeepaliveCountdown() {
    if (this._keepaliveCountdownTimer) {
      return;
    }
    this._keepaliveCountdownTimer = window.setInterval(() => {
      if (this._keepaliveRemainingSeconds() <= 0) {
        this._stopKeepaliveCountdown();
      }
      this.render();
    }, KEEPALIVE_COUNTDOWN_TICK_MS);
  }

  _stopKeepaliveCountdown() {
    window.clearInterval(this._keepaliveCountdownTimer);
    this._keepaliveCountdownTimer = null;
  }

  _keepaliveRemainingSeconds() {
    if (!this._keepaliveUntil) {
      return 0;
    }
    return Math.max(0, Math.ceil((this._keepaliveUntil - Date.now()) / 1000));
  }

  _formatKeepaliveRemaining(seconds) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
  }

  _cardIsVisible() {
    return (
      this.isConnected &&
      document.visibilityState !== "hidden" &&
      this._visibleOnPage !== false
    );
  }

  _showToast(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message },
        bubbles: true,
        composed: true,
      })
    );
  }

  _metric(label, value) {
    return `
      <div class="metric">
        <div class="label">${this._escape(label)}</div>
        <div class="value">${value || "Unavailable"}</div>
      </div>
    `;
  }

  _errorLine(error) {
    if (!error || error.state in { unknown: true, unavailable: true }) {
      return "";
    }
    const description = error.attributes?.description;
    const value = this._formatState(error);
    if (error.state === "0" || error.state === "100") {
      return "";
    }
    return `
      <div class="error-line">
        <ha-icon icon="mdi:alert-circle"></ha-icon>
        <span>${value}${description ? ` - ${this._escape(description)}` : ""}</span>
      </div>
    `;
  }

  _mapPanel(mapState, mowerState) {
    const map = mapState?.attributes;
    if (!map || mapState.state === "unavailable") {
      return "";
    }

    const liveInfo = map.info || {};
    const info = liveInfo.outline?.length ? liveInfo : this._staticMapInfo || {};
    const garden = this._positions(info.outline);
    const obstacles = this._polygons(info.obstacles);
    const livePath = this._positions(map.position_history);
    const tracePath = this._positions(map.trace?.path);
    const mowedArea = tracePath;
    const charge = this._positions(map.charge_positions);
    const rawCurrent = this._position(map.current_position);
    const current = this._displayMowerPosition({
      mowerState,
      rawCurrent,
      charge,
    });
    const beacons = this._positions(map.uwb_positions);

    return `
      <div class="map">
        ${this._mapSvg({
          garden,
          obstacles,
          mowedArea,
          livePath,
          current,
          charge,
          beacons,
          mowerState,
        })}
      </div>
    `;
  }

  _mapSvg({ garden, obstacles, mowedArea, livePath, current, charge, beacons, mowerState }) {
    const points = [
      ...garden,
      ...obstacles.flat(),
      ...mowedArea,
      ...livePath,
      ...(current ? [current] : []),
      ...charge,
      ...beacons,
    ];
    if (!points.length) {
      return `<div class="map-empty">Waiting for live map data</div>`;
    }

    const width = 360;
    const height = 300;
    const bounds = this._mapBounds(points);
    const gardenPath = this._closedPath(garden, bounds, width, height);
    const mowedAreaPath = this._closedPath(mowedArea, bounds, width, height);
    const liveSvgPath = this._path(livePath, bounds, width, height);
    const currentPoint = current ? this._project(current, bounds, width, height) : null;
    const currentHeading = current
      ? this._mowerHeading(current, charge, livePath, mowerState)
      : 0;
    const markerAnimation = this._mowerMarkerAnimation(
      current,
      currentPoint,
      bounds,
      width,
      height
    );

    return `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Live mower map">
        ${gardenPath ? `<path class="map-garden" d="${gardenPath}"></path>` : ""}
        ${obstacles
          .map((obstacle) => this._closedPath(obstacle, bounds, width, height))
          .filter(Boolean)
          .map((path) => `<path class="map-obstacle" d="${path}"></path>`)
          .join("")}
        ${mowedAreaPath ? `<path class="map-mowed-area" d="${mowedAreaPath}"></path>` : ""}
        ${liveSvgPath ? `<path class="map-trail" d="${liveSvgPath}"></path>` : ""}
        ${charge
          .map((position) => {
            const point = this._project(position, bounds, width, height);
            return `
              <path class="map-station" d="${this._housePath(point.x, point.y - 10)}"></path>
            `;
          })
          .join("")}
        ${beacons
          .map((position) => {
            const point = this._project(position, bounds, width, height);
            return `
              <circle class="map-beacon" cx="${point.x}" cy="${point.y}" r="6"></circle>
            `;
          })
          .join("")}
        ${currentPoint
          ? `
            <g transform="translate(${currentPoint.x} ${currentPoint.y})">
              ${markerAnimation}
              <g transform="rotate(${currentHeading})">
                <path class="map-mower" d="M 13 0 L -9 -8 L -5 0 L -9 8 Z"></path>
              </g>
            </g>
          `
          : ""}
      </svg>
    `;
  }

  _displayMowerPosition({ mowerState, rawCurrent, charge }) {
    if (mowerState === "docked" && charge.length) {
      return rawCurrent || charge[0];
    }
    return rawCurrent;
  }

  _fastestMowerPosition(rawCurrent, livePath, tracePath) {
    const traceLead = this._traceLeadPosition(tracePath, rawCurrent);
    if (!traceLead) {
      return rawCurrent;
    }

    const previous = this._lastMowerPosition || this._lastPathPosition(livePath);
    if (!previous) {
      return rawCurrent || traceLead;
    }

    const rawDistance = rawCurrent
      ? Math.hypot(rawCurrent.x - previous.x, rawCurrent.y - previous.y)
      : 0;
    const traceDistance = Math.hypot(traceLead.x - previous.x, traceLead.y - previous.y);
    return traceDistance > rawDistance + 50 ? traceLead : rawCurrent || traceLead;
  }

  _traceLeadPosition(tracePath, rawCurrent) {
    if (!tracePath.length) {
      this._lastTracePointKeys = null;
      return null;
    }

    const pointKeys = new Set(tracePath.map((position) => this._positionKey(position)));
    const previousKeys = this._lastTracePointKeys;
    this._lastTracePointKeys = pointKeys;
    if (!previousKeys) {
      return null;
    }

    const changed = tracePath.filter((position) => !previousKeys.has(this._positionKey(position)));
    if (!changed.length) {
      return null;
    }

    const anchor = rawCurrent || this._lastMowerPosition || this._lastPathPosition(tracePath);
    if (!anchor) {
      return changed[changed.length - 1];
    }
    return changed.reduce((closest, position) => {
      const closestDistance = Math.hypot(closest.x - anchor.x, closest.y - anchor.y);
      const distance = Math.hypot(position.x - anchor.x, position.y - anchor.y);
      return distance < closestDistance ? position : closest;
    }, changed[0]);
  }

  _lastPathPosition(path) {
    return path.length ? path[path.length - 1] : null;
  }

  _positionKey(position) {
    return `${Math.round(position.x)}:${Math.round(position.y)}`;
  }

  _mapBounds(points) {
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    let minX = Math.min(...xs);
    let maxX = Math.max(...xs);
    let minY = Math.min(...ys);
    let maxY = Math.max(...ys);

    if (minX === maxX) {
      minX -= 1000;
      maxX += 1000;
    }
    if (minY === maxY) {
      minY -= 1000;
      maxY += 1000;
    }

    return { minX, maxX, minY, maxY };
  }

  _path(points, bounds, width, height) {
    if (points.length < 2) {
      return "";
    }
    return points
      .map((position, index) => {
        const point = this._project(position, bounds, width, height);
        return `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
      })
      .join(" ");
  }

  _closedPath(points, bounds, width, height) {
    const path = this._path(points, bounds, width, height);
    return path ? `${path} Z` : "";
  }

  _housePath(x, y) {
    const size = 10;
    return [
      `M ${x.toFixed(1)} ${(y - size).toFixed(1)}`,
      `L ${(x + size).toFixed(1)} ${(y - 1).toFixed(1)}`,
      `L ${(x + size * 0.7).toFixed(1)} ${(y - 1).toFixed(1)}`,
      `L ${(x + size * 0.7).toFixed(1)} ${(y + size).toFixed(1)}`,
      `L ${(x - size * 0.7).toFixed(1)} ${(y + size).toFixed(1)}`,
      `L ${(x - size * 0.7).toFixed(1)} ${(y - 1).toFixed(1)}`,
      `L ${(x - size).toFixed(1)} ${(y - 1).toFixed(1)}`,
      "Z",
    ].join(" ");
  }

  _mowerHeading(current, charge, path, mowerState) {
    if (mowerState === "docked" && charge.length) {
      const heading = this._headingToTarget(current, charge[0]);
      if (heading !== null) {
        return heading;
      }
      return Number(charge[0].a || current.a || 0) + 180;
    }
    return this._heading(current, path);
  }

  _headingToTarget(current, target) {
    const dx = target.x - current.x;
    const dy = -(target.y - current.y);
    if (Math.hypot(dx, dy) < 1) {
      return null;
    }
    return (Math.atan2(dy, dx) * 180) / Math.PI;
  }

  _heading(current, path) {
    const previous = this._previousPosition(current, path);
    if (previous) {
      const dx = current.x - previous.x;
      const dy = -(current.y - previous.y);
      if (Math.hypot(dx, dy) >= 1) {
        return (Math.atan2(dy, dx) * 180) / Math.PI;
      }
    }
    return this._mowerHeadingToSvg(current) ?? 0;
  }

  _mowerHeadingToSvg(position) {
    const heading = Number(position?.a);
    if (!Number.isFinite(heading)) {
      return null;
    }
    return heading - 90;
  }

  _previousPosition(current, path) {
    for (let index = path.length - 1; index >= 0; index -= 1) {
      const candidate = path[index];
      if (Math.hypot(current.x - candidate.x, current.y - candidate.y) > 20) {
        return candidate;
      }
    }
    if (path.length >= 2) {
      return path[path.length - 2];
    }
    return null;
  }

  _project(position, bounds, width, height) {
    const padding = 4;
    const spanX = bounds.maxX - bounds.minX;
    const spanY = bounds.maxY - bounds.minY;
    const scale = Math.min(
      (width - padding * 2) / spanX,
      (height - padding * 2) / spanY
    );
    const drawnWidth = spanX * scale;
    const drawnHeight = spanY * scale;
    const offsetX = (width - drawnWidth) / 2;
    const offsetY = (height - drawnHeight) / 2;

    return {
      x: offsetX + (position.x - bounds.minX) * scale,
      y: height - (offsetY + (position.y - bounds.minY) * scale),
    };
  }

  _mowerMarkerAnimation(current, currentPoint, bounds, width, height) {
    if (!current || !currentPoint) {
      this._lastMowerPosition = null;
      this._lastMowerUpdateAt = null;
      return "";
    }

    const previousPosition = this._lastMowerPosition;
    const previousUpdateAt = this._lastMowerUpdateAt;
    const updateAt = Date.now();
    this._lastMowerPosition = current;
    this._lastMowerUpdateAt = updateAt;

    if (!previousPosition) {
      return "";
    }

    const previousPoint = this._project(previousPosition, bounds, width, height);
    const distance = Math.hypot(
      currentPoint.x - previousPoint.x,
      currentPoint.y - previousPoint.y
    );
    if (distance < 1) {
      return "";
    }

    const elapsedMs = previousUpdateAt ? updateAt - previousUpdateAt : 2500;
    const durationSeconds = Math.min(8, Math.max(1.2, elapsedMs / 1000 + 0.3));

    return `
      <animateTransform
        attributeName="transform"
        type="translate"
        from="${previousPoint.x} ${previousPoint.y}"
        to="${currentPoint.x} ${currentPoint.y}"
        dur="${durationSeconds.toFixed(1)}s"
        fill="freeze"
      ></animateTransform>
    `;
  }

  _positions(value) {
    return Array.isArray(value)
      ? value.map((position) => this._position(position)).filter(Boolean)
      : [];
  }

  _polygons(value) {
    return Array.isArray(value)
      ? value.map((polygon) => this._positions(polygon)).filter((polygon) => polygon.length)
      : [];
  }

  _combinedPath(primary, secondary) {
    const combined = [];
    const seen = new Set();
    [...primary, ...secondary].forEach((position) => {
      const key = `${Math.round(position.x)}:${Math.round(position.y)}`;
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      combined.push(position);
    });
    return combined;
  }

  _position(value) {
    if (!value || value.x === undefined || value.y === undefined) {
      return null;
    }
    const x = Number(value.x);
    const y = Number(value.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return null;
    }
    return {
      ...value,
      x,
      y,
      a: value.a === undefined || value.a === null ? null : Number(value.a),
    };
  }

  _directionControl(direction) {
    const value = Number(direction?.state);
    const safeValue = Number.isFinite(value) ? Math.round(value) : 90;
    const pendingValue =
      this._pendingAction?.type === "direction"
        ? this._pendingAction.expectedValue
        : null;
    const displayValue = Number.isFinite(pendingValue) ? pendingValue : safeValue;
    return `
      <div class="direction">
        <div class="direction-header">
          <span>Cut direction</span>
          <span class="direction-value">${Number.isFinite(displayValue) ? `${displayValue}°` : "Unknown"}</span>
        </div>
        <div class="direction-presets">
          ${[0, 45, 90, 135, 180]
            .map(
              (angle) => `
                <button class="${[
                  displayValue === angle ? "selected" : "",
                  this._pendingAction?.key === `direction-${angle}` ? "pending" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}" data-direction="${angle}">
                  ${angle}°
                </button>
              `
            )
            .join("")}
        </div>
      </div>
    `;
  }

  _formatState(stateObj) {
    if (!stateObj) {
      return "Unavailable";
    }
    if (stateObj.state === "unknown" || stateObj.state === "unavailable") {
      return this._label(stateObj.state);
    }
    if (this._hass?.formatEntityState) {
      return this._escape(this._hass.formatEntityState(stateObj));
    }
    const unit = stateObj.attributes?.unit_of_measurement;
    return `${this._escape(stateObj.state)}${unit ? ` ${this._escape(unit)}` : ""}`;
  }

  _formatAreaState(stateObj) {
    if (!stateObj) {
      return "Unavailable";
    }
    if (stateObj.state === "unknown" || stateObj.state === "unavailable") {
      return this._label(stateObj.state);
    }

    const targetUnit = this._areaDisplayUnit();
    if (targetUnit !== "ft²") {
      const value = Number(stateObj.state);
      if (!Number.isFinite(value)) {
        return this._formatState(stateObj);
      }
      const unit = stateObj.attributes?.unit_of_measurement;
      return `${this._formatFixed(value, 0)}${unit ? ` ${this._escape(unit)}` : ""}`;
    }

    const value = Number(stateObj.state);
    const sourceUnit = stateObj.attributes?.unit_of_measurement;
    if (!Number.isFinite(value)) {
      return this._formatState(stateObj);
    }

    let squareFeet;
    if (sourceUnit === "ft²") {
      squareFeet = value;
    } else if (sourceUnit === "m²") {
      squareFeet = value * 10.76391041671;
    } else if (sourceUnit === "cm²") {
      squareFeet = value / 929.0304;
    } else if (sourceUnit === "in²") {
      squareFeet = value / 144;
    } else {
      return this._formatState(stateObj);
    }

    return `${this._formatFixed(squareFeet, 0)} ft²`;
  }

  _formatRoundedState(stateObj) {
    if (!stateObj) {
      return "Unavailable";
    }
    if (stateObj.state === "unknown" || stateObj.state === "unavailable") {
      return this._label(stateObj.state);
    }
    const value = Number(stateObj.state);
    if (!Number.isFinite(value)) {
      return this._formatState(stateObj);
    }
    const unit = stateObj.attributes?.unit_of_measurement;
    return `${Math.round(value)}${unit ? ` ${this._escape(unit)}` : ""}`;
  }

  _areaDisplayUnit() {
    return (
      this._hass?.config?.unit_system?.area ||
      this._hass?.config?.unit_system?.area_unit ||
      (this._hass?.locale?.unit_system === "us_customary" ? "ft²" : undefined)
    );
  }

  _formatFixed(value, digits) {
    const language = this._hass?.locale?.language || navigator.language;
    return new Intl.NumberFormat(language, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  }

  _friendlyName(stateObj) {
    return stateObj?.attributes?.friendly_name;
  }

  _label(value) {
    return String(value || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  _escape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _loadStaticMap() {
    const url = this.config?.map_static_url;
    if (!url || this._staticMapInfo !== undefined || this._staticMapPromise) {
      return;
    }
    this._staticMapPromise = fetch(url)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        this._staticMapInfo = data || null;
        this._staticMapPromise = null;
        this.render();
      })
      .catch(() => {
        this._staticMapInfo = null;
        this._staticMapPromise = null;
      });
  }

  _call(domain, service, entityId) {
    if (!entityId) {
      return;
    }
    this._hass.callService(domain, service, {
      entity_id: entityId,
    });
  }

  _setDirection(value) {
    if (!Number.isFinite(value) || !this.config.direction_entity) {
      return;
    }
    this._hass.callService("number", "set_value", {
      entity_id: this.config.direction_entity,
      value,
    });
  }
}

customElements.define("ecovacs-goat-card", EcovacsGoatCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "ecovacs-goat-card",
  name: "Ecovacs GOAT Card",
  description: "Control an ECOVACS GOAT mower with explicit start, stop, dock, and live-map keepalive buttons.",
});
