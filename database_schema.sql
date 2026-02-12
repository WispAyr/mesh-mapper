-- SQLite Database Schema for Mesh Mapper
-- Provides persistence and fast queries for all data types

-- Drone Detections (main tracking data)
CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mac TEXT NOT NULL,
    alias TEXT,
    timestamp REAL NOT NULL,
    rssi INTEGER,
    drone_lat REAL,
    drone_lon REAL,
    drone_altitude REAL,
    pilot_lat REAL,
    pilot_lon REAL,
    basic_id TEXT,
    faa_data TEXT,  -- JSON string
    status TEXT DEFAULT 'active',
    last_update REAL,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    INDEX idx_mac (mac),
    INDEX idx_timestamp (timestamp),
    INDEX idx_status (status),
    INDEX idx_last_update (last_update)
);

-- AIS Vessels
CREATE TABLE IF NOT EXISTS ais_vessels (
    mmsi TEXT PRIMARY KEY,
    name TEXT,
    vessel_type TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    course REAL,
    speed REAL,
    heading INTEGER,
    length REAL,
    width REAL,
    timestamp REAL NOT NULL,
    last_seen REAL NOT NULL,
    INDEX idx_timestamp (timestamp),
    INDEX idx_last_seen (last_seen)
);

-- Weather Data
CREATE TABLE IF NOT EXISTS weather_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_key TEXT NOT NULL,  -- e.g., "55.458_-4.629"
    location_name TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    source TEXT,  -- 'manual' or 'drone_detection'
    weather_json TEXT,  -- Full weather data as JSON
    last_update REAL NOT NULL,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    UNIQUE(location_key),
    INDEX idx_last_update (last_update),
    INDEX idx_location (lat, lon)
);

-- Webcams
CREATE TABLE IF NOT EXISTS webcams (
    webcam_id TEXT PRIMARY KEY,
    title TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    status TEXT,
    image_url TEXT,
    player_url TEXT,
    webcam_json TEXT,  -- Full webcam data as JSON
    last_update REAL NOT NULL,
    INDEX idx_last_update (last_update),
    INDEX idx_location (lat, lon)
);

-- APRS Stations
CREATE TABLE IF NOT EXISTS aprs_stations (
    callsign TEXT PRIMARY KEY,
    name TEXT,
    type TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    altitude REAL,
    course REAL,
    speed REAL,
    symbol TEXT,
    comment TEXT,
    status TEXT,
    timestamp INTEGER,
    last_seen REAL NOT NULL,
    INDEX idx_last_seen (last_seen),
    INDEX idx_location (lat, lon)
);

-- ADSB Aircraft (Automatic Dependent Surveillance-Broadcast)
CREATE TABLE IF NOT EXISTS adsb_aircraft (
    hex TEXT PRIMARY KEY,  -- ICAO 24-bit address (hex code)
    callsign TEXT,
    registration TEXT,
    aircraft_type TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    altitude_ft REAL,
    altitude_baro REAL,  -- Barometric altitude
    altitude_geom REAL,  -- Geometric altitude
    speed_kts REAL,  -- Ground speed in knots
    track REAL,  -- Track angle in degrees
    vertical_rate INTEGER,  -- Vertical rate in ft/min
    squawk TEXT,  -- Transponder squawk code
    category TEXT,  -- Aircraft category
    timestamp REAL NOT NULL,
    last_seen REAL NOT NULL,
    INDEX idx_timestamp (timestamp),
    INDEX idx_last_seen (last_seen),
    INDEX idx_location (lat, lon)
);

-- FAA Cache (for faster lookups)
CREATE TABLE IF NOT EXISTS faa_cache (
    mac TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    faa_response TEXT,  -- JSON string
    cached_at REAL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (mac, remote_id),
    INDEX idx_mac (mac),
    INDEX idx_remote_id (remote_id)
);

-- Aliases (device names)
CREATE TABLE IF NOT EXISTS aliases (
    mac TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    updated_at REAL DEFAULT (strftime('%s', 'now'))
);

-- Zones (airspace restrictions)
CREATE TABLE IF NOT EXISTS zones (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    zone_type TEXT,
    coordinates TEXT,  -- JSON array of [lat, lon] pairs
    lower_altitude_ft REAL,
    upper_altitude_ft REAL,
    enabled INTEGER DEFAULT 1,
    source TEXT,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    INDEX idx_enabled (enabled)
);

-- Incidents (zone violations, detections)
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_type TEXT NOT NULL,  -- 'detection', 'zone_entry', 'zone_exit'
    timestamp REAL NOT NULL,
    mac TEXT,
    alias TEXT,
    zone_id TEXT,
    drone_lat REAL,
    drone_lon REAL,
    drone_altitude REAL,
    pilot_lat REAL,
    pilot_lon REAL,
    basic_id TEXT,
    rssi INTEGER,
    faa_data TEXT,  -- JSON string
    details TEXT,  -- JSON string for additional data
    INDEX idx_timestamp (timestamp),
    INDEX idx_type (incident_type),
    INDEX idx_mac (mac)
);

-- Recent active data view (for fast page loads)
CREATE VIEW IF NOT EXISTS recent_detections AS
SELECT * FROM detections
WHERE last_update > (strftime('%s', 'now') - 300)  -- Last 5 minutes
ORDER BY last_update DESC;

CREATE VIEW IF NOT EXISTS recent_ais_vessels AS
SELECT * FROM ais_vessels
WHERE last_seen > (strftime('%s', 'now') - 600)  -- Last 10 minutes
ORDER BY last_seen DESC;

CREATE VIEW IF NOT EXISTS recent_weather AS
SELECT * FROM weather_data
WHERE last_update > (strftime('%s', 'now') - 600)  -- Last 10 minutes
ORDER BY last_update DESC;

CREATE VIEW IF NOT EXISTS active_webcams AS
SELECT * FROM webcams
WHERE status = 'active' AND last_update > (strftime('%s', 'now') - 3600)  -- Last hour
ORDER BY last_update DESC;

CREATE VIEW IF NOT EXISTS recent_adsb_aircraft AS
SELECT * FROM adsb_aircraft
WHERE last_seen > (strftime('%s', 'now') - 300)  -- Last 5 minutes
ORDER BY last_seen DESC;

