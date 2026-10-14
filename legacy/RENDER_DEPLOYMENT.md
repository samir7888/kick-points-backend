# 🚀 Render Deployment with Secret Files

This guide shows how to deploy your Kick Points Miner to Render using secret files (more secure than environment variables).

## 📋 Your Current Setup

- **Render URL**: `https://kick-points.onrender.com`
- **Configuration**: Using `config.json` as a secret file
- **Tokens**: All sensitive data stored securely in secret files

## 🔧 Render Configuration

### 1. Web Service Settings

- **Name**: `kick-points`
- **Runtime**: `Docker`
- **Plan**: `Free`
- **Port**: `4000` (automatically detected from Dockerfile)

### 2. Environment Variables (Render Dashboard)

Set these in your Render service's Environment tab:

```
RENDER_EXTERNAL_URL=https://kick-points.onrender.com
KEEPALIVE_INTERVAL=840
TZ=UTC
```

### 3. Secret Files (Render Dashboard)

In your Render service's Settings → Secret Files:

**File**: `config.json`
**Contents**: Your complete config.json file (copy the entire content from your local file)

## 🤖 Testing Your Deployment

### 1. Check Service Health

Visit: `https://kick-points.onrender.com/health`

You should see: `{"ok":true,"status":"Active"}`

### 2. Check Web Dashboard

Visit: `https://kick-points.onrender.com`

You should see your channels and their status.

### 3. Test Telegram Bot

1. Find your bot: `@kickkkkkk_bot` (or your bot username)
2. Send `/start` - should get welcome message
3. Send `/ping` - should get status response
4. Send `/status` - should show miner status

## 🏓 Enable Keep-Alive (Prevent Sleep)

### Start Keep-Alive

Send `/wake` to your Telegram bot. You should see:

```
🏃 Keep-alive started!

🌐 URL: https://kick-points.onrender.com/health
⏰ Interval: Every 14 minutes (840s)
🔍 Status: ✅ Test ping successful

This will keep your Render service awake by pinging it regularly.
Use /sleep to stop the keep-alive pings.
```

### Verify Keep-Alive

1. **Check bot status**: Send `/status` - should show "Keep-alive: 🏃 Running"
2. **Check Render logs**: Look for `[KeepAlive] Ping OK (200)` every 14 minutes
3. **Monitor uptime**: Your service should stay awake 24/7

## 📊 Monitoring Commands

### Telegram Bot Commands

- `/status` - Full miner status & keep-alive status
- `/channels` - Overview of all channels
- `/predictions` - Prediction stats
- `/wake` - Start keep-alive (prevents sleep)
- `/sleep` - Stop keep-alive
- `/ping` - Quick health check

### Web Dashboard

- **URL**: `https://kick-points.onrender.com`
- **Features**: Live channel status, message counts, prediction stats
- **Auto-refresh**: Every 5 seconds

## 🔧 Troubleshooting

### Problem: Service not responding

**Check:**

1. Render deployment logs for errors
2. Verify `config.json` secret file is properly uploaded
3. Ensure all tokens in config.json are valid

### Problem: Telegram bot not working

**Check:**

1. Telegram bot token is correct in `config.json`
2. Bot is started (send `/start` first)
3. Check Render logs for Telegram-related errors

### Problem: Keep-alive not working

**Solutions:**

1. Send `/wake` to start keep-alive
2. Check that `RENDER_EXTERNAL_URL` environment variable is set
3. Verify `/health` endpoint works: `https://kick-points.onrender.com/health`

### Problem: Channels not showing activity

**Check:**

1. Kick authorization token is valid and not expired
2. Channel names are correct (case-sensitive)
3. Streamers are actually live

## 📝 Updating Configuration

### To Update config.json:

1. Go to Render Dashboard → Your Service → Settings → Secret Files
2. Edit the `config.json` file
3. Save changes
4. Render will automatically redeploy with new config

### Common Updates:

- **Add/remove channels**: Update the `channels` array
- **Change prediction amount**: Update `prediction.amount`
- **Update tokens**: Replace `authorization` or `telegram.bot_token`
- **Modify wait times**: Adjust `wait_times` values

## ✅ Deployment Checklist

- [ ] Service deployed on Render successfully
- [ ] `config.json` uploaded as secret file
- [ ] Environment variables set (RENDER_EXTERNAL_URL, etc.)
- [ ] Web dashboard accessible and showing channels
- [ ] Telegram bot responding to `/start` and `/ping`
- [ ] Keep-alive started with `/wake` command
- [ ] Keep-alive pings visible in Render logs
- [ ] Channels showing activity in dashboard

## 🔄 Service Management

### Restart Service

- Go to Render Dashboard → Manual Deploy → Deploy Latest Commit

### View Logs

- Render Dashboard → Logs tab
- Look for miner activity, keep-alive pings, and any errors

### Check Resource Usage

- Render Dashboard → Metrics tab
- Monitor CPU, memory, and network usage

## 🚀 Your Service Status

**URL**: https://kick-points.onrender.com
**Bot**: @kickkkkkk_bot (based on your token)
**Status**: Ready for keep-alive activation

Send `/wake` to your bot to start 24/7 operation!
