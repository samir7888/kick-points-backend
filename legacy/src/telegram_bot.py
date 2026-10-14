"""Telegram Bot for Kick Points Miner.

Commands:
  /start        - Welcome message & command list
  /status       - Full miner status (all channels)
  /channels     - Live channels overview
  /channel <n>  - Status for a specific channel
  /predictions  - 7-day prediction stats (all channels)
  /pred <n>     - Prediction stats for a specific channel
  /wake         - Start keep-alive pings (prevents Render sleep)
  /sleep        - Stop keep-alive pings
  /ping         - Quick health-check ping
  /help         - Show all commands
"""

import asyncio
import html
import os
import threading
import time
from datetime import datetime
from typing import Optional

import httpx
from loguru import logger
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ── shared miner state (set after miner boots) ────────────────────────────────
_miner_ref = None  # PointsMiner instance
_keepalive_active = False  # Render keep-alive toggle
_keepalive_thread: Optional[threading.Thread] = None
_bot_app: Optional[Application] = None


def set_miner(miner) -> None:
    global _miner_ref
    _miner_ref = miner


# ── helpers ───────────────────────────────────────────────────────────────────


def _get_shared_context() -> dict:
    """Pull live state from web_server's shared_context."""
    try:
        from . import web_server

        return dict(web_server.shared_context)
    except Exception:
        return {}


def _status_emoji(status: str) -> str:
    return {"online": "🟢", "offline": "⚫", "error": "🔴"}.get(status, "⚪")


def _fmt_number(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_time(t: str) -> str:
    if not t or t == "N/A":
        return "—"
    return str(t)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _esc(s: any) -> str:
    return html.escape(str(s) if s is not None else "")


# ── Render keep-alive ─────────────────────────────────────────────────────────


def _keepalive_worker(service_url: str, interval_s: int = 840) -> None:
    """Ping the service every ~14 min to prevent Render free tier sleep."""
    global _keepalive_active
    logger.info(f"[KeepAlive] Started — pinging {service_url} every {interval_s}s")
    while _keepalive_active:
        try:
            r = httpx.get(f"{service_url}/health", timeout=10)
            logger.debug(f"[KeepAlive] Ping OK ({r.status_code})")
        except Exception as e:
            logger.warning(f"[KeepAlive] Ping failed: {e}")
        # sleep in 30-second chunks so we can exit quickly on stop
        for _ in range(max(1, interval_s // 30)):
            if not _keepalive_active:
                break
            time.sleep(30)
    logger.info("[KeepAlive] Stopped")


# ── Command handlers (Using HTML formatting for guaranteed safety) ────────────


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🟢 <b>Kick Points Miner — Telegram Bot</b>\n\n"
        "Welcome! Use these commands to monitor and control your miner:\n\n"
        "📊 <b>Miner & Channel Info</b>\n"
        "• /status — Full miner status & channels breakdown\n"
        "• /channels — Quick overview of all channels\n"
        "• /channel &lt;name&gt; — Detailed status of a single channel\n"
        "• /predictions — 7-day prediction stats & recent bets\n"
        "• /pred &lt;name&gt; — Prediction history for a specific channel\n"
        "• /ping — Quick health check ping\n\n"
        "⚙️ <b>Render / Hosting Controls</b>\n"
        "• /wake — Start keep-alive pings (prevents Render instance from sleeping)\n"
        "• /sleep — Stop keep-alive pings\n\n"
        "Type /help anytime to see this list again."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx_data = _get_shared_context()
    status = _esc(ctx_data.get("status", "Unknown"))
    started = _esc(ctx_data.get("started_at", "—"))
    text = (
        f"✅ <b>Pong!</b>\n\n"
        f"<b>Status:</b> <code>{status}</code>\n"
        f"<b>Started:</b> <code>{started}</code>\n"
        f"<b>Time:</b> <code>{_now()}</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx_data = _get_shared_context()
    channels: dict = ctx_data.get("channels", {})

    online = sum(1 for c in channels.values() if c.get("status") == "online")
    offline = sum(1 for c in channels.values() if c.get("status") == "offline")
    errors = sum(1 for c in channels.values() if c.get("status") == "error")
    total_msgs = ctx_data.get("total_messages_sent", 0)
    total_errs = ctx_data.get("total_errors", 0)
    started = _esc(ctx_data.get("started_at", "—"))
    miner_status = _esc(ctx_data.get("status", "Unknown"))
    keepalive_txt = "🏃 Running" if _keepalive_active else "💤 Off"

    from .prediction_logger import prediction_logger

    p_stats = prediction_logger.get_prediction_stats()
    votes_placed = p_stats.get("votes_placed", 0)
    votes_won = p_stats.get("votes_won", 0)
    votes_lost = p_stats.get("votes_lost", 0)
    votes_ref = p_stats.get("votes_refunded", 0)
    win_rate = p_stats.get("win_rate", 0.0)

    lines = [
        "🟢 <b>Kick Miner Status</b>",
        "",
        f"🔋 <b>Miner:</b> <code>{miner_status}</code>",
        f"📅 <b>Started:</b> <code>{started}</code>",
        f"🏓 <b>Keep-alive:</b> {keepalive_txt}",
        "",
        f"📡 <b>Channels ({len(channels)} total)</b>",
        f"  🟢 Online:  {online}",
        f"  ⚫ Offline: {offline}",
        f"  🔴 Errors:  {errors}",
        "",
        f"🎲 <b>Predictions:</b> <code>{votes_placed}</code> placed (🏆 <code>{votes_won}</code>W / ❌ <code>{votes_lost}</code>L / 🔄 <code>{votes_ref}</code>)",
        f"📈 <b>Prediction Win Rate:</b> <code>{win_rate}%</code>",
        "",
        f"📨 <b>Messages sent:</b> <code>{_fmt_number(total_msgs)}</code>",
        f"⚠️ <b>Total errors:</b> <code>{_fmt_number(total_errs)}</code>",
        f"🕐 <b>Report time:</b> <code>{_now()}</code>",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_channels(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx_data = _get_shared_context()
    channels: dict = ctx_data.get("channels", {})

    if not channels:
        await update.message.reply_text("No channels registered yet.")
        return

    order = {"online": 0, "error": 1, "offline": 2}
    sorted_ch = sorted(
        channels.items(), key=lambda x: order.get(x[1].get("status", ""), 3)
    )

    lines = ["📡 <b>Channel Overview</b>\n"]
    for name, info in sorted_ch:
        st = info.get("status", "offline")
        emoji = _status_emoji(st)
        msgs = _fmt_number(info.get("messages_sent", 0))
        pvotes = info.get("predictions_voted", 0)
        pwon = info.get("predictions_won", 0)
        plost = info.get("predictions_lost", 0)
        pred_txt = f" | 🎲 {pvotes} votes (🏆{pwon}W ❌{plost}L)" if pvotes else ""
        active_pred = info.get("active_prediction")
        pred_badge = ""
        if active_pred:
            pred_badge = f"\n  📌 <i>{_esc(active_pred.get('title', 'Prediction'))}</i> → <code>{_esc(active_pred.get('voted_outcome', '?'))}</code>"

        lines.append(
            f"{emoji} <b>{_esc(name)}</b> — <code>{st.upper()}</code>\n"
            f"  📨 {msgs} msgs{pred_txt}\n"
            f"  🕐 {_fmt_time(info.get('last_update', 'N/A'))}"
            f"{pred_badge}"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "<b>Usage:</b> /channel &lt;username&gt;\n<i>Example:</i> /channel anshyt",
            parse_mode=ParseMode.HTML,
        )
        return

    name = ctx.args[0].lower().strip()
    ctx_data = _get_shared_context()
    channels: dict = ctx_data.get("channels", {})

    info = channels.get(name)
    if not info:
        known = ", ".join(f"<code>{_esc(k)}</code>" for k in channels.keys()) or "none"
        await update.message.reply_text(
            f"Channel <code>{_esc(name)}</code> not found.\nMonitored channels: {known}",
            parse_mode=ParseMode.HTML,
        )
        return

    st = info.get("status", "offline")
    emoji = _status_emoji(st)
    msgs = _fmt_number(info.get("messages_sent", 0))
    pvotes = info.get("predictions_voted", 0)
    pwon = info.get("predictions_won", 0)
    plost = info.get("predictions_lost", 0)
    prefunded = info.get("predictions_refunded", 0)
    errors = info.get("errors", 0)
    last_msg = _esc(info.get("last_message", "—"))
    last_upd = _esc(_fmt_time(info.get("last_update", "N/A")))
    active_pred = info.get("active_prediction")

    decided = pwon + plost
    ch_winrate = round((pwon / decided * 100), 1) if decided > 0 else 0.0

    pred_lines = []
    if active_pred:
        pred_lines = [
            "",
            "📌 <b>Active Prediction</b>",
            f"  Title: <i>{_esc(active_pred.get('title', 'Unknown'))}</i>",
            f"  Voted: <code>{_esc(active_pred.get('voted_outcome', '?'))}</code>",
            f"  Points: <code>{active_pred.get('amount', 0)}</code>",
            f"  Status: <code>{_esc(active_pred.get('status', '?'))}</code>",
        ]

    history = info.get("prediction_history", [])[:5]
    hist_lines = []
    if history:
        hist_lines = ["", "🗂️ <b>Recent Prediction History</b>"]
        for h in history:
            hst = str(h.get("status", "unknown")).lower()
            if hst in ("won", "win"):
                badge = f"🏆 WON (+{h.get('points_won', 0)} pts)"
            elif hst in ("lost", "lose"):
                badge = "❌ LOST"
            elif hst in ("refunded", "cancelled"):
                badge = "🔄 REFUNDED"
            else:
                badge = "⏳ PENDING / ACTIVE"

            hist_lines.append(
                f"  • <i>{_esc(h.get('title', '?'))}</i> → <code>{_esc(h.get('voted_outcome', '?'))}</code> ({h.get('amount', 0)} pts) — {badge}"
            )

    lines = (
        [
            f"{emoji} <b>{_esc(name)}</b> — <code>{st.upper()}</code>",
            "",
            f"📨 <b>Messages Sent:</b> <code>{msgs}</code>",
            f"🎲 <b>Votes Placed:</b> <code>{pvotes}</code>",
            f"🏆 <b>Votes Won:</b> <code>{pwon}</code>",
            f"❌ <b>Votes Lost:</b> <code>{plost}</code>",
            f"🔄 <b>Refunded:</b> <code>{prefunded}</code>",
            f"📈 <b>Win Rate:</b> <code>{ch_winrate}%</code>",
            f"⚠️ <b>Errors:</b> <code>{errors}</code>",
            f"💬 <b>Last Message:</b> <code>{last_msg}</code>",
            f"🕐 <b>Last Update:</b> <code>{last_upd}</code>",
        ]
        + pred_lines
        + hist_lines
    )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_predictions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from .prediction_logger import prediction_logger

    try:
        stats = prediction_logger.get_prediction_stats()
        recent = prediction_logger.get_recent_predictions(hours=24)[:6]

        channels_txt = (
            ", ".join(f"<code>{_esc(c)}</code>" for c in stats.get("channels", []))
            or "none"
        )

        lines = [
            "🎲 <b>Prediction Stats (Last 7 Days)</b>",
            "",
            f"📊 <b>Votes Placed:</b> <code>{stats.get('votes_placed', 0)}</code>",
            f"🏆 <b>Votes Won:</b> <code>{stats.get('votes_won', 0)}</code>",
            f"❌ <b>Votes Lost:</b> <code>{stats.get('votes_lost', 0)}</code>",
            f"🔄 <b>Refunded / Cancelled:</b> <code>{stats.get('votes_refunded', 0)}</code>",
            f"📈 <b>Win Rate:</b> <code>{stats.get('win_rate', 0.0)}%</code>",
            f"🪙 <b>Points Bet:</b> <code>{_fmt_number(stats.get('total_points_bet', 0))}</code>",
            f"🪙 <b>Points Won:</b> <code>{_fmt_number(stats.get('total_points_won', 0))}</code>",
            f"📡 <b>Channels:</b> {channels_txt}",
        ]

        if recent:
            lines += ["", "🕐 <b>Recent Predictions Activity</b>"]
            for ev in recent:
                et = ev.get("event_type", "?")
                uname = _esc(ev.get("username", "?"))
                pred = ev.get("prediction", {})
                title = _esc(pred.get("title", "?"))
                ts = _esc(ev.get("timestamp", "")[:16].replace("T", " "))
                info = ev.get("additional_info", {})

                if et == "prediction_result":
                    res = info.get("result", "")
                    if res == "won":
                        badge = f"🏆 WON (+{info.get('points_won', 0)} pts)"
                    elif res == "lost":
                        badge = "❌ LOST"
                    else:
                        badge = "🔄 REFUNDED"
                    lines.append(
                        f"  • <code>{ts}</code> {uname} — <i>{title}</i> → {badge}"
                    )
                elif et == "voted":
                    outcome = _esc(info.get("voted_outcome", ""))
                    lines.append(
                        f"  • <code>{ts}</code> {uname} — <i>{title}</i> (Voted: <code>{outcome}</code>)"
                    )
                else:
                    lines.append(
                        f"  • <code>{ts}</code> {uname} — <i>{title}</i> ({et})"
                    )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Telegram cmd_predictions error: {e}")
        await update.message.reply_text(f"Error fetching stats: {e}")


async def cmd_pred_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from .prediction_logger import prediction_logger

    if not ctx.args:
        await update.message.reply_text(
            "<b>Usage:</b> /pred &lt;username&gt;\n<i>Example:</i> /pred anshyt",
            parse_mode=ParseMode.HTML,
        )
        return

    name = ctx.args[0].lower().strip()
    try:
        stats = prediction_logger.get_prediction_stats(name)
        recent = prediction_logger.get_recent_predictions(name, hours=168)[:6]

        decided = stats.get("votes_won", 0) + stats.get("votes_lost", 0)
        wr = (
            round((stats.get("votes_won", 0) / decided * 100), 1)
            if decided > 0
            else 0.0
        )

        lines = [
            f"🎲 <b>Predictions for {_esc(name)} (7 days)</b>",
            "",
            f"📊 <b>Votes Placed:</b> <code>{stats.get('votes_placed', 0)}</code>",
            f"🏆 <b>Votes Won:</b> <code>{stats.get('votes_won', 0)}</code>",
            f"❌ <b>Votes Lost:</b> <code>{stats.get('votes_lost', 0)}</code>",
            f"🔄 <b>Refunded:</b> <code>{stats.get('votes_refunded', 0)}</code>",
            f"📈 <b>Win Rate:</b> <code>{wr}%</code>",
            f"🪙 <b>Points Bet:</b> <code>{_fmt_number(stats.get('total_points_bet', 0))}</code>",
            f"🪙 <b>Points Won:</b> <code>{_fmt_number(stats.get('total_points_won', 0))}</code>",
        ]

        if recent:
            lines += ["", "📋 <b>Recent Events</b>"]
            for ev in recent:
                et = ev.get("event_type", "?")
                pred = ev.get("prediction", {})
                title = _esc(pred.get("title", "?"))
                ts = _esc(ev.get("timestamp", "")[:16].replace("T", " "))
                info = ev.get("additional_info", {})
                if et == "prediction_result":
                    res = info.get("result", "")
                    badge = (
                        "🏆 WON"
                        if res == "won"
                        else ("❌ LOST" if res == "lost" else "🔄 REFUNDED")
                    )
                    lines.append(f"  {badge} <code>{ts}</code> — <i>{title}</i>")
                elif et == "voted":
                    outcome = _esc(info.get("voted_outcome", ""))
                    lines.append(
                        f"  🗳️ <code>{ts}</code> — <i>{title}</i> → <code>{outcome}</code>"
                    )
                else:
                    lines.append(f"  • <code>{ts}</code> — <i>{title}</i> ({et})")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Telegram cmd_pred_channel error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def cmd_wake(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global _keepalive_active, _keepalive_thread

    if _keepalive_active:
        await update.message.reply_text(
            "🏃 <b>Keep-alive is already running!</b>\n"
            f"Currently pinging every {int(os.environ.get('KEEPALIVE_INTERVAL', '840')) // 60} minutes.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Try multiple environment variables for service URL, with your Render URL as primary
    service_url = (
        os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("SERVICE_URL")
        or os.environ.get("RAILWAY_STATIC_URL")  # Railway support
        or os.environ.get("VERCEL_URL")  # Vercel support
        or "https://kick-points.onrender.com"  # Your Render URL as default
        or "http://localhost:4000"  # Local fallback
    ).rstrip("/")

    # If it's a relative URL from Vercel, add https://
    if service_url.startswith("//"):
        service_url = "https:" + service_url
    elif service_url and not service_url.startswith(("http://", "https://")):
        service_url = "https://" + service_url

    interval = int(os.environ.get("KEEPALIVE_INTERVAL", "840"))

    try:
        # Test the URL first
        test_response = None
        try:
            import httpx

            with httpx.Client(timeout=10) as client:
                test_response = client.get(f"{service_url}/health")
            logger.info(
                f"[KeepAlive] Test ping successful: {service_url}/health -> {test_response.status_code}"
            )
        except Exception as e:
            logger.warning(f"[KeepAlive] Test ping failed: {e}")
            # Continue anyway, might work during actual pings

        _keepalive_active = True
        _keepalive_thread = threading.Thread(
            target=_keepalive_worker,
            args=(service_url, interval),
            daemon=True,
            name="tg-keepalive",
        )
        _keepalive_thread.start()

        status_text = (
            "✅ Test ping successful"
            if test_response and test_response.status_code == 200
            else "⚠️ Test ping failed, but will keep trying"
        )

        await update.message.reply_text(
            f"🏃 <b>Keep-alive started!</b>\n\n"
            f"🌐 <b>URL:</b> <code>{service_url}/health</code>\n"
            f"⏰ <b>Interval:</b> Every {interval // 60} minutes ({interval}s)\n"
            f"🔍 <b>Status:</b> {status_text}\n\n"
            f"This will keep your Render service awake by pinging it regularly.\n"
            f"Use /sleep to stop the keep-alive pings.",
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.error(f"[KeepAlive] Failed to start: {e}")
        await update.message.reply_text(
            f"❌ <b>Failed to start keep-alive:</b>\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_sleep(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global _keepalive_active

    if not _keepalive_active:
        await update.message.reply_text(
            "💤 <b>Keep-alive is already stopped.</b>\n"
            "Your service will sleep after Render's inactivity timeout (usually ~15 minutes).",
            parse_mode=ParseMode.HTML,
        )
        return

    _keepalive_active = False
    await update.message.reply_text(
        "💤 <b>Keep-alive stopped successfully!</b>\n\n"
        "🔻 The service will now sleep after Render's inactivity timeout.\n"
        "🔄 Use /wake to restart keep-alive pings anytime.\n\n"
        "⏰ <b>Note:</b> Render free tier sleeps after ~15 minutes of no requests.",
        parse_mode=ParseMode.HTML,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error(f"Telegram error handling update {update}: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"⚠️ Error processing command: <code>{_esc(context.error)}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


# ── Bot startup ───────────────────────────────────────────────────────────────


async def _post_init(application: Application) -> None:
    """Register bot commands in Telegram menu."""
    await application.bot.set_my_commands(
        [
            BotCommand("status", "Full miner status"),
            BotCommand("channels", "Live channel overview"),
            BotCommand("channel", "Single channel details"),
            BotCommand("predictions", "7-day prediction stats"),
            BotCommand("pred", "Channel prediction stats"),
            BotCommand("ping", "Quick health check"),
            BotCommand("wake", "Start Render keep-alive"),
            BotCommand("sleep", "Stop Render keep-alive"),
            BotCommand("help", "Show all commands"),
        ]
    )
    logger.info("Telegram bot commands registered.")


def start_bot(token: str, allowed_users: list[int] | None = None) -> None:
    """Start the Telegram bot in a background thread."""
    global _bot_app

    if not token:
        logger.warning("No Telegram bot token configured — bot disabled.")
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        app = Application.builder().token(token).post_init(_post_init).build()
        _bot_app = app

        # Register handlers
        for cmd, handler in [
            ("start", cmd_start),
            ("help", cmd_help),
            ("ping", cmd_ping),
            ("status", cmd_status),
            ("channels", cmd_channels),
            ("channel", cmd_channel),
            ("predictions", cmd_predictions),
            ("pred", cmd_pred_channel),
            ("wake", cmd_wake),
            ("sleep", cmd_sleep),
        ]:
            app.add_handler(CommandHandler(cmd, handler))

        app.add_error_handler(error_handler)

        logger.info("Telegram bot polling started.")
        app.run_polling(stop_signals=None)

    t = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    t.start()
    logger.info("Telegram bot thread launched.")
