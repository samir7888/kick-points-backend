# Kick Points Miner & Prediction Auto-Voter

Automated bot for mining channel points and auto-participating in predictions on Kick.com through periodic chat interactions and real-time Pusher WebSockets.

## Features

- **Real-Time Prediction Auto-Voting**: Instant detection via Kick's dedicated Pusher WebSocket stream (`predictions-channel-{channel_id}` and `private-chatrooms.{chatroom_id}.v2`) with automatic fallback to Kick's REST API.
- **Smart Point Balance Fallback**: Automatically votes your configured points (e.g., 50 pts) and seamlessly falls back to Kick's platform minimum (10 pts) if channel points are insufficient.
- **Multi-Channel Concurrent Monitoring**: Concurrently handles chat mining and prediction voting across all configured channels.
- **Web Dashboard**: Modern dark-mode web interface (port `4000`) visualizing active channels, predictions, points voted, and live status.
- **Docker & Cloud Ready**: Fully containerized and optimized for Oracle Cloud Free Tier (ARM64 Ampere / x86_64 AMD) with persistent volume logging and auto-restart.

---

## Configuration

Copy `config.example.json` to `config.json` and configure your channels and Bearer token:

```json
{
  "channels": ["pekkaaaplays", "anshyt", "mafianinja", "killeryttt"],
  "authorization": "Bearer YOUR_TOKEN_HERE",
  "wait_times": {
    "livestream_active": { "min": 120, "max": 300 },
    "livestream_inactive": 600,
    "error_wait": 180
  },
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
  }
}
```

### Obtaining Your Authorization Token:

1. Open any Kick.com livestream while logged into your Kick account.
2. Open Developer Tools (`F12`) → **Network** tab.
3. Send a message or vote on a prediction.
4. Filter by `messages/send` or `predictions/latest`.
5. Under Request Headers, copy the full `Authorization` value (starting with `Bearer ...`).

---

## Docker Deployment (Oracle Cloud Free Tier)

### 1. Prerequisites on Oracle Cloud Instance (Ubuntu / Oracle Linux)

```bash
# Update and install Docker + Compose
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
```

### 2. Open Port 4000 (For Web Dashboard)

In your Oracle Cloud Console:

- Go to **Networking** → **Virtual Cloud Networks** → Select your VCN.
- Select your **Security List** → **Add Ingress Rule**:
  - Source CIDR: `0.0.0.0/0`
  - IP Protocol: `TCP`
  - Destination Port Range: `4000`

On the VPS instance firewall (iptables / ufw):

```bash
# Ubuntu UFW:
sudo ufw allow 4000/tcp

# Or Oracle Linux iptables:
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 4000 -j ACCEPT
sudo netfilter-persistent save
```

### 3. Deploy via Docker Compose

#### Option A: Using config.json file (Simple)

```bash
# Clone repository
git clone <repo-url> kick-miner
cd kick-miner

# Create config.json with your bearer token
cp config.example.json config.json
nano config.json

# Uncomment the config volume mount in docker-compose.yml
# - ./config.json:/app/config.json:ro

# Start container in detached mode
docker compose up -d --build
```

#### Option B: Using environment variables (Recommended for production)

```bash
# Clone repository
git clone <repo-url> kick-miner
cd kick-miner

# Create .env file from example
cp .env.example .env

# Edit .env and set CONFIG_JSON with your complete configuration
nano .env

# Start container in detached mode
docker compose up -d --build
```

### 4. Monitor and Manage

```bash
# View real-time logs
docker compose logs -f

# Check container health and status
docker compose ps

# Restart the service
docker compose restart

# Stop the service
docker compose down
```

The web dashboard will be available at:
`http://<YOUR_ORACLE_PUBLIC_IP>:4000`

### Troubleshooting Docker Deployment

**Problem**: `FileNotFoundError: [Errno 2] No such file or directory: 'config.json'`

**Solutions**:

1. **If using config file**: Uncomment the volume mount line in `docker-compose.yml`:

   ```yaml
   volumes:
     - ./config.json:/app/config.json:ro # Uncomment this line
     - ./logs:/app/logs
   ```

2. **If using environment variables**: Set `CONFIG_JSON` in your `.env` file with the complete JSON configuration as a single line.

3. **Quick fix**: The app will automatically fall back to `config.example.json` if `config.json` is not found, but make sure to update the bearer token in the example file.

**Problem**: Container exits immediately

**Solutions**:

1. Check logs: `docker compose logs`
2. Verify your bearer token is valid
3. Ensure all required fields are in your configuration

---

## Local Development (Without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Run the miner
python main.py
```

## Requirements

- Python 3.10+ (or Docker)
- Dependencies: `cloudscraper`, `loguru`, `flask`, `websocket-client`

## License

[MIT](LICENSE)
