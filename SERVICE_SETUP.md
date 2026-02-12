# Systemd Service Setup

This guide will help you set up the drone detection system to run automatically at boot using systemd.

## Installation

### Step 1: Install the Service

Run the installation script as root:

```bash
sudo /home/drone/mesh-mapper/install-service.sh
```

This will:
- Copy the service file to `/etc/systemd/system/`
- Reload systemd
- Enable the service to start on boot

### Step 2: Start the Service

Start the service immediately (without rebooting):

```bash
sudo systemctl start drone-mapper
```

### Step 3: Verify It's Running

Check the service status:

```bash
sudo systemctl status drone-mapper
```

You should see the service is active and running.

## Service Management

### View Logs

View real-time logs:
```bash
sudo journalctl -u drone-mapper -f
```

View recent logs:
```bash
sudo journalctl -u drone-mapper -n 50
```

View logs from today:
```bash
sudo journalctl -u drone-mapper --since today
```

### Stop the Service

```bash
sudo systemctl stop drone-mapper
```

### Restart the Service

```bash
sudo systemctl restart drone-mapper
```

### Disable Auto-Start (but keep service installed)

```bash
sudo systemctl disable drone-mapper
```

### Re-enable Auto-Start

```bash
sudo systemctl enable drone-mapper
```

### Remove the Service

```bash
sudo systemctl stop drone-mapper
sudo systemctl disable drone-mapper
sudo rm /etc/systemd/system/drone-mapper.service
sudo systemctl daemon-reload
```

## Service Configuration

The service file is located at `/etc/systemd/system/drone-mapper.service` after installation.

### Default Settings

- **User**: `drone`
- **Working Directory**: `/home/drone/mesh-mapper`
- **Web Interface**: Available on port 5000
- **Auto-restart**: Enabled (restarts if it crashes)
- **Restart Delay**: 10 seconds after failure

### Customizing the Service

To modify the service behavior, edit the service file:

```bash
sudo nano /etc/systemd/system/drone-mapper.service
```

After making changes, reload systemd:

```bash
sudo systemctl daemon-reload
sudo systemctl restart drone-mapper
```

### Common Modifications

#### Run in Headless Mode

Change the ExecStart line to:
```
ExecStart=/usr/bin/python3 /home/drone/mesh-mapper/mesh-mapper.py --headless
```

#### Change Web Port

Add `--web-port` argument:
```
ExecStart=/usr/bin/python3 /home/drone/mesh-mapper/mesh-mapper.py --web-port 8080
```

#### Enable Debug Logging

Add `--debug` argument:
```
ExecStart=/usr/bin/python3 /home/drone/mesh-mapper/mesh-mapper.py --debug
```

#### Disable Auto-Start of Ports

Add `--no-auto-start` argument:
```
ExecStart=/usr/bin/python3 /home/drone/mesh-mapper/mesh-mapper.py --no-auto-start
```

## Troubleshooting

### Service Won't Start

1. Check the service status:
   ```bash
   sudo systemctl status drone-mapper
   ```

2. Check the logs:
   ```bash
   sudo journalctl -u drone-mapper -n 100
   ```

3. Verify Python and dependencies:
   ```bash
   python3 --version
   pip3 list | grep -E "(flask|serial|socketio)"
   ```

4. Check file permissions:
   ```bash
   ls -la /home/drone/mesh-mapper/mesh-mapper.py
   ```

### Port Already in Use

The service automatically attempts to free port 5000 on startup. If you see port conflicts:

1. Check what's using the port:
   ```bash
   sudo lsof -i :5000
   ```

2. Kill the process manually:
   ```bash
   sudo fuser -k 5000/tcp
   ```

3. Restart the service:
   ```bash
   sudo systemctl restart drone-mapper
   ```

### Service Keeps Restarting

If the service keeps crashing and restarting:

1. Check logs for errors:
   ```bash
   sudo journalctl -u drone-mapper -n 100 --no-pager
   ```

2. Try running manually to see the error:
   ```bash
   cd /home/drone/mesh-mapper
   python3 mesh-mapper.py
   ```

3. Check if serial ports are available:
   ```bash
   ls -la /dev/ttyACM*
   ```

### USB Devices Not Detected at Boot

The service waits for USB devices, but if they're not ready:

1. Increase the delay in the service file:
   ```
   ExecStartPre=/bin/sleep 10
   ```

2. Or add a dependency on USB subsystem (if available):
   ```
   After=usb-gadget.service
   ```

### Accessing the Web Interface

Once the service is running, access the web interface at:
- **Local**: http://localhost:5000
- **Network**: http://<pi-ip-address>:5000

To find your Pi's IP address:
```bash
hostname -I
```

## Service Dependencies

The service is configured to start after:
- Network is online (`network-online.target`)
- USB devices are available (`usb-gadget.service` if present)
- 5-second delay to ensure USB serial ports are ready

## Security Notes

- The service runs as user `drone` (not root)
- The web interface binds to `0.0.0.0` (all interfaces)
- Consider firewall rules if the Pi is on a public network
- The service has `NoNewPrivileges=true` for security

## Performance

- The service will automatically restart if it crashes
- Restart delay is 10 seconds to prevent rapid restart loops
- Logs are sent to systemd journal (view with `journalctl`)


