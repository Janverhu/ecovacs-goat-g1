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
 *   direction_entity: number.mower_cut_direction
 *   stop_button: button.mower_end_mowing
 *   refresh_button: button.mower_refresh_state
 */

const DEFAULT_CONFIG = {
  entity: "lawn_mower.mower",
  battery_entity: "sensor.mower_battery_level",
  area_entity: "sensor.mower_mowing_area",
  progress_entity: "sensor.mower_mowing_progress",
  error_entity: "sensor.mower_error",
  direction_entity: "number.mower_cut_direction",
  stop_button: "button.mower_end_mowing",
  refresh_button: "button.mower_refresh_state",
  name: "Mower",
};

const STATE_META = {
  mowing: { label: "Mowing", className: "active", icon: "mdi:robot-mower" },
  paused: { label: "Paused", className: "paused", icon: "mdi:pause-circle" },
  docked: { label: "Docked", className: "docked", icon: "mdi:home-lightning-bolt" },
  returning: { label: "Returning", className: "returning", icon: "mdi:home-import-outline" },
  error: { label: "Error", className: "error", icon: "mdi:alert-circle" },
  unavailable: { label: "Unavailable", className: "muted", icon: "mdi:cloud-alert" },
  unknown: { label: "Unknown", className: "muted", icon: "mdi:help-circle" },
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
  button.stop {
    background: var(--error-color, #d32f2f);
    color: #fff;
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
  setConfig(config) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  set hass(hass) {
    this._hass = hass;
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
    const state = mower?.state || "unavailable";
    const meta = STATE_META[state] || {
      label: this._label(state),
      className: "muted",
      icon: "mdi:robot-mower-outline",
    };

    const area = this._state(this.config.area_entity);
    const progress = this._state(this.config.progress_entity);
    const battery = this._state(this.config.battery_entity);
    const error = this._state(this.config.error_entity);
    const direction = this._state(this.config.direction_entity);
    const unavailable = !mower || state === "unavailable";
    const returning = state === "returning";
    const mowing = state === "mowing";
    const paused = state === "paused";
    const startDisabled = unavailable || returning || mowing;
    const endDisabled = unavailable || returning || (!mowing && !paused);
    const dockDisabled = unavailable || returning || state === "docked";
    const primaryAction = mowing ? "pause" : "start";
    const primaryLabel = mowing ? "Pause" : paused ? "Resume" : "Start";
    const primaryIcon = mowing ? "mdi:pause" : "mdi:play";

    this.innerHTML = `
      <ha-card>
        <style>${STYLE}</style>
        <div class="header">
          <div class="title">
            <ha-icon icon="mdi:robot-mower-outline"></ha-icon>
            <h2>${this._escape(this.config.name || this._friendlyName(mower) || "Mower")}</h2>
          </div>
          <div class="chip ${meta.className}">
            <ha-icon icon="${meta.icon}"></ha-icon>
            ${this._escape(meta.label)}
          </div>
        </div>

        <div class="summary">
          ${this._metric("Mowing area", this._formatState(area))}
          ${this._metric("Progress", this._formatState(progress))}
          ${this._metric("Battery", this._formatState(battery))}
        </div>

        ${this._errorLine(error)}

        <div class="actions">
          <button class="primary" data-action="${primaryAction}" ${startDisabled ? "disabled" : ""}>
            <ha-icon icon="${primaryIcon}"></ha-icon>
            <span>${primaryLabel}</span>
          </button>
          <button class="stop" data-action="end" ${endDisabled ? "disabled" : ""}>
            <ha-icon icon="mdi:stop"></ha-icon>
            <span>End</span>
          </button>
          <button data-action="dock" ${dockDisabled ? "disabled" : ""}>
            <ha-icon icon="mdi:home-import-outline"></ha-icon>
            <span>Dock</span>
          </button>
          <button data-action="refresh">
            <ha-icon icon="mdi:refresh"></ha-icon>
            <span>Refresh</span>
          </button>
        </div>

        ${this._directionControl(direction)}
      </ha-card>
    `;

    this.querySelector('[data-action="start"]')?.addEventListener("click", () =>
      this._call("lawn_mower", "start_mowing", this.config.entity)
    );
    this.querySelector('[data-action="pause"]')?.addEventListener("click", () =>
      this._call("lawn_mower", "pause", this.config.entity)
    );
    this.querySelector('[data-action="end"]')?.addEventListener("click", () =>
      this._call("button", "press", this.config.stop_button)
    );
    this.querySelector('[data-action="dock"]')?.addEventListener("click", () =>
      this._call("lawn_mower", "dock", this.config.entity)
    );
    this.querySelector('[data-action="refresh"]')?.addEventListener("click", () =>
      this._call("button", "press", this.config.refresh_button)
    );
    this.querySelectorAll("[data-direction]").forEach((button) =>
      button.addEventListener("click", () =>
        this._setDirection(Number(button.dataset.direction))
      )
    );
  }

  _state(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
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

  _directionControl(direction) {
    const value = Number(direction?.state);
    const safeValue = Number.isFinite(value) ? Math.round(value) : 90;
    return `
      <div class="direction">
        <div class="direction-header">
          <span>Cut direction</span>
          <span class="direction-value">${Number.isFinite(value) ? `${safeValue}°` : "Unknown"}</span>
        </div>
        <div class="direction-presets">
          ${[0, 45, 90, 135, 180]
            .map(
              (angle) => `
                <button class="${safeValue === angle ? "selected" : ""}" data-direction="${angle}">
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
    const unit = stateObj.attributes?.unit_of_measurement;
    return `${this._escape(stateObj.state)}${unit ? ` ${this._escape(unit)}` : ""}`;
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
  description: "Control an ECOVACS GOAT mower with explicit start, stop, dock, and refresh buttons.",
});
