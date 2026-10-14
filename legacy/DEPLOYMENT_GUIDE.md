# 🚀 Render Deployment Guide

This guide will help you deploy the Kick Points Miner to Render's free tier with proper keep-alive functionality.

## 📋 Prerequisites

1. **GitHub Repository**: Your code should be in a GitHub repository
2. **Render Account**: Sign up at [render.com](https://render.com)
3. **Telegram Bot**: Get a bot token from [@BotFather](https://t.me/botfather)
4. **Kick Authorization**: Get your Bearer token from Kick.com

## 🔑 Getting Your Kick Authorization Token

1. Open any Kick.com livestream while logged in
2. Open Developer Tools (F12) → **Network** tab
3. Send a message in chat
4. Look for a request to `messages/send`
5. Copy the `Authorization` header value (starts with `Bearer ...`)

## 🛠️ Render Deployment Steps

### 1. Create a New Web Service

1. Go to [Render Dashboard](https://dashboard.render.com)
2. Click **New** → **Web Service**
3. Connect your GitHub repository
4. Configure the service:
   - **Name**: `kick-points-miner` (or your choice)
   - **Region**: Choose closest to you
   - **Branch**: `main`
   - **Runtime**: `Docker`
   - **Plan**: `Free` (for free tier)

### 2. Set Environment Variables

In the **Environment** section, add these variables:

#### Required Variables:

```
CONFIG_JSON={"channels":["pekkaaaplays","anshyt","mafianinja","killeryttt"],"wait_times":{"livestream_active":{"min":120,"max":300},"livestream_inactive":600,"error_wait":180},"authorization":"Bearer YOUR_KICK_BEARER_TOKEN_HERE","messages":["[emote:1730752:emojiAngel]","[emote:1730756:emojiCheerful]","[emote:1730787:emojiHappy]","[emote:1730834:emojiYay]"],"web_dashboard":{"enabled":true,"port":4000},"prediction":{"enabled":true,"amount":50},"telegram":{"bot_token":"YOUR_TELEGRAM_BOT_TOKEN_HERE","allowed_user_ids":[]}}
```

#### Optional Variables:

```
KEEPALIVE_INTERVAL=840
PORT=4000
TZ=UTC
```

### 3. Important Configuration Notes

- **Replace `YOUR_KICK_BEARER_TOKEN_HERE`** with your actual Bearer token
- **Replace `YOUR_TELEGRAM_BOT_TOKEN_HERE`** with your actual bot token
- **Update the channels list** with your preferred streamers
- **Update `allowed_user_ids`** with your Telegram user ID (optional, for security)

### 4. Deploy

1. Click **Create Web Service**
2. Wait for the deployment to complete (5-10 minutes)
3. Note your service URL: `https://your-app-name.onrender.com`

## 🤖 Telegram Bot Setup

### 1. Find Your Telegram User ID (Optional)

Send a message to your bot, then visit:

```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

Look for your `user_id` in the response.

### 2. Test the Bot

1. Find your bot on Telegram (`@your_bot_username`)
2. Send `/start` to see the welcome message
3. Send `/ping` to test connectivity
4. Send `/status` to see your miner status

## 🏓 Keep-Alive Setup (Prevent Free Tier Sleep)

### 1. Start Keep-Alive

Send `/wake` to your Telegram bot. You should see:

```
🏃 Keep-alive started!

🌐 URL: https://your-app-name.onrender.com/health
⏰ Interval: Every 14 minutes (840s)
🔍 Status: ✅ Test ping successful

This will keep your Render service awake by pinging it regularly.
Use /sleep to stop the keep-alive pings.
```

### 2. Verify Keep-Alive is Working

- Check the logs in Render dashboard
- Look for `[KeepAlive] Ping OK (200)` messages every 14 minutes
- Your service should stay awake 24/7

### 3. Stop Keep-Alive (if needed)

Send `/sleep` to your bot to stop the pings and allow the service to sleep.

## 📊 Monitoring

### Web Dashboard

Visit: `https://your-app-name.onrender.com`

### Telegram Commands

- `/status` - Full miner status
- `/channels` - Channel overview
- `/predictions` - Prediction stats
- `/wake` - Start keep-alive
- `/sleep` - Stop keep-alive

## 🔧 Troubleshooting

### Problem: Bot doesn't respond to `/wake`

**Solutions:**

1. Check if `TELEGRAM_BOT_TOKEN` is set correctly in Render environment variables
2. Verify your bot token is valid
3. Make sure your service is running (check Render logs)

### Problem: Keep-alive shows "Test ping failed"

**Solutions:**

1. Wait a few minutes for the service to fully start
2. Check if the service URL is accessible in a browser
3. Verify the `/health` endpoint works: `https://your-app.onrender.com/health`

### Problem: Service still goes to sleep

**Solutions:**

1. Verify keep-alive is running: send `/status` and check "Keep-alive: 🏃 Running"
2. Check Render logs for keep-alive ping messages
3. Make sure `KEEPALIVE_INTERVAL` is set to 840 (14 minutes)

### Problem: Configuration not loading

**Solutions:**

1. Check that `CONFIG_JSON` environment variable is set
2. Verify the JSON is valid (use a JSON validator)
3. Check Render logs for configuration errors

## 📝 Example CONFIG_JSON

Here's a properly formatted CONFIG_JSON for environment variables:

```json
{
  "channels": ["pekkaaaplays", "anshyt", "mafianinja", "killeryttt"],
  "wait_times": {
    "livestream_active": {
      "min": 120,
      "max": 300
    },
    "livestream_inactive": 600,
    "error_wait": 180
  },
  "authorization": "Bearer YOUR_ACTUAL_TOKEN_HERE",
  "messages": [
    "[emote:1730752:emojiAngel]",
    "[emote:1730756:emojiCheerful]",
    "[emote:1730787:emojiHappy]",
    "[emote:1730834:emojiYay]"
  ],
  "web_dashboard": {
    "enabled": true,
    "port": 4000
  },
  "prediction": {
    "enabled": true,
    "amount": 50
  },
  "telegram": {
    "bot_token": "YOUR_ACTUAL_BOT_TOKEN_HERE",
    "allowed_user_ids": []
  }
}
```

**Remember**: When setting this as an environment variable, it must be on a single line without spaces between keys.

## ✅ Success Checklist

- [ ] Service deployed successfully on Render
- [ ] Web dashboard accessible at your Render URL
- [ ] Telegram bot responds to `/start`
- [ ] `/wake` command starts keep-alive successfully
- [ ] Keep-alive pings visible in logs every 14 minutes
- [ ] Channels showing in dashboard
- [ ] Messages being sent (check `/status`)

## 📞 Support

If you encounter issues:

1. Check Render deployment logs
2. Verify all environment variables are set correctly
3. Test individual components (web dashboard, telegram bot, keep-alive)
4. Ensure your Kick authorization token is valid
