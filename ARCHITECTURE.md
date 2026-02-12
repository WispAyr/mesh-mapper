# Drone Mesh Mapper — Architecture & Code Audit

> Based on [colonelpanichacks/drone-mesh-mapper](https://github.com/colonelpanichacks/drone-mesh-mapper), heavily customized with multi-source environmental awareness layers.

## Overview

A **13,933-line Python monolith** (`mesh-mapper.py`) running on a Raspberry Pi 5 that:

1. **Detects drones** via ESP32 WiFi sniffers connected over USB serial
2. **Tracks aircraft** via ADS-B (airplanes.live API)
3. **Monitors ships** via AIS (aisstream.io WebSocket + REST APIs)
4. **Watches weather** via Windy API + Met Office warnings
5. **Tracks ham radio** via APRS (aprs.fi API)
6. **Detects lightning** via Blitzortung WebSocket
7. **Maps airspace** via OpenAir + NOTAM data
8. **Serves a real-time web UI** with Leaflet maps + 3D Cesium view

All served from Flask + Flask-SocketIO on port 5000, running as a systemd service (`drone-mapper.service`).

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Raspberry Pi 5 (drone)                       │
│                                                                   │
│  ┌──────────────┐    ┌──────────────────────────────────────┐    │
│  │ ESP32 #1     │────│  Serial Reader Thread (port1)         │    │
│  │ (ttyACM0)    │    │  115200 baud, JSON detection msgs     │    │
│  └──────────────┘    └───────────────┬──────────────────────┘    │
│  ┌──────────────┐    ┌──────────────┐│                            │
│  │ ESP32 #2     │────│  Serial Reader││  ┌─────────────────────┐  │
│  │ (ttyACM1)    │    │  Thread (port2││  │ Background Threads   │  │
│  └──────────────┘    └──────────────┘│  │                       │  │
│                                      │  │ • ADS-B updater (5s)  │  │
│                                      │  │ • AIS REST (60s)      │  │
│                                      │  │ • AIS WebSocket       │  │
│                                      │  │ • APRS updater (120s) │  │
│                                      │  │ • Weather (300s)      │  │
│                                      │  │ • Webcams (600s)      │  │
│                                      │  │ • Met Office (1800s)  │  │
│                                      │  │ • Lightning WS        │  │
│                                      │  │ • Ports (3600s)       │  │
│                                      │  │ • OpenAir (24h)       │  │
│                                      │  │ • NOTAM (6h)          │  │
│                                      │  │ • Cleanup (300s)      │  │
│                                      │  │ • WS Broadcaster(30s) │  │
│                                      │  │ • Port Monitor (10s)  │  │
│                                      │  │ • Status Logger (60s) │  │
│                                      │  └─────────────────────┘  │
│                                      ▼                            │
│                          ┌──────────────────┐                     │
│                          │  update_detection │                     │
│                          │  (core pipeline)  │                     │
│                          └────────┬─────────┘                     │
│                                   │                               │
│                    ┌──────────────┼──────────────┐                │
│                    ▼              ▼              ▼                │
│             ┌──────────┐  ┌──────────┐  ┌──────────────┐         │
│             │ SQLite DB │  │ CSV/KML  │  │ SocketIO     │         │
│             │ mesh_     │  │ Files    │  │ Broadcast    │         │
│             │ mapper.db │  │          │  │ (real-time)  │         │
│             └──────────┘  └──────────┘  └──────┬───────┘         │
│                                                 │                 │
│                          ┌──────────────────────┘                 │
│                          ▼                                        │
│                  ┌───────────────┐                                │
│                  │ Flask+SocketIO│                                │
│                  │ Web Server    │                                │
│                  │ Port 5000     │                                │
│                  └───────┬───────┘                                │
│                          │                                        │
└──────────────────────────┼────────────────────────────────────────┘
                           │
                     ┌─────┴─────┐
                     │ Web UI    │
                     │ (Leaflet  │
                     │  + 3D)    │
                     └───────────┘
```

---

## Threading Model

**~15 daemon threads** running concurrently, all using `threading.Event` (`SHUTDOWN_EVENT`) for graceful shutdown:

| Thread | Interval | Purpose |
|--------|----------|---------|
| `serial_reader` × N | Continuous | Read ESP32 JSON over USB serial (one per port) |
| `adsb_updater` | 5s | Fetch aircraft from airplanes.live API |
| `ais_updater` | 60s | REST API AIS vessel fetch |
| `ais_websocket_thread` | Continuous | Real-time AIS from aisstream.io |
| `aprs_updater` | 120s | Fetch APRS stations from aprs.fi |
| `weather_updater` | 300s | Fetch forecasts from Windy API |
| `webcams_updater` | 600s | Fetch webcam data from Windy |
| `metoffice_updater` | 1800s | UK weather warnings (GeoJSON/RSS) |
| `ports_updater` | 3600s | Maritime port data from Marinesia |
| `lightning_websocket_thread` | Continuous | Real-time lightning from Blitzortung |
| `openair_updater` | 24h | UK airspace boundaries |
| `notam_updater` | 6h | NOTAMs from UK PIB archive |
| `cleanup_timer` | 300s | Mark stale detections, clean FAA cache |
| `broadcaster` | 30s | Full state WebSocket broadcast |
| `port_monitor` | 10s | Monitor USB port availability |
| `status_logger` | 60s | Log system status metrics |

**Thread safety:**
- `serial_objs_lock` (threading.Lock) guards serial port dict
- `DB_LOCK` (threading.Lock) guards SQLite writes
- Global dicts (`tracked_pairs`, `AIS_VESSELS`, `ADSB_AIRCRAFT`, etc.) accessed without locks — potential race conditions but acceptable for read-heavy workloads

---

## Data Sources & APIs

### 1. ESP32 Serial (Drone Detection) — Core Feature
- **Protocol:** JSON over USB serial at 115200 baud
- **Ports:** Up to 3 configurable (`/dev/ttyACM0`, `/dev/ttyACM1`)
- **Detection format:**
  ```json
  {
    "mac": "AA:BB:CC:DD:EE:FF",
    "rssi": -65,
    "drone_lat": 55.458,
    "drone_long": -4.629,
    "drone_altitude": 120.5,
    "pilot_lat": 55.457,
    "pilot_long": -4.630,
    "basic_id": "SERIAL_NUMBER",
    "remote_id": "SERIAL_NUMBER"
  }
  ```
- **Commands sent to ESP32:** `WATCHDOG_RESET\n`
- Heartbeat messages (`{"heartbeat": ...}`) are filtered out
- Supports no-GPS detections (RSSI only, no coordinates)

### 2. ADS-B (Aircraft) — airplanes.live
- **API:** `https://api.airplanes.live/v2/point/{lat}/{lon}/{radius_nm}`
- **No API key required** (public API)
- **Update interval:** 5 seconds
- **Default search:** Center 56.5°N, 4.0°W (Scotland), 1000km radius
- **Known issue:** 429 rate limiting with 5s interval over large radius
- **Data:** ICAO hex, callsign, registration, type, position, altitude, speed, track, squawk

### 3. AIS (Maritime Vessels) — Multiple Sources
- **Primary: aisstream.io WebSocket** (`wss://stream.aisstream.io/v0/stream`)
  - API key required (stored in `ais_config.json`)
  - Real-time position reports + static data
  - UK waters bounding box: SW(-11.0, 49.5) to NE(2.0, 61.0)
- **Fallback REST APIs** (tried in order):
  1. **Marinesia** (`api.marinesia.com`) — needs `MARINESIA_API_KEY`
  2. **Datalastic** (`api.datalastic.com`) — needs `DATALASTIC_API_KEY`
  3. **MarineTraffic** (`services.marinetraffic.com`) — needs `MARINE_TRAFFIC_API_KEY`
- **Grid search mode:** Divides UK waters into 2° cells for comprehensive coverage

### 4. APRS (Ham Radio Stations) — aprs.fi
- **API:** `https://api.aprs.fi/api/get`
- **API key:** Stored in `aprs_config.json` (key: `52628.6y7Il9Hp5bjt9F`)
- **Update interval:** 120 seconds
- **Queries specific callsigns** (configured via web UI)

### 5. Weather — Windy API
- **API:** `https://api.windy.com/api/point-forecast/v2`
- **API key:** Stored in `weather_config.json` (key: `lMB6Y9omePmFJzNDV3iEaoHU1hkHgS4g`)
- **Update interval:** 300 seconds
- **Locations:** Configured via web UI (default: Ayr, Glasgow)
- **Auto-adds locations** around active drone detections

### 6. Webcams — Windy API
- **API:** `https://api.windy.com/webcams/api/v3/webcams`
- **API key:** `fL4oaiWWotB9uYoLlNhj627ojnMq9uPD`
- **Update interval:** 600 seconds

### 7. Lightning — Blitzortung
- **WebSocket:** `wss://ws1.blitzortung.org/`
- **No API key** (public community network)
- **Real-time strikes** within configurable regions
- **Processing:** Calculates distance from drone detections, emits to clients

### 8. Met Office Weather Warnings
- **Primary:** NSWWS GeoJSON API (`metoffice.gov.uk/public/data/NSWWS/WarningsJSON`)
- **Fallback:** RSS feed (`metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/UK`)
- **Update interval:** 1800 seconds (30 min)
- **Includes polygon boundaries** for map overlay

### 9. Maritime Ports — Marinesia API
- **API:** `api.marinesia.com/api/v1/port/nearby`
- **Needs `MARINESIA_API_KEY`** environment variable
- **Update interval:** 3600 seconds (1 hour)

### 10. UK Airspace — OpenAir Format
- **Source:** `https://asselect.uk/default/openair.txt`
- **Downloaded daily**, parsed into zones
- **Filters:** Only zones ≤400ft AGL (drone-relevant)
- **Classes tracked:** A, C, D, E, P (Prohibited), R (Restricted), FRZ, CTR, TMZ, ATZ, MATZ, RMZ

### 11. NOTAMs — UK PIB Archive
- **Source:** `https://raw.githubusercontent.com/Jonty/uk-notam-archive/main/data/PIB.xml`
- **Updated every 6 hours**
- **Parses coordinates** (DMS format) and generates zone polygons
- **Filters expired NOTAMs** automatically

### 12. FAA Remote ID Lookup
- **API:** `https://uasdoc.faa.gov/api/v1/serialNumbers`
- **No API key** (uses browser-like session with cookie refresh)
- **Caches results** by (MAC, remote_id) tuple
- **Persistent cache** in memory + database

---

## Database Schema (SQLite)

File: `mesh_mapper.db`

| Table | Primary Key | Purpose |
|-------|-------------|---------|
| `detections` | `id` (auto) | Drone detection history |
| `ais_vessels` | `mmsi` | Maritime vessel positions |
| `adsb_aircraft` | `hex` | Aircraft tracking data |
| `weather_data` | `id` (auto), UNIQUE `location_key` | Weather forecasts |
| `webcams` | `webcam_id` | Nearby webcam feeds |
| `aprs_stations` | `callsign` | APRS ham radio stations |
| `faa_cache` | `(mac, remote_id)` | FAA lookup cache |
| `aliases` | `mac` | User-assigned drone names |
| `zones` | `id` | Airspace restriction zones |
| `incidents` | `id` (auto) | Zone violations & detection events |

**Views:**
- `recent_detections` — last 5 minutes
- `recent_ais_vessels` — last 10 minutes
- `recent_weather` — last 10 minutes
- `active_webcams` — last hour
- `recent_adsb_aircraft` — last 5 minutes

---

## Flask Routes (REST API)

### Core Detection
| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/` | Main map UI (redirects to `/select_ports` if no ports configured) |
| GET | `/3d` | 3D Cesium airspace visualization |
| GET | `/select_ports` | USB port selection page |
| POST | `/select_ports` | Configure USB ports + webhook URL |
| GET | `/api/detections` | All current tracked drones |
| POST | `/api/detections` | Submit a detection (for testing/mesh networking) |
| GET | `/api/detections_history` | GeoJSON FeatureCollection of all history |
| POST | `/api/reactivate/<mac>` | Manually reactivate a stale detection |

### Device Management
| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/aliases` | Get all device aliases |
| POST | `/api/set_alias` | Set alias for a MAC address |
| POST | `/api/clear_alias/<mac>` | Clear alias |
| GET | `/api/ports` | List available USB serial ports |
| GET | `/api/serial_status` | Connection status of each port |
| GET | `/api/selected_ports` | Currently selected ports |

### Zone Management
| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/zones` | All defined zones |
| POST | `/api/zones` | Create a custom zone |
| PUT | `/api/zones/<id>` | Update a zone |
| DELETE | `/api/zones/<id>` | Delete a zone |
| POST | `/api/zones/update-openair` | Force OpenAir zone refresh |
| POST | `/api/zones/update-notam` | Force NOTAM zone refresh |

### Incidents
| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/incidents` | Query incidents (filterable) |
| GET | `/api/incidents/stats` | Incident statistics |

### Data Layers (each has GET/POST for config + GET for data)
| Layer | Data Route | Config Route | Toggle Route |
|-------|-----------|-------------|-------------|
| ADS-B | `/api/adsb_aircraft` | `/api/adsb_settings` | `/api/adsb_detection` |
| AIS | `/api/ais_vessels` | `/api/ais_config` | `/api/ais_detection` |
| APRS | `/api/aprs_stations` | `/api/aprs_config` | `/api/aprs_detection` |
| Weather | `/api/weather` | `/api/weather_config` | `/api/weather_detection` |
| Webcams | `/api/webcams` | `/api/webcams_config` | `/api/webcams_detection` |
| Lightning | — | — | `/api/lightning_detection` |
| Met Office | `/api/metoffice_warnings` | `/api/metoffice_settings` | `/api/metoffice_warnings_detection` |
| Ports (maritime) | `/api/maritime_ports` | — | — |

### Downloads
| Route | Purpose |
|-------|---------|
| `/download/csv` | Current session CSV |
| `/download/kml` | Current session KML |
| `/download/aliases` | Aliases JSON |
| `/download/cumulative_detections.csv` | All-time CSV |
| `/download/cumulative.kml` | All-time KML |

### Diagnostics & Control
| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/diagnostics` | System diagnostics info |
| POST | `/api/debug_mode` | Toggle debug logging |
| POST | `/api/send_command` | Send command to serial ports |
| GET/POST | `/api/webhook_url` | Get/set webhook URL |
| GET | `/api/recent_data` | Fast page load (all recent data from DB) |

### Service Worker
| Route | Purpose |
|-------|---------|
| `/sw.js` | Map tile caching service worker |

---

## SocketIO Events

### Server → Client
| Event | Data | Purpose |
|-------|------|---------|
| `detection` | Single detection dict | Real-time drone detection |
| `detections` | All tracked_pairs | Full state sync |
| `aliases` | Alias dict | Device name updates |
| `serial_status` | Status dict | USB connection status |
| `paths` | Drone/pilot paths | Flight path data |
| `cumulative_log` | Cumulative data | Historical summary |
| `faa_cache` | FAA cache | Registration lookups |
| `ais_vessels` | `{vessels: [...]}` | Maritime vessel positions |
| `ais_vessel_update` | Single vessel | Real-time AIS update |
| `adsb_aircraft` | `{aircraft: [...]}` | Aircraft positions |
| `aprs_stations` | `{stations: [...]}` | APRS station positions |
| `weather_data` | `{weather: {...}}` | Weather forecast data |
| `webcams_data` | `{webcams: {...}}` | Webcam information |
| `metoffice_warnings` | `{warnings: [...]}` | UK weather warnings |
| `ports` | `{ports: [...]}` | Maritime port data |
| `lightning_strike` | Strike data | Real-time lightning |
| `zones_updated` | `{zones: [...]}` | Airspace zone changes |

### Client → Server
| Event | Purpose |
|-------|---------|
| `connect` | Triggers full state sync to new client |

---

## Web UI

### Main Map Page (inline HTML_PAGE, ~5800 lines)
- **Leaflet.js** map with multiple tile layers (OSM, CartoDB Dark, ESRI Satellite, OpenTopoMap)
- **Real-time drone markers** with color-coded tracks (unique color per MAC)
- **Pilot location markers** with connection lines to drones
- **Layer controls** for toggling each data source
- **Detection popups** with full details (FAA data, coordinates, RSSI)
- **Zone overlays** with color-coding by type
- **Weather warning polygons** (Met Office)
- **AIS vessel icons** with heading indicators
- **ADS-B aircraft markers** with altitude/speed info
- **APRS station markers**
- **Lightning strike markers** (fade over time)
- **Webcam markers** with preview images
- **Webhook notifications** on new detections
- **Follow/lock mode** for tracking specific entities
- **Service worker** for offline map tile caching

### Port Selection Page (inline PORT_SELECTION_PAGE)
- Lists available USB serial ports
- Allows selecting up to 3 ESP32 devices
- Webhook URL configuration
- ASCII art branding

### 3D View (`templates/3d_view.html`)
- **CesiumJS** 3D globe visualization
- Real-time aircraft + drone positioning in 3D space
- Altitude visualization
- Zone boundaries in 3D

---

## Configuration Files

| File | Purpose |
|------|---------|
| `ais_config.json` | AISStream.io API key |
| `aprs_config.json` | APRS.fi API key + callsign list |
| `weather_config.json` | Windy API key + monitored locations |
| `webcams_config.json` | Windy webcams API key |
| `lightning_settings.json` | Lightning detection enable/disable |
| `selected_ports.json` | USB port assignments |
| `webhook_url.json` | Webhook notification URL |
| `database_schema.sql` | SQLite schema definition |
| `drone-mapper.service` | systemd service unit |
| `requirements.txt` | Python dependencies |

---

## Webhook / Notification System

- **Configurable webhook URL** (set via UI or API)
- **Triggers on:**
  - New drone detection (first time seeing a MAC)
  - Drone state transition (inactive → active)
  - No-GPS drone detection (once per session)
- **Payload format:**
  ```json
  {
    "alert": "New drone detected",
    "mac": "AA:BB:CC:DD:EE:FF",
    "basic_id": "SERIAL_NUMBER",
    "alias": "Known Drone Name",
    "drone_lat": 55.458,
    "drone_long": -4.629,
    "pilot_lat": 55.457,
    "pilot_long": -4.630,
    "faa_data": {...},
    "drone_gmap": "https://www.google.com/maps?q=55.458,-4.629",
    "pilot_gmap": "https://www.google.com/maps?q=55.457,-4.630",
    "isNew": true
  }
  ```
- **Both client-side** (via `/api/webhook_popup`) and **server-side** (automatic) webhook sending

---

## Zone Event System

- **Zone entry/exit detection** using point-in-polygon algorithm
- **Zone types:** Restricted, Prohibited, CTR, FRZ, ATZ, MATZ, TMZ, RMZ, NOTAM
- **Events logged as incidents** with full detection context
- **Sources:**
  - Custom user-drawn zones
  - OpenAir airspace data (UK)
  - NOTAM temporary restrictions
  - Met Office warning polygons

---

## Known Issues & Concerns

1. **ADS-B 429 Rate Limiting:** 5-second polling interval with 1000km radius is aggressive for the free airplanes.live API. Consider increasing interval or reducing radius.

2. **7.7GB Log File:** `mapper.log` grows unbounded. No log rotation configured. After 45+ days, it's massive.

3. **No Thread Safety on Global Dicts:** `tracked_pairs`, `AIS_VESSELS`, `ADSB_AIRCRAFT` etc. are plain dicts accessed from multiple threads without locks. Works in practice due to GIL but not robust.

4. **13,933-Line Monolith:** All HTML (two full pages), all CSS, all JavaScript, all Python logic in one file. Very hard to maintain.

5. **Hardcoded API Keys in Config Files:** `ais_config.json`, `aprs_config.json`, `weather_config.json`, `webcams_config.json` contain API keys in plaintext. Should use environment variables.

6. **SQLite Concurrent Access:** While `DB_LOCK` is used, SQLite isn't ideal for concurrent write-heavy workloads from many threads.

7. **No Authentication:** Web UI and all API endpoints are completely open. Anyone on the network can access/modify settings.

8. **Stale NOTAM Filtering:** Uses basic date parsing that may not catch all expiry formats.

9. **Memory Growth:** `detection_history` list grows unbounded. `MAX_DETECTION_HISTORY` is defined (1000) but not enforced on the history list.

10. **Duplicate Route Definitions:** `api_diagnostics` is defined twice (lines 12764 and ~12589-area).

---

## Dependencies

```
Flask==3.1.1
Flask-SocketIO==5.5.1
pyserial==3.5
requests==2.32.3
urllib3==2.3.0
python-socketio==5.14.3
websocket-client==1.7.0
```

---

## systemd Service

- **Unit:** `drone-mapper.service`
- **User:** `drone`
- **Working Directory:** `/home/drone/mesh-mapper`
- **Restart:** Always (10s delay)
- **Security:** `NoNewPrivileges=true`, `PrivateTmp=true`
- **File limit:** 65536

---

## File Structure

```
mesh-mapper/
├── mesh-mapper.py          # Main application (13,933 lines)
├── database_schema.sql     # SQLite schema
├── templates/
│   └── 3d_view.html        # CesiumJS 3D visualization
├── drone-mapper.service    # systemd unit
├── install-service.sh      # Service installer
├── requirements.txt        # Python deps
├── .gitignore
│
├── ais_config.json         # AISStream API key
├── aprs_config.json        # APRS.fi API key + callsigns
├── weather_config.json     # Windy API key + locations
├── webcams_config.json     # Windy webcams API key
├── lightning_settings.json # Lightning enable flag
├── selected_ports.json     # USB port config
├── webhook_url.json        # Webhook URL
│
├── CODE_REVIEW.md          # Existing code review notes
├── DATABASE_PROPOSAL.md    # Database design proposal
├── ENHANCEMENT_RECOMMENDATIONS.md
├── design.md               # Original design doc
├── SERVICE_SETUP.md        # Service installation guide
├── README.md               # Project readme
└── ARCHITECTURE.md         # This file
```

**Runtime-generated files (not committed):**
- `mesh_mapper.db` — SQLite database
- `mapper.log` — Application log (grows large!)
- `detections_*.csv` / `detections_*.kml` — Per-session detection data
- `cumulative_detections.csv` / `cumulative.kml` — All-time data
- `openair.txt` — Downloaded UK airspace data
- `notam.xml` — Downloaded NOTAM data
- `zones.json` — Parsed airspace zones
- `faa_cache.csv` / `faa_log.csv` — FAA lookup cache
