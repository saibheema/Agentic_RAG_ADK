# Secure Database Tunnel Setup — IT Instructions for Youngsinc

## What This Does

This creates a **secure, encrypted, outbound-only tunnel** from your network to our cloud.  
Your SQL Server (`10.0.0.22:1433`) becomes accessible to our application hosted on Google Cloud.

**No inbound firewall rules needed. No VPN service. No ports opened on your router.**  
One small background process on any always-on Windows/Linux machine inside your network.

---

## What You Need

| Requirement | Details |
|-------------|---------|
| A Windows or Linux machine | Any PC/server that is **always on** and has network access to `10.0.0.22` |
| Internet access from that machine | Outbound TCP port **9090** must be allowed (most corporate firewalls allow this by default) |
| Admin rights on that machine | Just to run the process (or install as a service) |

---

## Step-by-Step Setup

### Step 1 — Download `chisel` (one binary, ~8 MB, no install needed)

**On Windows:**
1. Go to: https://github.com/jpillora/chisel/releases/latest
2. Download: `chisel_X.X.X_windows_amd64.gz`
3. Extract the `.gz` file (use 7-Zip or WinRAR) → you get `chisel.exe`
4. Move `chisel.exe` to `C:\chisel\chisel.exe`

**On Linux:**
```bash
curl -Lo chisel.gz https://github.com/jpillora/chisel/releases/latest/download/chisel_1.10.1_linux_amd64.gz
gunzip chisel.gz
chmod +x chisel
sudo mv chisel /usr/local/bin/chisel
```

---

### Step 2 — Run the tunnel (one command)

We will provide you with our **cloud server IP** once set up on our end. Replace `CLOUD_IP` below.

**On Windows** — open Command Prompt as Administrator:
```
C:\chisel\chisel.exe client CLOUD_IP:9090 R:1433:10.0.0.22:1433
```

**On Linux:**
```bash
chisel client CLOUD_IP:9090 R:1433:10.0.0.22:1433
```

You should see output like:
```
2026/03/01 10:00:00 client: Connected (Latency 45ms)
```

That's it. The tunnel is active.

---

### Step 3 — Keep it running (set up as a background service)

#### Windows — Install as a Windows Service (runs automatically on boot)

1. Download NSSM (Non-Sucking Service Manager): https://nssm.cc/download  
   Extract `nssm.exe` to `C:\chisel\`

2. Open Command Prompt **as Administrator** and run:
```
C:\chisel\nssm.exe install ChiselTunnel "C:\chisel\chisel.exe" "client CLOUD_IP:9090 R:1433:10.0.0.22:1433"
C:\chisel\nssm.exe set ChiselTunnel DisplayName "Chisel DB Tunnel"
C:\chisel\nssm.exe set ChiselTunnel Description "Secure outbound tunnel to cloud application"
C:\chisel\nssm.exe set ChiselTunnel Start SERVICE_AUTO_START
C:\chisel\nssm.exe start ChiselTunnel
```

3. Verify it's running:
```
sc query ChiselTunnel
```

#### Linux — Install as a systemd service

Create `/etc/systemd/system/chisel-tunnel.service`:
```ini
[Unit]
Description=Chisel DB Tunnel
After=network.target

[Service]
ExecStart=/usr/local/bin/chisel client CLOUD_IP:9090 R:1433:10.0.0.22:1433
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable chisel-tunnel
sudo systemctl start chisel-tunnel
sudo systemctl status chisel-tunnel
```

---

## Firewall / Security Notes for Your IT Team

| Direction | Protocol | Port | Destination | Required? |
|-----------|----------|------|-------------|-----------|
| **Outbound** | TCP | 9090 | `CLOUD_IP` | ✅ Yes — this is the only requirement |
| Inbound | Any | Any | — | ❌ No changes needed |
| Router port forward | Any | Any | — | ❌ Not needed |

**Security properties:**
- All traffic is **TLS-encrypted** (same encryption as HTTPS)
- Connection is **initiated from inside your network** — we cannot connect to you unless your process is running
- You can **stop the service any time** to immediately cut off access
- No credentials are stored on the tunnel machine — it only forwards TCP connections

---

## Connectivity Test

Once the service is running, send us a message and we will confirm connectivity from our cloud to `10.0.0.22:1433` within a few minutes.

If there's a connection issue, check:
1. The chisel process is running (Task Manager → Services on Windows, `systemctl status` on Linux)
2. The machine running chisel can reach `10.0.0.22:1433` (test: `telnet 10.0.0.22 1433` or `Test-NetConnection -ComputerName 10.0.0.22 -Port 1433` on PowerShell)
3. Outbound TCP port 9090 is not blocked by your corporate firewall

---

## What We Will Provide

Before you start Step 2, we will send you:
- `CLOUD_IP` — the IP address of our cloud relay server
- Confirmation that the server is ready to accept your connection

---

## Questions?

Contact: [your name / email here]
