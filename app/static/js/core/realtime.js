// WebSocket stream with auto-reconnect; falls back to REST polling if sockets fail.
import { api } from "./api.js";

export class Realtime {
  constructor({ onSnapshot, onStatus }) {
    this.onSnapshot = onSnapshot;
    this.onStatus = onStatus;
    this.ws = null;
    this.provider = null;
    this.retry = 0;
    this.pollTimer = null;
    this.pingTimer = null;
  }

  connect(provider) {
    this.close();
    this.provider = provider;
    this.retry = 0;
    this._open();
  }

  _open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/${this.provider}`);
    this.ws = ws;
    this.onStatus("connecting");
    ws.onopen = () => {
      this.retry = 0;
      this._stopPolling();
      this.onStatus("live");
      this.pingTimer = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: "ping" })), 25000);
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "vehicles" && msg.provider === this.provider) this.onSnapshot(msg);
      else if (msg.type === "status" && msg.status === "error") this.onStatus("error", msg.detail);
    };
    ws.onclose = () => {
      clearInterval(this.pingTimer);
      if (this.ws !== ws) return; // replaced by a newer connection
      this.retry++;
      this.onStatus("reconnecting");
      if (this.retry >= 3) this._startPolling();
      setTimeout(() => this.ws === ws && this._open(), Math.min(15000, 600 * 2 ** this.retry));
    };
  }

  _startPolling() {
    if (this.pollTimer) return;
    const tick = async () => {
      try {
        const snap = await api.vehicles(this.provider);
        this.onSnapshot(snap);
        this.onStatus("polling");
      } catch (e) {
        this.onStatus("error", e.message);
      }
    };
    tick();
    this.pollTimer = setInterval(tick, 3000);
  }

  _stopPolling() {
    clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  close() {
    this._stopPolling();
    clearInterval(this.pingTimer);
    const ws = this.ws;
    this.ws = null;
    if (ws) ws.close();
  }
}
