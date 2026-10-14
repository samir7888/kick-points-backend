"""Web dashboard for the Kick Points Miner.

A lightweight Flask dashboard (ported from the kick-points project) that
visualizes the state of every monitored channel. The monitor threads push
their status into a shared context; the dashboard only reads it, so it can
never break the mining loop.
"""

import threading
from datetime import datetime

from flask import Flask, render_template_string, jsonify
from loguru import logger
from .prediction_logger import prediction_logger

data_lock = threading.Lock()

# Shared state, updated by ChannelMonitor threads.
shared_context = {
    "status": "Initializing",  # Active / Initializing
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "channels": {},  # username -> info dict
    "total_messages_sent": 0,
    "total_errors": 0,
}

app = Flask(__name__)

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KICKMINER</title>
<style>
:root {
    --kick-green: #53fc18;
    --kick-green-dim: rgba(83, 252, 24, 0.15);
    --kick-green-border: rgba(83, 252, 24, 0.3);
    --bg-dark: #0b0e11;
    --bg-card: #191c21;
    --bg-card-hover: #1e2228;
    --bg-header: #12151a;
    --border-color: #2a2e35;
    --text-primary: #efeff1;
    --text-secondary: #adadb8;
    --text-muted: #7a7a85;
    --red: #f04747;
    --orange: #faa61a;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
    background: var(--bg-dark);
    color: var(--text-primary);
    min-height: 100vh;
}

.container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }

.header { text-align: center; padding: 32px 0 24px; }
.header h1 { font-size: 36px; font-weight: 800; letter-spacing: 2px; }
.header .kick-text { color: var(--kick-green); }
.header .subtitle {
    color: var(--text-muted);
    font-size: 13px;
    margin-top: 4px;
    letter-spacing: 1px;
    text-transform: uppercase;
}

.summary-bar {
    display: flex;
    justify-content: center;
    gap: 32px;
    padding: 16px 24px;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    margin-bottom: 24px;
    flex-wrap: wrap;
}

.summary-item { text-align: center; }
.summary-item .value { font-size: 24px; font-weight: 700; color: var(--kick-green); }
.summary-item .label {
    font-size: 11px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 2px;
}

.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
}

.status-active {
    background: var(--kick-green-dim);
    color: var(--kick-green);
    border: 1px solid var(--kick-green-border);
}

.status-init {
    background: rgba(250, 166, 26, 0.15);
    color: var(--orange);
    border: 1px solid rgba(250, 166, 26, 0.3);
}

.status-dot-pulse {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    animation: pulse 2s infinite;
}

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

.streamer-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
    padding: 4px 0;
}

.streamer-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 14px 16px;
    position: relative;
    transition: all 0.2s;
    overflow: hidden;
}

.streamer-card::before {
    content: '';
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 3px;
    background: var(--border-color);
    transition: background 0.2s;
}

.streamer-card.online::before { background: var(--kick-green); }
.streamer-card.offline::before { background: #444; }
.streamer-card.error::before { background: var(--red); }

.streamer-card:hover { background: var(--bg-card-hover); border-color: #3a3e45; }

.card-row-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
}

.streamer-info-left {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
}

.s-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.s-dot.online {
    background: var(--kick-green);
    box-shadow: 0 0 8px rgba(83, 252, 24, 0.5);
    animation: pulse 2s infinite;
}
.s-dot.offline { background: #555; }
.s-dot.error { background: var(--red); }

.s-name {
    font-weight: 600;
    font-size: 14px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.s-name a { color: var(--text-primary); text-decoration: none; transition: color 0.2s; }
.s-name a:hover { color: var(--kick-green); }

.s-errors {
    font-size: 10px;
    background: rgba(240, 71, 71, 0.15);
    color: var(--red);
    padding: 2px 6px;
    border-radius: 4px;
    flex-shrink: 0;
}

.card-row-bottom {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.s-points { font-size: 18px; font-weight: 700; color: var(--kick-green); }
.s-points .unit { font-size: 11px; color: var(--text-muted); font-weight: 500; }
.s-time { font-size: 11px; color: var(--text-muted); }

.prediction-info {
    margin-top: 8px;
    padding: 6px 8px;
    background: rgba(83, 252, 24, 0.1);
    border-left: 2px solid var(--kick-green);
    border-radius: 4px;
    font-size: 10px;
}

.prediction-info.unavailable {
    background: rgba(119, 119, 133, 0.1);
    border-left-color: var(--text-muted);
    color: var(--text-muted);
}

.prediction-title {
    font-weight: 600;
    margin-bottom: 2px;
}

.prediction-status {
    display: inline-block;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
}

.prediction-status.voted {
    background: var(--kick-green-dim);
    color: var(--kick-green);
}

.prediction-status.won {
    background: rgba(83, 252, 24, 0.25);
    color: var(--kick-green);
    border: 1px solid rgba(83, 252, 24, 0.4);
}

.prediction-status.lost {
    background: rgba(240, 71, 71, 0.25);
    color: var(--red);
    border: 1px solid rgba(240, 71, 71, 0.4);
}

.prediction-status.refunded {
    background: rgba(255, 255, 255, 0.1);
    color: var(--text-muted);
}

.prediction-status.failed {
    background: rgba(240, 71, 71, 0.15);
    color: var(--red);
}

.watch-btn {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    background: transparent;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 11px;
    font-weight: 600;
    transition: all 0.2s;
    flex-shrink: 0;
}
.watch-btn:hover {
    border-color: var(--kick-green);
    color: var(--kick-green);
    background: var(--kick-green-dim);
}
.watch-btn svg { width: 12px; height: 12px; }

.s-status-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.s-status-label.online { color: var(--kick-green); }
.s-status-label.offline { color: var(--text-muted); }
.s-status-label.error { color: var(--red); }

.grand-total {
    text-align: center;
    padding: 24px;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    margin: 24px 0;
}
.grand-total .label {
    color: var(--text-muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.grand-total .value { font-size: 40px; font-weight: 800; color: var(--kick-green); margin-top: 4px; }

.footer {
    text-align: center;
    padding: 16px 0 24px;
    color: var(--text-muted);
    font-size: 12px;
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
}
.refresh-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--kick-green);
    animation: pulse 2s infinite;
}

.empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
.empty-state .icon { font-size: 48px; margin-bottom: 12px; }
.empty-state .text { font-size: 16px; }

@media (max-width: 600px) {
    .header h1 { font-size: 28px; }
    .summary-bar { gap: 16px; padding: 12px 16px; }
    .summary-item .value { font-size: 18px; }
    .streamer-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="container">

    <div class="header">
        <h1><span class="kick-text">KICK</span>MINER</h1>
        <div class="subtitle">Channel Points Farmer</div>
    </div>

    <div class="summary-bar">
        <div class="summary-item">
            <div class="value" id="sum-status">—</div>
            <div class="label">Status</div>
        </div>
        <div class="summary-item">
            <div class="value" id="sum-channels">0</div>
            <div class="label">Channels</div>
        </div>
        <div class="summary-item">
            <div class="value" id="sum-online">0</div>
            <div class="label">Live Now</div>
        </div>
        <div class="summary-item">
            <div class="value" id="sum-total">0</div>
            <div class="label">Messages Sent</div>
        </div>
        <div class="summary-item">
            <div class="value" id="sum-errors">0</div>
            <div class="label">Errors</div>
        </div>
        <div class="summary-item">
            <div class="value" id="sum-predictions">0</div>
            <div class="label">Predictions Available</div>
        </div>
    </div>

    <div id="channels-container">
        <div class="empty-state">
            <div class="icon">⏳</div>
            <div class="text">Waiting for first channel check...</div>
        </div>
    </div>

    <div class="grand-total">
        <div class="label">Total Messages Sent</div>
        <div class="value" id="grand-total">0</div>
    </div>

    <div class="footer">
        <span class="refresh-dot"></span>
        <span>Auto-refresh every 5s · Started <span id="started-at">—</span> · Last update: <span id="refresh-time">--:--:--</span></span>
    </div>

</div>

<script>
const EXTERNAL_LINK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';

function fmtTime(v) {
    if (!v || v === "N/A" || v === "None" || v === "null") return "—";
    return v;
}

function fmtNumber(n) { return (n || 0).toLocaleString(); }

function render(data) {
    // Summary bar
    const statusEl = document.getElementById("sum-status");
    if (data.status === "Active") {
        statusEl.innerHTML = '<span class="status-badge status-active"><span class="status-dot-pulse"></span>ACTIVE</span>';
    } else {
        statusEl.innerHTML = '<span class="status-badge status-init"><span class="status-dot-pulse"></span>' + data.status.toUpperCase() + '</span>';
    }

    const channels = data.channels || {};
    const names = Object.keys(channels).sort((a, b) => {
        const st = { online: 0, error: 1, offline: 2 };
        return (st[channels[a].status] ?? 3) - (st[channels[b].status] ?? 3) || a.localeCompare(b);
    });

    let online = 0, predictionsAvailable = 0, cards = "";
    names.forEach(name => {
        const info = channels[name];
        const status = info.status || "offline";
        if (status === "online") online++;
        if (info.predictions_available) predictionsAvailable++;

        const lastMsg = info.last_message
            ? '<div class="s-time" title="Last message sent">💬 ' + info.last_message + '</div>'
            : '';

        // Prediction info section
        let predictionHtml = '';
        if (info.active_prediction) {
            const pred = info.active_prediction;
            const statusClass = pred.status || 'voted';
            const winnerText = pred.winning_outcome ? ` (Winner: ${pred.winning_outcome})` : '';
            const ptsWonText = pred.points_won > 0 ? ` (+${pred.points_won} pts)` : '';
            predictionHtml = `
                <div class="prediction-info">
                    <div class="prediction-title">${pred.title || 'Active Prediction'}</div>
                    <div>
                        ${pred.voted_outcome ? 'Voted: ' + pred.voted_outcome : 'Available to vote'}
                        ${pred.amount > 0 ? ' (' + pred.amount + ' pts)' : ''}${ptsWonText}
                        <span class="prediction-status ${statusClass}">${pred.status || 'active'}</span>${winnerText}
                    </div>
                </div>`;
        } else if (info.predictions_available) {
            predictionHtml = '<div class="prediction-info unavailable">No active predictions</div>';
        }

        const pwon = info.predictions_won || 0;
        const plost = info.predictions_lost || 0;
        const decided = pwon + plost;
        const winRateStr = decided > 0 ? ` (${Math.round((pwon / decided) * 100)}% WR)` : '';
        const predStatLine = info.predictions_voted
            ? `<div class="s-time" title="Prediction results">🎲 ${info.predictions_voted} votes • 🏆 ${pwon}W ❌ ${plost}L${winRateStr}</div>`
            : '';

        cards += `
        <div class="streamer-card ${status}">
            <div class="card-row-top">
                <div class="streamer-info-left">
                    <span class="s-dot ${status}"></span>
                    <span class="s-name"><a href="https://kick.com/${name}" target="_blank" rel="noopener">${name}</a></span>
                    ${info.errors > 0 ? '<span class="s-errors" title="Errors">' + info.errors + '</span>' : ''}
                </div>
                <a class="watch-btn" href="https://kick.com/${name}" target="_blank" rel="noopener">${EXTERNAL_LINK_SVG}Watch</a>
            </div>
            <div class="card-row-bottom">
                <div>
                    <div class="s-points">${fmtNumber(info.messages_sent)} <span class="unit">msgs</span></div>
                    ${predStatLine}
                    ${lastMsg}
                </div>
                <div style="text-align:right">
                    <div class="s-status-label ${status}">${status.toUpperCase()}</div>
                    <div class="s-time">${fmtTime(info.last_update)}</div>
                </div>
            </div>
            ${predictionHtml}
        </div>`;
    });

    const container = document.getElementById("channels-container");
    if (!names.length) {
        container.innerHTML = '<div class="empty-state"><div class="icon">⏳</div><div class="text">Waiting for first channel check...</div></div>';
    } else {
        container.innerHTML = '<div class="streamer-grid">' + cards + '</div>';
    }

    document.getElementById("sum-channels").textContent = names.length;
    document.getElementById("sum-online").textContent = online;
    document.getElementById("sum-total").textContent = fmtNumber(data.total_messages_sent);
    document.getElementById("sum-errors").textContent = fmtNumber(data.total_errors);
    document.getElementById("sum-predictions").textContent = predictionsAvailable;
    document.getElementById("grand-total").textContent = fmtNumber(data.total_messages_sent);
    document.getElementById("started-at").textContent = data.started_at || "—";
    document.getElementById("refresh-time").textContent = new Date().toLocaleTimeString();
}

async function refresh() {
    try {
        const res = await fetch("/api/data");
        const data = await res.json();
        render(data);
    } catch (e) { /* keep last good render */ }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/data")
def get_data():
    with data_lock:
        # Create a copy of channels data and ensure all channels have the active_predictions field
        channels_data = {}
        for username, channel_info in shared_context["channels"].items():
            channels_data[username] = dict(channel_info)  # Copy the dict
            # Ensure active_predictions field exists - set to True if there's an active prediction
            channels_data[username]["active_predictions"] = bool(
                channel_info.get("active_prediction")
            )

        return jsonify(
            {
                "status": shared_context["status"],
                "started_at": shared_context["started_at"],
                "channels": channels_data,
                "total_messages_sent": shared_context["total_messages_sent"],
                "total_errors": shared_context["total_errors"],
            }
        )


@app.route("/health")
def health():
    return jsonify({"ok": True, "status": shared_context["status"]})


@app.route("/api/predictions")
def get_predictions():
    """Get prediction statistics and recent events."""
    try:
        stats = prediction_logger.get_prediction_stats()
        recent_events = prediction_logger.get_recent_predictions(hours=24)

        return jsonify(
            {
                "stats": stats,
                "recent_events": recent_events[:20],  # Last 20 events
                "success": True,
            }
        )
    except Exception as e:
        logger.error(f"Error fetching prediction data: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/predictions/<username>")
def get_channel_predictions(username: str):
    """Get prediction data for a specific channel."""
    try:
        stats = prediction_logger.get_prediction_stats(username)
        recent_events = prediction_logger.get_recent_predictions(
            username, hours=168
        )  # Last week

        return jsonify(
            {
                "username": username,
                "stats": stats,
                "recent_events": recent_events,
                "success": True,
            }
        )
    except Exception as e:
        logger.error(f"Error fetching prediction data for {username}: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/debug/<username>")
def debug_channel_data(username: str):
    """Debug endpoint to inspect raw channel data."""
    try:
        # We need to access the client from somewhere - let's create a temporary one
        # This is a bit hacky but for debugging purposes it's fine
        from .config import Config
        from .client import KickClient

        config = Config.load()
        client = KickClient(config.authorization)

        channel_data = client.debug_channel_data(username)
        if channel_data:
            return jsonify(
                {"username": username, "channel_data": channel_data, "success": True}
            )
        else:
            return jsonify(
                {
                    "username": username,
                    "error": "No channel data found",
                    "success": False,
                }
            ), 404

    except Exception as e:
        logger.error(f"Error debugging channel data for {username}: {e}")
        return jsonify({"error": str(e), "success": False}), 500


def register_channel(username: str) -> None:
    """Add a channel to the dashboard (shown as pending until first check)."""
    with data_lock:
        shared_context["channels"].setdefault(
            username,
            {
                "status": "offline",
                "messages_sent": 0,
                "errors": 0,
                "last_update": "N/A",
                "last_message": None,
                "predictions_voted": 0,
                "predictions_won": 0,
                "predictions_lost": 0,
                "predictions_refunded": 0,
                "predictions_available": False,
                "active_prediction": None,
                "prediction_history": [],
            },
        )


def update_channel(
    username: str,
    status: str,
    sent_message: str | None = None,
    prediction_info: dict | None = None,
) -> None:
    """Update dashboard state for one channel after a check.

    status == "prediction_vote" increments the prediction counter and logs the vote
    without changing the channel's online/offline status.
    """
    with data_lock:
        info = shared_context["channels"].setdefault(
            username,
            {
                "status": "offline",
                "messages_sent": 0,
                "predictions_voted": 0,
                "predictions_won": 0,
                "predictions_lost": 0,
                "predictions_refunded": 0,
                "errors": 0,
                "last_update": "N/A",
                "last_message": None,
                "predictions_available": False,
                "active_prediction": None,
                "prediction_history": [],
            },
        )

        if status == "prediction_vote":
            info["predictions_voted"] += 1
            info["last_update"] = datetime.now().strftime("%H:%M:%S")
            if prediction_info:
                info["active_prediction"] = prediction_info
                info["predictions_available"] = True
                # Add to history (keep last 10)
                history_entry = {
                    "id": prediction_info.get("id"),
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "title": prediction_info.get("title", "Unknown"),
                    "voted_outcome": prediction_info.get("voted_outcome"),
                    "amount": prediction_info.get("amount", 0),
                    "status": prediction_info.get("status", "unknown"),
                }
                info["prediction_history"].insert(0, history_entry)
                info["prediction_history"] = info["prediction_history"][:10]
            return

        info["status"] = status
        info["last_update"] = datetime.now().strftime("%H:%M:%S")
        if sent_message:
            info["messages_sent"] += 1
            info["last_message"] = sent_message
            shared_context["total_messages_sent"] += 1
        if status == "error":
            info["errors"] += 1
            shared_context["total_errors"] += 1


def record_prediction_result(username: str, result_info: dict) -> None:
    """Record win/loss result of a prediction for a channel."""
    with data_lock:
        info = shared_context["channels"].get(username)
        if not info:
            return

        res = result_info.get("result", "").lower()
        if res in ("won", "win"):
            info["predictions_won"] = info.get("predictions_won", 0) + 1
        elif res in ("lost", "lose"):
            info["predictions_lost"] = info.get("predictions_lost", 0) + 1
        elif res in ("refunded", "cancelled", "canceled"):
            info["predictions_refunded"] = info.get("predictions_refunded", 0) + 1

        pred_id = result_info.get("id")

        # Update entry in history
        for h in info.get("prediction_history", []):
            if (
                not pred_id
                or h.get("id") == pred_id
                or h.get("title") == result_info.get("title")
            ):
                h["status"] = res
                h["winning_outcome"] = result_info.get("winning_outcome")
                h["points_won"] = result_info.get("points_won", 0)
                break

        # Update active prediction status
        if info.get("active_prediction"):
            if not pred_id or info["active_prediction"].get("id") == pred_id:
                info["active_prediction"]["status"] = res
                info["active_prediction"]["winning_outcome"] = result_info.get(
                    "winning_outcome"
                )
                info["active_prediction"]["points_won"] = result_info.get(
                    "points_won", 0
                )

        info["last_update"] = datetime.now().strftime("%H:%M:%S")


def update_channel_prediction_info(username: str, prediction_info: dict) -> None:
    """Update prediction info for a channel without changing vote count."""
    with data_lock:
        info = shared_context["channels"].get(username)
        if info:
            info["active_prediction"] = prediction_info
            info["predictions_available"] = True
            info["last_update"] = datetime.now().strftime("%H:%M:%S")


def update_channel_prediction_availability(
    username: str, has_predictions: bool
) -> None:
    """Update whether a channel has active predictions available."""
    with data_lock:
        info = shared_context["channels"].get(username)
        if info:
            info["predictions_available"] = has_predictions
            if not has_predictions:
                info["active_prediction"] = None
            info["last_update"] = datetime.now().strftime("%H:%M:%S")


def set_active() -> None:
    with data_lock:
        shared_context["status"] = "Active"


def start_server(port: int = 5000) -> None:
    """Start the dashboard in a background thread (never blocks mining)."""

    def run() -> None:
        import logging

        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        logger.info(f"Web Dashboard available at http://localhost:{port}")
        app.run(
            host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True
        )

    threading.Thread(target=run, daemon=True, name="web-dashboard").start()
