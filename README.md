# Drone Detection System - Raspberry Pi 5

Real-time drone detection, mapping, and Remote ID compliance monitoring system running on Raspberry Pi 5 with multiple ESP32 detection units.

Based on the [drone-mesh-mapper](https://github.com/colonelpanichacks/drone-mesh-mapper) project.

## 🛠️ Hardware Setup

### Current Configuration
- **Host**: Raspberry Pi 5
- **Detection Units**: 2x ESP32-based devices
- **Serial Ports**: 
  - `/dev/ttyACM0` (port2)
  - `/dev/ttyACM1` (port1)
- **Baud Rate**: 115200

### Detection Units
ESP32 devices configured to detect WiFi Drone RemoteID transmissions and send detection data over serial. Each unit scans for:
- MAC addresses
- RSSI (signal strength)
- GPS coordinates (drone and pilot)
- Remote ID / Basic ID (FAA compliance)

## 📦 Installation

### Prerequisites
- Raspberry Pi 5 (or compatible)
- Python 3.13+ (tested with 3.13.5)
- USB serial ports for detection units

### Dependencies
```bash
pip3 install -r requirements.txt
```

### Required Packages
- Flask 3.1.1 - Web interface
- Flask-SocketIO 5.5.1 - Real-time updates
- pyserial 3.5 - Serial communication
- requests 2.32.3 - HTTP requests (FAA API, webhooks)
- urllib3 2.3.0 - HTTP utilities
- python-socketio 5.14.3 - Socket.IO support

## 🚀 Usage

### Basic Operation
```bash
python3 mesh-mapper.py
```

Access the web interface at: **http://localhost:5000**

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--headless` | Run without web interface | false |
| `--debug` | Enable debug logging | false |
| `--web-port PORT` | Web interface port | 5000 |
| `--port-interval SECONDS` | Port monitoring interval | 10 |
| `--no-auto-start` | Disable automatic port connection | false |

### Examples

```bash
# Standard operation with web interface
python3 mesh-mapper.py

# Headless mode for dedicated server
python3 mesh-mapper.py --headless --debug

# Custom web port
python3 mesh-mapper.py --web-port 8080

# Disable auto-connection to saved ports
python3 mesh-mapper.py --no-auto-start
```

## 📋 Features

### Real-time Detection
- **Live Mapping**: Interactive map showing drone positions in real-time
- **Multi-device Support**: Handles multiple ESP32 receivers simultaneously
- **Flight Path Tracking**: Visual trails showing drone and pilot movement
- **Session Persistence**: Drones remain visible across application restarts

### Data Management
- **Detection History**: Complete log of all drone encounters with timestamps
- **Device Aliases**: Assign friendly names to frequently seen drones
- **Export Formats**: Download data as CSV or KML (Google Earth)
- **Cumulative Logging**: Long-term historical data storage

### FAA Integration
- **Remote ID Lookup**: Automatic FAA registration queries
- **Compliance Monitoring**: Track Remote ID compliance status
- **Registration Cache**: Cached FAA data for faster lookups

### Web Interface
- **Real-time Updates**: WebSocket-powered live data streaming
- **Mobile Responsive**: Works on desktop, tablet, and mobile devices
- **Multiple Views**: Map, detection list, and device status panels
- **Data Export**: Download detections directly from web interface

### Configuration
- **Port Management**: Save and restore USB port configurations
- **Status Monitoring**: Real-time connection health and data flow indicators
- **Webhook Support**: External system integration via HTTP callbacks
- **Headless Operation**: Run without web interface for dedicated deployments

### Maritime AIS Integration
- **Real-time Vessel Tracking**: Display maritime vessels around UK waters on the map
- **Vessel Information**: View vessel name, MMSI, course, speed, and type
- **Multiple Data Sources**: Supports REST APIs and WebSocket feeds
- **Toggle Layer**: Show/hide vessel markers with a single click
- **Live Updates**: Real-time position updates via WebSocket connections

## 📊 Data Files

### Detection Files
- `detections_YYYYMMDD_HHMMSS.csv` - Session-specific detection log
- `detections_YYYYMMDD_HHMMSS.kml` - Session-specific KML for Google Earth
- `cumulative_detections.csv` - All-time detection history
- `cumulative.kml` - All-time KML file

### Configuration Files
- `selected_ports.json` - Saved serial port configuration
- `webhook_url.json` - Webhook endpoint configuration
- `aliases.json` - Device alias mappings (created on first alias)

### Cache & Logs
- `faa_cache.csv` - Cached FAA registration data
- `faa_log.csv` - FAA query log
- `mapper.log` - Application log file

## 🔌 API Reference

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Main web interface |
| GET | `/api/detections` | Current active drone detections |
| POST | `/api/detections` | Submit new detection data |
| GET | `/api/detections_history` | Historical detection data (GeoJSON) |
| GET | `/api/paths` | Flight path data for visualization |
| POST | `/api/reactivate/<mac>` | Reactivate inactive drone detection |

### Device Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/aliases` | Get device aliases |
| POST | `/api/set_alias` | Set friendly name for device |
| POST | `/api/clear_alias/<mac>` | Remove device alias |
| GET | `/api/ports` | Available serial ports |
| GET | `/api/serial_status` | ESP32 connection status |
| GET | `/api/selected_ports` | Currently configured ports |

### FAA & External Integration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/faa/<identifier>` | FAA registration lookup |
| POST | `/api/query_faa` | Manual FAA query |
| POST | `/api/set_webhook_url` | Configure webhook endpoint |
| GET | `/api/get_webhook_url` | Get current webhook URL |
| POST | `/api/webhook_popup` | Webhook notification handler |

### Data Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/download/csv` | Download current detections (CSV) |
| GET | `/download/kml` | Download current detections (KML) |
| GET | `/download/aliases` | Download device aliases |
| GET | `/download/cumulative_detections.csv` | Download full history (CSV) |
| GET | `/download/cumulative.kml` | Download full history (KML) |

### Maritime AIS

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/ais_vessels` | Get current AIS vessel data |
| GET | `/api/ais_detection` | Get AIS detection enabled status |
| POST | `/api/ais_detection` | Toggle AIS detection on/off |
| POST | `/api/ais_update` | Manually trigger AIS data update |

### System Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/diagnostics` | System health and performance |
| POST | `/api/debug_mode` | Toggle debug logging |
| POST | `/api/send_command` | Send command to ESP32 devices |
| GET | `/select_ports` | Port selection interface |
| POST | `/select_ports` | Update port configuration |

### WebSocket Events

Real-time events pushed to connected clients:
- `detection` - New or updated drone detection
- `detections` - Updated drone detection data
- `paths` - Updated flight path data
- `serial_status` - ESP32 connection status changes
- `aliases` - Device alias updates
- `cumulative_log` - Historical data updates
- `faa_cache` - FAA lookup results
- `ais_vessels` - Bulk AIS vessel data update
- `ais_vessel_update` - Individual vessel position update

## 🔧 Configuration

### Port Selection
On first run, the system will prompt you to select serial ports. Selected ports are saved in `selected_ports.json` and automatically reconnected on subsequent runs.

### Maritime AIS Data Setup

The system supports multiple AIS data sources for UK waters:

#### Option 1: AISStream.io (Recommended - Free WebSocket Feed)
1. Get a free API key from https://aisstream.io
2. Set environment variable:
   ```bash
   export AISSTREAM_API_KEY="your-api-key-here"
   ```
3. Restart the application - real-time vessel data will stream automatically

#### Option 2: Datalastic API (REST API)
1. Get an API key from https://datalastic.com (free tier available)
2. Set environment variable:
   ```bash
   export DATALASTIC_API_KEY="your-api-key-here"
   ```
3. Vessel data will update every 60 seconds

#### Option 3: Marinesia API (Recommended)
1. Get an API key from https://api.marinesia.com/swagger
2. Set environment variable:
   ```bash
   export MARINESIA_API_KEY="your-api-key-here"
   ```
3. Vessel data will update every 60 seconds
4. Port data will update every hour (ports don't change frequently)
5. Provides comprehensive maritime data including AIS vessel positions, vessel profiles, port information, and more

**Comprehensive Vessel Coverage:**
- By default, the system uses the `/vessel/nearby` endpoint to get vessels in UK waters
- Additionally, it uses paginated `/vessel/location` endpoint to supplement results and ensure no vessels are missed
- For even more comprehensive coverage, enable grid search:
  ```bash
  export AIS_USE_GRID_SEARCH="true"
  ```
  This divides UK waters into smaller grid cells and queries each one, ensuring maximum vessel detection (slower but more thorough)

#### Option 4: MarineTraffic API
1. Get an API key from https://www.marinetraffic.com
2. Set environment variable:
   ```bash
   export MARINE_TRAFFIC_API_KEY="your-api-key-here"
   ```
3. Vessel data will update every 60 seconds

**Note**: You can use multiple sources simultaneously. WebSocket feeds provide real-time updates, while REST APIs provide periodic snapshots.

### Webhook Configuration
Configure webhook URL via the web interface or API:
```bash
curl -X POST http://localhost:5000/api/set_webhook_url \
  -H "Content-Type: application/json" \
  -d '{"webhook_url": "https://your-webhook-endpoint.com/drone-detection"}'
```

### Aliases
Assign friendly names to MAC addresses via the web interface or API:
```bash
curl -X POST http://localhost:5000/api/set_alias \
  -H "Content-Type: application/json" \
  -d '{"mac": "dc:32:62:3e:71:a5", "alias": "Neighbor Drone"}'
```

## 🐛 Troubleshooting

### ESP32 Not Detected
```bash
# Check USB connection
ls -la /dev/tty* | grep ACM

# Verify driver installation
dmesg | grep tty

# Check port permissions
ls -l /dev/ttyACM*
```

### Web Interface Not Loading
```bash
# Check if service is running
netstat -tlnp | grep :5000

# Review logs
tail -f mapper.log

# Check for port conflicts
sudo lsof -i :5000
```

### No Drone Detections
- Verify ESP32 firmware is properly flashed
- Check WiFi channel configuration (default: channel 6)
- Ensure drones are transmitting Remote ID (required in many jurisdictions)
- Check serial connection and baud rate (115200)
- Review `mapper.log` for serial communication errors

### Port Connection Issues
- Ensure detection units are powered and connected
- Check USB cable quality (data-capable, not charge-only)
- Verify port permissions: `sudo usermod -a -G dialout $USER` (logout/login required)
- Try different USB ports if connection is unstable

## 📈 Performance

| Metric | Performance |
|--------|-------------|
| Detection Latency | < 500ms average |
| Concurrent Drones | 50+ simultaneous |
| Memory Usage | < 100MB typical |
| Storage Efficiency | ~1KB per detection |
| Network Throughput | 1000+ detections/min |

## 🔒 Security Notes

- Web interface binds to `0.0.0.0` by default (accessible from network)
- For production use, consider:
  - Firewall rules to restrict access
  - Reverse proxy with authentication
  - HTTPS/TLS encryption
  - Rate limiting on API endpoints

## 📄 License

Based on the [drone-mesh-mapper](https://github.com/colonelpanichacks/drone-mesh-mapper) project (MIT License).

## 🙏 Acknowledgments

- Original project: [colonelpanichacks/drone-mesh-mapper](https://github.com/colonelpanichacks/drone-mesh-mapper)
- Forked from: [JimZGChow/wifi-rid-to-mesh](https://github.com/JimZGChow/wifi-rid-to-mesh)
- OpenDroneID Community - Standards and specifications

## 📝 Detection Data Format

Each detection includes:
- `mac`: MAC address (unique identifier)
- `rssi`: Signal strength in dBm
- `drone_lat`, `drone_long`, `drone_altitude`: Drone GPS coordinates
- `pilot_lat`, `pilot_long`: Pilot GPS coordinates
- `basic_id`: Remote ID / Basic ID (FAA compliance)
- `faa_data`: FAA registration information (when available)
- `timestamp`: Detection timestamp
- `source_port`: Serial port that received the detection

## 🔄 System Status

Check system health:
```bash
curl http://localhost:5000/api/diagnostics
```

Response includes:
- Selected ports and connection status
- Number of tracked drones
- Recent detections
- Available ports
- System configuration

