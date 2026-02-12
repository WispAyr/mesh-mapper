import os
import time
import json
import csv
import logging
from logging.handlers import RotatingFileHandler
import colorsys
import threading
import requests
import urllib3
import serial
import serial.tools.list_ports
import signal
import sys
import argparse
import socket
import subprocess
import math
import xml.etree.ElementTree as ET
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from email.utils import parsedate_to_datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request, jsonify, redirect, url_for, render_template, render_template_string, send_file, Response
from flask_socketio import SocketIO, emit
from functools import wraps
from collections import deque
import websocket
import ssl
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ----------------------
# Enhanced Logging Setup
# ----------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        RotatingFileHandler('mapper.log', maxBytes=50*1024*1024, backupCount=3),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Add a debug mode flag that can be toggled
DEBUG_MODE = False

def set_debug_mode(enabled=True):
    """Enable or disable debug logging"""
    global DEBUG_MODE
    DEBUG_MODE = enabled
    if enabled:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("Debug logging enabled")
    else:
        logging.getLogger().setLevel(logging.INFO)
        logger.info("Debug logging disabled")

# ----------------------
# Global Configuration
# ----------------------
HEADLESS_MODE = False
AUTO_START_ENABLED = True
PORT_MONITOR_INTERVAL = 10  # seconds
SHUTDOWN_EVENT = threading.Event()
LIGHTNING_DETECTION_ENABLED = True  # Lightning detection enabled by default
LIGHTNING_WS_CONNECTION = None  # Track WebSocket connection for cleanup
AIS_DETECTION_ENABLED = True  # Maritime AIS detection enabled by default
AIS_VESSELS = {}  # Store current AIS vessel data: {mmsi: vessel_data}
AIS_UPDATE_INTERVAL = 60  # Update AIS data every 60 seconds
AIS_WS_CONNECTION = None  # Track WebSocket connection for AIS data
AIS_API_KEY = os.environ.get('AIS_API_KEY', '')  # Optional API key for AIS services
AIS_USE_GRID_SEARCH = os.environ.get('AIS_USE_GRID_SEARCH', 'false').lower() == 'true'  # Use grid search for comprehensive coverage
PORT_DATA_ENABLED = True  # Port data enabled by default
PORTS = {}  # Store current port data: {port_id: port_data}
PORT_UPDATE_INTERVAL = 3600  # Update port data every hour (ports don't change frequently)
APRS_DETECTION_ENABLED = True  # APRS station detection enabled by default
APRS_STATIONS = {}  # Store current APRS station data: {callsign: station_data}
APRS_UPDATE_INTERVAL = 120  # Update APRS data every 120 seconds
APRS_API_KEY = os.environ.get('APRS_API_KEY', '')  # API key from aprs.fi
ADSB_DETECTION_ENABLED = True  # ADSB aircraft detection enabled by default
ADSB_AIRCRAFT = {}  # Store current ADSB aircraft data: {hex: aircraft_data}
ADSB_UPDATE_INTERVAL = 30  # Update ADSB data every 30 seconds (was 5s, reduced to avoid 429 rate limiting)
ADSB_CENTER_LAT = 56.5  # Center latitude for ADSB search (Scotland center, near Edinburgh)
ADSB_CENTER_LON = -4.0  # Center longitude for ADSB search (Scotland center)
ADSB_RADIUS_KM = 1000  # Search radius in kilometers (maximum coverage - covers UK and surrounding areas)
WEATHER_ENABLED = True  # Weather data enabled by default
WEATHER_DATA = {}  # Store current weather data: {location_key: weather_data}
WEATHER_UPDATE_INTERVAL = 300  # Update weather data every 5 minutes
WEATHER_API_KEY = os.environ.get('WINDY_API_KEY', '')  # API key from Windy.com
WEATHER_LOCATIONS = []  # List of locations to fetch weather for: [{"lat": float, "lon": float, "name": str}]
WEBCAMS_ENABLED = True  # Webcams enabled by default
WEBCAMS_DATA = {}  # Store current webcam data: {webcam_id: webcam_data}
WEBCAMS_UPDATE_INTERVAL = 600  # Update webcams every 10 minutes
WEBCAMS_API_KEY = os.environ.get('WINDY_WEBCAMS_API_KEY', '')  # API key from Windy.com for webcams
METOFFICE_WARNINGS_ENABLED = True  # Met Office weather warnings enabled by default
METOFFICE_WARNINGS = {}  # Store current weather warnings: {warning_id: warning_data}
METOFFICE_UPDATE_INTERVAL = 1800  # Update weather warnings every 30 minutes
METOFFICE_RSS_URL = 'https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/UK'
METOFFICE_GEOJSON_URL = 'https://www.metoffice.gov.uk/public/data/NSWWS/WarningsJSON'  # Met Office NSWWS API (GeoJSON format)

# ----------------------
# Thread Locks for shared global dicts
# ----------------------
ADSB_AIRCRAFT_LOCK = threading.Lock()
AIS_VESSELS_LOCK = threading.Lock()
APRS_STATIONS_LOCK = threading.Lock()

# ----------------------
# Exponential backoff state for rate-limited APIs
# ----------------------
_adsb_backoff_interval = ADSB_UPDATE_INTERVAL  # current ADSB poll interval (doubles on 429)
_ais_backoff_interval = AIS_UPDATE_INTERVAL
_aprs_backoff_interval = APRS_UPDATE_INTERVAL
_MAX_BACKOFF = 120  # max seconds between retries after 429

# ----------------------
# Stale data thresholds (seconds)
# ----------------------
ADSB_STALE_SECONDS = 300    # 5 minutes
AIS_STALE_SECONDS = 1800    # 30 minutes
APRS_STALE_SECONDS = 3600   # 60 minutes

# ----------------------
# Performance Optimizations
# ----------------------
MAX_DETECTION_HISTORY = 1000  # Limit detection history size
MAX_FAA_CACHE_SIZE = 500      # Limit FAA cache size
KML_GENERATION_INTERVAL = 30  # Only regenerate KML every 30 seconds
last_kml_generation = 0
last_cumulative_kml_generation = 0

def cleanup_old_detections():
    """Mark stale detections as inactive instead of removing them to preserve session persistence.
    Also prune stale data from ADSB_AIRCRAFT, AIS_VESSELS, and APRS_STATIONS dicts."""
    global ADSB_AIRCRAFT, AIS_VESSELS, APRS_STATIONS
    current_time = time.time()
    
    for mac, detection in tracked_pairs.items():
        last_update = detection.get('last_update', 0)
        # Instead of deleting, mark as inactive for very old detections (30+ minutes)
        if current_time - last_update > staleThreshold * 30:  # 30x stale threshold (30 minutes)
            detection['status'] = 'inactive_old'  # Mark as very old but keep in session
        elif current_time - last_update > staleThreshold * 3:  # 3x stale threshold (3 minutes)
            detection['status'] = 'inactive'  # Mark as inactive but keep in session
    
    # Only clean up FAA cache, but keep drone detections for session persistence
    if len(FAA_CACHE) > MAX_FAA_CACHE_SIZE:
        keys_to_remove = list(FAA_CACHE.keys())[:100]
        for key in keys_to_remove:
            del FAA_CACHE[key]
    
    # Prune stale ADSB aircraft (not seen in 5 minutes)
    with ADSB_AIRCRAFT_LOCK:
        stale_adsb = [k for k, v in ADSB_AIRCRAFT.items()
                      if current_time - v.get('last_seen', v.get('timestamp', 0)) > ADSB_STALE_SECONDS]
        for k in stale_adsb:
            del ADSB_AIRCRAFT[k]
        if stale_adsb:
            logger.info(f"Pruned {len(stale_adsb)} stale ADSB aircraft (>{ADSB_STALE_SECONDS}s)")

    # Prune stale AIS vessels (not seen in 30 minutes)
    with AIS_VESSELS_LOCK:
        stale_ais = [k for k, v in AIS_VESSELS.items()
                     if current_time - v.get('timestamp', 0) > AIS_STALE_SECONDS]
        for k in stale_ais:
            del AIS_VESSELS[k]
        if stale_ais:
            logger.info(f"Pruned {len(stale_ais)} stale AIS vessels (>{AIS_STALE_SECONDS}s)")

    # Prune stale APRS stations (not seen in 60 minutes)
    with APRS_STATIONS_LOCK:
        stale_aprs = [k for k, v in APRS_STATIONS.items()
                      if current_time - v.get('lasttime', v.get('time', 0)) > APRS_STALE_SECONDS]
        for k in stale_aprs:
            del APRS_STATIONS[k]
        if stale_aprs:
            logger.info(f"Pruned {len(stale_aprs)} stale APRS stations (>{APRS_STALE_SECONDS}s)")

    # Clean up expired NOTAM zones
    filter_expired_notam_zones()

def start_cleanup_timer():
    """Start periodic cleanup every 5 minutes"""
    def cleanup_timer():
        while not SHUTDOWN_EVENT.is_set():
            cleanup_old_detections()
            time.sleep(300)  # 5 minutes
    
    cleanup_thread = threading.Thread(target=cleanup_timer, daemon=True)
    cleanup_thread.start()
    logger.info("Cleanup timer started")

def start_openair_updater():
    """Start periodic OpenAir airspace data updates (daily)"""
    def openair_updater():
        # Wait 30 seconds after startup before first check
        time.sleep(30)
        
        while not SHUTDOWN_EVENT.is_set():
            try:
                # Check if file exists and is older than 24 hours
                update_needed = False
                
                if not os.path.exists(OPENAIR_FILE):
                    update_needed = True
                    logger.info("OpenAir file not found, will download")
                else:
                    # Check file age
                    file_age = time.time() - os.path.getmtime(OPENAIR_FILE)
                    if file_age > 86400:  # 24 hours
                        update_needed = True
                        logger.info(f"OpenAir file is {file_age/3600:.1f} hours old, will update")
                
                if update_needed:
                    logger.info("Updating zones from OpenAir data...")
                    update_zones_from_openair(max_altitude_ft=400, merge_with_existing=True)
                    logger.info("OpenAir zones updated successfully")
                
            except Exception as e:
                logger.error(f"Error in OpenAir updater: {e}")
            
            # Check again in 24 hours
            time.sleep(86400)  # 24 hours
    
    updater_thread = threading.Thread(target=openair_updater, daemon=True)
    updater_thread.start()
    logger.info("OpenAir updater started (daily checks)")

def start_notam_updater():
    """Start periodic NOTAM data updates (every 6 hours)"""
    def notam_updater():
        # Wait 60 seconds after startup before first check
        time.sleep(60)
        
        while not SHUTDOWN_EVENT.is_set():
            try:
                # Check if file exists and is older than 6 hours (NOTAMs change more frequently)
                update_needed = False
                
                if not os.path.exists(NOTAM_FILE):
                    update_needed = True
                    logger.info("NOTAM file not found, will download")
                else:
                    # Check file age
                    file_age = time.time() - os.path.getmtime(NOTAM_FILE)
                    if file_age > 21600:  # 6 hours
                        update_needed = True
                        logger.info(f"NOTAM file is {file_age/3600:.1f} hours old, will update")
                
                if update_needed:
                    logger.info("Updating zones from NOTAM data...")
                    update_zones_from_notam(max_altitude_ft=400, merge_with_existing=True)
                    logger.info("NOTAM zones updated successfully")
                
            except Exception as e:
                logger.error(f"Error in NOTAM updater: {e}")
            
            # Check again in 6 hours
            time.sleep(21600)  # 6 hours
    
    updater_thread = threading.Thread(target=notam_updater, daemon=True)
    updater_thread.start()
    logger.info("NOTAM updater started (6-hour checks)")

# ----------------------
# Maritime AIS Data Functions
# ----------------------
def fetch_ais_data_uk(bounds=None, use_grid=False):
    """Fetch AIS vessel data for UK waters using public APIs
    
    Args:
        bounds: Optional dict with 'north', 'south', 'east', 'west' keys
                If None, uses default UK bounding box
        use_grid: If True, divides area into grid cells for comprehensive coverage
    Returns:
        List of vessel dictionaries with AIS data
    """
    global AIS_VESSELS
    
    # Default UK bounding box (approximate)
    if bounds is None:
        bounds = {
            'north': 61.0,  # Northern Scotland
            'south': 49.5,  # Southern England
            'east': 2.0,    # Eastern England
            'west': -11.0   # Western Ireland/Atlantic
        }
    
    vessels = []
    
    # If using grid approach, divide UK waters into smaller cells
    if use_grid:
        grid_size_lat = 2.0  # 2 degree latitude cells
        grid_size_lon = 2.0  # 2 degree longitude cells
        
        lat_start = bounds['south']
        lon_start = bounds['west']
        
        grid_cells = []
        current_lat = lat_start
        while current_lat < bounds['north']:
            current_lon = lon_start
            while current_lon < bounds['east']:
                grid_cells.append({
                    'south': current_lat,
                    'north': min(current_lat + grid_size_lat, bounds['north']),
                    'west': current_lon,
                    'east': min(current_lon + grid_size_lon, bounds['east'])
                })
                current_lon += grid_size_lon
            current_lat += grid_size_lat
        
        logger.info(f"Querying {len(grid_cells)} grid cells for comprehensive vessel coverage")
        seen_mmsis = set()
        
        for i, cell_bounds in enumerate(grid_cells):
            try:
                cell_vessels = fetch_ais_data_uk(bounds=cell_bounds, use_grid=False)
                for vessel in cell_vessels:
                    mmsi = vessel.get('mmsi')
                    if mmsi and mmsi not in seen_mmsis:
                        seen_mmsis.add(mmsi)
                        vessels.append(vessel)
                if (i + 1) % 10 == 0:
                    logger.info(f"Processed {i + 1}/{len(grid_cells)} grid cells, found {len(vessels)} unique vessels")
                time.sleep(0.2)  # Rate limiting between cells
            except Exception as e:
                logger.debug(f"Error fetching grid cell {i+1}: {e}")
        
        logger.info(f"Grid search complete: {len(vessels)} unique vessels found")
        return vessels
    
    try:
        session = create_retry_session()
        
        # Try multiple AIS data sources in order of preference
        
        # Option 1: Try Marinesia API (comprehensive maritime data)
        marinesia_key = os.environ.get('MARINESIA_API_KEY', '')
        if marinesia_key:
            try:
                # First try the nearby endpoint for the full bounding box
                url = "https://api.marinesia.com/api/v1/vessel/nearby"
                params = {
                    'lat_min': bounds['south'],
                    'lat_max': bounds['north'],
                    'long_min': bounds['west'],
                    'long_max': bounds['east'],
                    'key': marinesia_key
                }
                response = session.get(url, params=params, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    if not data.get('error', False) and 'data' in data:
                        seen_mmsis = set()
                        for vessel in data['data']:
                            # Marinesia API returns: name, type, flag, a, b, c, d (dimensions), mmsi, lat, lng, cog, sog, rot, hdt, dest, eta, ts, status
                            mmsi = str(vessel.get('mmsi', ''))
                            if mmsi and mmsi not in seen_mmsis:
                                seen_mmsis.add(mmsi)
                                # Calculate length and width from dimensions (a+b, c+d)
                                length = float(vessel.get('a', 0) + vessel.get('b', 0))
                                width = float(vessel.get('c', 0) + vessel.get('d', 0))
                                
                                vessels.append({
                                    'mmsi': mmsi,
                                    'name': vessel.get('name', 'Unknown'),
                                    'lat': float(vessel.get('lat', 0)),
                                    'lon': float(vessel.get('lng', 0)),
                                    'course': float(vessel.get('cog', 0)),  # Course Over Ground
                                    'speed': float(vessel.get('sog', 0)),   # Speed Over Ground
                                    'heading': int(vessel.get('hdt', 0)),   # Heading
                                    'vessel_type': vessel.get('type', 'Unknown'),
                                    'flag': vessel.get('flag', ''),
                                    'destination': vessel.get('dest', ''),
                                    'eta': vessel.get('eta', ''),
                                    'length': length,
                                    'width': width,
                                    'timestamp': time.time(),
                                    'source': 'marinesia'
                                })
                        
                        # If we got results, also try to get all vessels using paginated location endpoint
                        # This ensures we don't miss any vessels due to API limits
                        try:
                            url_location = "https://api.marinesia.com/api/v1/vessel/location"
                            page = 1
                            max_pages = 100  # Limit to prevent excessive API calls
                            limit = 10  # Max allowed by API
                            
                            while page <= max_pages:
                                params_location = {
                                    'page': page,
                                    'limit': limit,
                                    'key': marinesia_key
                                }
                                response_location = session.get(url_location, params=params_location, timeout=30)
                                if response_location.status_code == 200:
                                    data_location = response_location.json()
                                    if data_location.get('error', False) or 'data' not in data_location:
                                        break
                                    
                                    page_vessels = 0
                                    for vessel_loc in data_location.get('data', []):
                                        mmsi = str(vessel_loc.get('mmsi', ''))
                                        lat = float(vessel_loc.get('lat', 0))
                                        lon = float(vessel_loc.get('lng', 0))
                                        
                                        # Check if vessel is within UK bounds and not already added
                                        if (mmsi and mmsi not in seen_mmsis and 
                                            bounds['south'] <= lat <= bounds['north'] and
                                            bounds['west'] <= lon <= bounds['east']):
                                            seen_mmsis.add(mmsi)
                                            vessels.append({
                                                'mmsi': mmsi,
                                                'name': vessel_loc.get('name', 'Unknown'),
                                                'lat': lat,
                                                'lon': lon,
                                                'course': float(vessel_loc.get('cog', 0)),
                                                'speed': float(vessel_loc.get('sog', 0)),
                                                'heading': int(vessel_loc.get('hdt', 0)),
                                                'vessel_type': vessel_loc.get('type', 'Unknown'),
                                                'flag': vessel_loc.get('flag', ''),
                                                'destination': vessel_loc.get('dest', ''),
                                                'eta': vessel_loc.get('eta', ''),
                                                'length': float(vessel_loc.get('a', 0) + vessel_loc.get('b', 0)),
                                                'width': float(vessel_loc.get('c', 0) + vessel_loc.get('d', 0)),
                                                'timestamp': time.time(),
                                                'source': 'marinesia_location'
                                            })
                                            page_vessels += 1
                                    
                                    # Check if there are more pages
                                    meta = data_location.get('meta', {})
                                    total_pages = meta.get('total_pages', 0)
                                    if page >= total_pages or page_vessels == 0:
                                        break
                                    
                                    page += 1
                                    time.sleep(0.5)  # Rate limiting
                                else:
                                    break
                        except Exception as e:
                            logger.debug(f"Marinesia location pagination error (non-critical): {e}")
                        
                        logger.info(f"Fetched {len(vessels)} total vessels from Marinesia API (nearby + paginated location)")
                        return vessels
            except Exception as e:
                logger.debug(f"Marinesia API error: {e}")
        
        # Option 2: Try Datalastic API (free tier available)
        # Requires API key from https://datalastic.com
        datalastic_key = os.environ.get('DATALASTIC_API_KEY', '')
        if datalastic_key:
            try:
                url = "https://api.datalastic.com/api/v0/vessel_in_area"
                params = {
                    'api-key': datalastic_key,
                    'bbox': f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}",
                    'format': 'json'
                }
                response = session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if 'data' in data and 'vessels' in data['data']:
                        for vessel in data['data']['vessels']:
                            vessels.append({
                                'mmsi': str(vessel.get('mmsi', '')),
                                'name': vessel.get('name', 'Unknown'),
                                'lat': float(vessel.get('lat', 0)),
                                'lon': float(vessel.get('lon', 0)),
                                'course': float(vessel.get('course', 0)),
                                'speed': float(vessel.get('speed', 0)),
                                'heading': int(vessel.get('heading', 0)),
                                'vessel_type': vessel.get('type', 'Unknown'),
                                'length': float(vessel.get('length', 0)),
                                'width': float(vessel.get('width', 0)),
                                'timestamp': time.time()
                            })
                        logger.info(f"Fetched {len(vessels)} vessels from Datalastic API")
                        return vessels
            except Exception as e:
                logger.debug(f"Datalastic API error: {e}")
        
        # Option 3: Try MarineTraffic API (requires API key)
        marine_traffic_key = os.environ.get('MARINE_TRAFFIC_API_KEY', '')
        if marine_traffic_key:
            try:
                url = f"https://services.marinetraffic.com/api/exportvessels/v:8/APIkey:{marine_traffic_key}/timespan:10/protocol:jsono"
                params = {
                    'minlat': bounds['south'],
                    'maxlat': bounds['north'],
                    'minlon': bounds['west'],
                    'maxlon': bounds['east']
                }
                response = session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        for vessel in data:
                            vessels.append({
                                'mmsi': str(vessel.get('MMSI', '')),
                                'name': vessel.get('SHIPNAME', 'Unknown'),
                                'lat': float(vessel.get('LAT', 0)),
                                'lon': float(vessel.get('LON', 0)),
                                'course': float(vessel.get('COURSE', 0)),
                                'speed': float(vessel.get('SPEED', 0)),
                                'heading': int(vessel.get('HEADING', 0)),
                                'vessel_type': vessel.get('TYPE', 'Unknown'),
                                'length': float(vessel.get('LENGTH', 0)),
                                'width': float(vessel.get('WIDTH', 0)),
                                'timestamp': time.time()
                            })
                        logger.info(f"Fetched {len(vessels)} vessels from MarineTraffic API")
                        return vessels
            except Exception as e:
                logger.debug(f"MarineTraffic API error: {e}")
        
        # Option 4: Try AISHub (community-driven, requires sharing your own AIS data)
        # This is a community service - you need to contribute data to access
        
        # Option 5: Use WebSocket feed for real-time data (handled separately)
        # The WebSocket connection is managed by start_ais_websocket()
        
        logger.debug(f"No AIS API configured or all APIs failed. Configure MARINESIA_API_KEY, DATALASTIC_API_KEY or MARINE_TRAFFIC_API_KEY environment variable.")
        return []
        
    except Exception as e:
        logger.error(f"Error fetching AIS data: {e}")
        return []

def update_ais_data():
    """Update AIS vessel data and emit to clients"""
    global AIS_VESSELS, AIS_DETECTION_ENABLED
    
    if not AIS_DETECTION_ENABLED:
        return
    
    try:
        # Get current map bounds if available, otherwise use UK default
        # Use grid search if enabled for comprehensive coverage
        vessels = fetch_ais_data_uk(use_grid=AIS_USE_GRID_SEARCH)
        
        # Update global vessel store
        new_vessels = {}
        for vessel in vessels:
            mmsi = vessel.get('mmsi')
            if mmsi:
                new_vessels[mmsi] = vessel
        
        with AIS_VESSELS_LOCK:
            AIS_VESSELS = new_vessels
        
        # Emit to connected clients
        try:
            with AIS_VESSELS_LOCK:
                vessels_emit = list(AIS_VESSELS.values())
            socketio.emit('ais_vessels', {'vessels': vessels_emit})
            logger.info(f"Emitted {len(vessels_emit)} AIS vessels to clients")
        except Exception as e:
            logger.debug(f"Error emitting AIS vessels: {e}")
            
    except Exception as e:
        logger.error(f"Error updating AIS data: {e}")

def start_ais_updater():
    """Start periodic AIS data updates via REST API"""
    def ais_updater():
        # Wait 10 seconds after startup before first update
        time.sleep(10)
        
        while not SHUTDOWN_EVENT.is_set():
            if AIS_DETECTION_ENABLED:
                try:
                    update_ais_data()
                except Exception as e:
                    logger.error(f"Error in AIS updater: {e}")
            
            # Wait for next update interval
            time.sleep(AIS_UPDATE_INTERVAL)
    
    updater_thread = threading.Thread(target=ais_updater, daemon=True)
    updater_thread.start()
    logger.info(f"AIS REST API updater started (updates every {AIS_UPDATE_INTERVAL} seconds)")

def start_ais_websocket():
    """Start real-time AIS data feed via WebSocket (aisstream.io)"""
    global AIS_WS_CONNECTION, AIS_API_KEY
    
    def ais_websocket_thread():
        global AIS_WS_CONNECTION, AIS_API_KEY
        # Get API key from environment variable or config file
        api_key = os.environ.get('AISSTREAM_API_KEY') or os.environ.get('AIS_API_KEY') or AIS_API_KEY
        
        if not api_key:
            logger.info("AIS API key not configured, skipping WebSocket feed. Set AISSTREAM_API_KEY environment variable or configure in ais_config.json")
            return
        
        ws_url = f"wss://stream.aisstream.io/v0/stream"
        reconnect_delay = 5
        max_reconnect_delay = 60
        
        while not SHUTDOWN_EVENT.is_set():
            if not AIS_DETECTION_ENABLED:
                time.sleep(5)
                continue
                
            try:
                logger.info("Connecting to AISStream.io WebSocket...")
                
                def on_message(ws, message):
                    try:
                        data = json.loads(message)
                        
                        # Log all received messages to see what we're getting
                        logger.info(f"Received AIS WebSocket message: {json.dumps(data)[:200]}...")
                        
                        # Debug: log message type
                        if 'MessageType' in data:
                            msg_type = data['MessageType']
                            logger.info(f"Received AIS message type: {msg_type}")
                            
                            # Position Report (Type 1, 2, 3)
                            if msg_type in ['PositionReport', 'PositionReportClassA', 'PositionReportClassB']:
                                process_ais_message(data)
                            # Static Data (Type 5)
                            elif msg_type == 'StaticData':
                                process_ais_static_data(data)
                            else:
                                logger.debug(f"Unhandled AIS message type: {msg_type}")
                        else:
                            # Log unexpected message format
                            logger.warning(f"Received AIS message without MessageType. Keys: {list(data.keys())}")
                            logger.debug(f"Full message: {json.dumps(data)[:500]}")
                                
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse AIS message as JSON: {e}")
                        logger.debug(f"Raw message: {message[:200]}")
                    except Exception as e:
                        logger.error(f"Error processing AIS message: {e}")
                
                def on_error(ws, error):
                    logger.warning(f"AIS WebSocket error: {error}")
                
                def on_close(ws, close_status_code, close_msg):
                    if not SHUTDOWN_EVENT.is_set():
                        logger.warning("AIS WebSocket closed, will reconnect...")
                
                def on_open(ws):
                    logger.info("Connected to AISStream.io WebSocket feed")
                    # Subscribe to UK waters bounding box
                    subscribe_msg = {
                        "APIKey": api_key,
                        "BoundingBoxes": [[
                            [-11.0, 49.5],  # Southwest corner
                            [2.0, 61.0]     # Northeast corner (UK waters)
                        ]]
                    }
                    logger.info(f"Sending subscription message: {json.dumps(subscribe_msg)}")
                    ws.send(json.dumps(subscribe_msg))
                    logger.info("Subscribed to UK waters AIS feed")
                
                ws = websocket.WebSocketApp(
                    ws_url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_open=on_open
                )
                AIS_WS_CONNECTION = ws
                ws.run_forever()
                AIS_WS_CONNECTION = None
                
            except Exception as e:
                logger.error(f"AIS WebSocket connection error: {e}")
            
            if not SHUTDOWN_EVENT.is_set():
                delay = min(reconnect_delay, max_reconnect_delay)
                logger.info(f"Reconnecting to AISStream.io in {delay} seconds...")
                time.sleep(delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
    
    websocket_thread = threading.Thread(target=ais_websocket_thread, daemon=True)
    websocket_thread.start()
    logger.info("AIS WebSocket feed started (aisstream.io)")

def process_ais_message(ais_data):
    """Process an AIS position message and update vessel data"""
    global AIS_VESSELS
    
    try:
        message = ais_data.get('Message', {})
        mmsi = str(message.get('UserID', ''))
        
        if not mmsi:
            logger.debug("AIS message missing UserID")
            return
        
        # Extract position data
        lat = float(message.get('Latitude', 0))
        lon = float(message.get('Longitude', 0))
        course = float(message.get('CourseOverGround', 0))
        speed = float(message.get('SpeedOverGround', 0)) / 10.0  # Convert from 0.1 knots to knots
        heading = int(message.get('TrueHeading', 0))
        
        if lat == 0 and lon == 0:
            logger.debug(f"AIS message for MMSI {mmsi} has invalid position")
            return  # Invalid position
        
        # Update or create vessel entry
        with AIS_VESSELS_LOCK:
            vessel = AIS_VESSELS.get(mmsi, {})
            vessel.update({
                'mmsi': mmsi,
                'lat': lat,
                'lon': lon,
                'course': course,
                'speed': speed,
                'heading': heading if heading != 511 else 0,  # 511 = not available
                'timestamp': time.time()
            })
            
            # Preserve static data if it exists
            if 'name' not in vessel:
                vessel['name'] = f"Vessel {mmsi}"
            if 'vessel_type' not in vessel:
                vessel['vessel_type'] = 'Unknown'
            
            AIS_VESSELS[mmsi] = vessel
        logger.info(f"Updated AIS vessel {mmsi} ({vessel.get('name', 'Unknown')}) at {lat}, {lon}")
        
        # Save to database
        try:
            save_ais_vessel_to_db(vessel)
        except Exception as e:
            logger.debug(f"Error saving AIS vessel to database: {e}")
        
        # Emit update to clients
        try:
            socketio.emit('ais_vessel_update', vessel)
        except Exception as e:
            logger.debug(f"Error emitting AIS vessel update: {e}")
            
    except Exception as e:
        logger.error(f"Error processing AIS message: {e}")

def process_ais_static_data(ais_data):
    """Process AIS static data (vessel name, type, dimensions)"""
    global AIS_VESSELS
    
    try:
        message = ais_data.get('Message', {})
        mmsi = str(message.get('UserID', ''))
        
        if not mmsi:
            return
        
        # Extract static data
        name = message.get('Name', '').strip()
        vessel_type = message.get('Type', 0)
        length = float(message.get('Dimension', {}).get('A', 0) + message.get('Dimension', {}).get('B', 0))
        width = float(message.get('Dimension', {}).get('C', 0) + message.get('Dimension', {}).get('D', 0))
        
        # Update vessel with static data
        with AIS_VESSELS_LOCK:
            vessel = AIS_VESSELS.get(mmsi, {})
            if name:
                vessel['name'] = name
            if vessel_type:
                # Map AIS vessel type codes to names (simplified)
                type_map = {
                    30: 'Fishing', 31: 'Towing', 32: 'Towing (long)', 33: 'Dredging',
                    34: 'Diving', 35: 'Military', 36: 'Sailing', 37: 'Pleasure Craft',
                    50: 'Pilot', 51: 'Search and Rescue', 52: 'Tug', 53: 'Port Tender',
                    54: 'Anti-pollution', 55: 'Law Enforcement', 58: 'Medical',
                    59: 'Passenger', 60: 'Passenger (hazardous)', 70: 'Cargo',
                    71: 'Cargo (hazardous)', 72: 'Tanker', 73: 'Tanker (hazardous)',
                    80: 'Other'
                }
                vessel['vessel_type'] = type_map.get(vessel_type, f'Type {vessel_type}')
            if length > 0:
                vessel['length'] = length
            if width > 0:
                vessel['width'] = width
            
            vessel['mmsi'] = mmsi
            AIS_VESSELS[mmsi] = vessel
        
        # Emit update to clients
        try:
            socketio.emit('ais_vessel_update', vessel)
        except Exception as e:
            logger.debug(f"Error emitting AIS vessel update: {e}")
            
    except Exception as e:
        logger.error(f"Error processing AIS static data: {e}")

# ----------------------
# Port Data Functions (Marinesia API)
# ----------------------
def fetch_ports_data(bounds=None):
    """Fetch port data for a given area using Marinesia API
    
    Args:
        bounds: Optional dict with 'north', 'south', 'east', 'west' keys
                If None, uses default UK bounding box
    Returns:
        List of port dictionaries with port data
    """
    global PORTS
    
    # Default UK bounding box (approximate)
    if bounds is None:
        bounds = {
            'north': 61.0,  # Northern Scotland
            'south': 49.5,  # Southern England
            'east': 2.0,    # Eastern England
            'west': -11.0   # Western Ireland/Atlantic
        }
    
    ports = []
    
    try:
        marinesia_key = os.environ.get('MARINESIA_API_KEY', '')
        if not marinesia_key:
            logger.debug("MARINESIA_API_KEY not configured, skipping port data fetch")
            return []
        
        session = create_retry_session()
        url = "https://api.marinesia.com/api/v1/port/nearby"
        params = {
            'lat_min': bounds['south'],
            'lat_max': bounds['north'],
            'long_min': bounds['west'],
            'long_max': bounds['east'],
            'key': marinesia_key
        }
        
        response = session.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if not data.get('error', False) and 'data' in data:
                for port in data['data']:
                    port_id = str(port.get('id', ''))
                    if port_id:
                        ports.append({
                            'id': port_id,
                            'name': port.get('name', 'Unknown Port'),
                            'lat': float(port.get('lat', 0)),
                            'lon': float(port.get('lng', 0)),
                            'country': port.get('country', ''),
                            'type': port.get('type', ''),
                            'timestamp': time.time(),
                            'source': 'marinesia'
                        })
                logger.info(f"Fetched {len(ports)} ports from Marinesia API")
                return ports
        else:
            logger.warning(f"Marinesia port API returned status {response.status_code}")
            
    except Exception as e:
        logger.error(f"Error fetching port data: {e}")
    
    return []

def update_ports_data():
    """Update port data and emit to clients"""
    global PORTS, PORT_DATA_ENABLED
    
    if not PORT_DATA_ENABLED:
        return
    
    try:
        # Get current map bounds if available, otherwise use UK default
        ports = fetch_ports_data()
        
        # Update global port store
        new_ports = {}
        for port in ports:
            port_id = port.get('id')
            if port_id:
                new_ports[port_id] = port
        
        PORTS = new_ports
        
        # Emit to connected clients
        try:
            socketio.emit('ports', {'ports': list(PORTS.values())})
            logger.info(f"Emitted {len(PORTS)} ports to clients")
        except Exception as e:
            logger.debug(f"Error emitting ports: {e}")
            
    except Exception as e:
        logger.error(f"Error updating port data: {e}")

def start_ports_updater():
    """Start periodic port data updates"""
    def ports_updater():
        # Wait 30 seconds after startup before first update
        time.sleep(30)
        
        while not SHUTDOWN_EVENT.is_set():
            if PORT_DATA_ENABLED:
                try:
                    update_ports_data()
                except Exception as e:
                    logger.error(f"Error in ports updater: {e}")
            
            # Wait for next update interval
            time.sleep(PORT_UPDATE_INTERVAL)
    
    updater_thread = threading.Thread(target=ports_updater, daemon=True)
    updater_thread.start()
    logger.info(f"Ports updater started (updates every {PORT_UPDATE_INTERVAL} seconds)")

# ----------------------
# Met Office Weather Warnings Functions
# ----------------------
def extract_polygons_from_geojson_geometry(geometry):
    """Extract polygon coordinates from GeoJSON geometry (Polygon or MultiPolygon)
    
    Args:
        geometry: GeoJSON geometry object with type and coordinates
    
    Returns:
        List of polygon coordinate lists: [[[lat, lon], ...], ...]
    """
    polygons = []
    
    if not geometry or 'type' not in geometry or 'coordinates' not in geometry:
        return polygons
    
    geom_type = geometry.get('type', '').lower()
    coordinates = geometry.get('coordinates', [])
    
    if geom_type == 'polygon':
        # Polygon coordinates format: [[[lon, lat], ...], ...]
        # First ring is outer boundary, rest are holes - we only want the outer boundary
        if coordinates and len(coordinates) > 0:
            outer_ring = coordinates[0]
            if len(outer_ring) >= 3:
                # Convert [lon, lat] to [lat, lon] for Leaflet
                polygon_coords = [[coord[1], coord[0]] for coord in outer_ring]
                polygons.append(polygon_coords)
    
    elif geom_type == 'multipolygon':
        # MultiPolygon coordinates: [[[[lon, lat], ...], ...], ...]
        for polygon in coordinates:
            if polygon and len(polygon) > 0:
                # First ring is outer boundary
                outer_ring = polygon[0]
                if len(outer_ring) >= 3:
                    # Convert [lon, lat] to [lat, lon] for Leaflet
                    polygon_coords = [[coord[1], coord[0]] for coord in outer_ring]
                    polygons.append(polygon_coords)
    
    return polygons

def fetch_metoffice_warnings():
    """Fetch UK weather warnings from Met Office NSWWS API (GeoJSON format)
    
    Returns:
        List of warning dictionaries with warning data including polygon coordinates
    """
    global METOFFICE_WARNINGS
    
    warnings = []
    
    try:
        # Try to fetch from NSWWS Public API (GeoJSON format)
        headers = {
            'User-Agent': 'mesh-mapper/1.0 (+https://github.com/mesh-mapper)',
            'Accept': 'application/json'
        }
        
        # Fetch GeoJSON from Met Office NSWWS API
        response = requests.get(METOFFICE_GEOJSON_URL, headers=headers, timeout=30)
        
        if response.status_code == 200:
            try:
                geojson_data = response.json()
                
                # GeoJSON FeatureCollection structure
                if geojson_data.get('type') == 'FeatureCollection':
                    features = geojson_data.get('features', [])
                    
                    for feature in features:
                        try:
                            properties = feature.get('properties', {})
                            geometry = feature.get('geometry', {})
                            
                            # Extract polygon coordinates from geometry
                            polygons = extract_polygons_from_geojson_geometry(geometry)
                            
                            if not polygons:
                                logger.debug(f"No polygons extracted from geometry for warning {warning_id}, geometry type: {geometry.get('type', 'unknown')}")
                                continue  # Skip warnings without valid polygons
                            
                            logger.debug(f"Extracted {len(polygons)} polygon(s) for warning {warning_id}: {properties.get('title', 'Unknown')}")
                            
                            # Extract warning information from properties
                            warning_id = properties.get('id') or properties.get('identifier') or properties.get('title', '')
                            
                            # Parse warning level (severityLevel or similar)
                            severity = properties.get('severityLevel', '').lower() or properties.get('severity', '').lower()
                            warning_level = 'yellow'  # default
                            if 'red' in severity:
                                warning_level = 'red'
                            elif 'amber' in severity or 'orange' in severity:
                                warning_level = 'amber'
                            elif 'yellow' in severity:
                                warning_level = 'yellow'
                            else:
                                # Try to get from title
                                title = properties.get('title', '').lower()
                                if 'red' in title:
                                    warning_level = 'red'
                                elif 'amber' in title:
                                    warning_level = 'amber'
                                elif 'yellow' in title:
                                    warning_level = 'yellow'
                            
                            # Parse weather type
                            weather_type = properties.get('weatherType', '').lower() or 'unknown'
                            if not weather_type or weather_type == 'unknown':
                                title = properties.get('title', '').lower()
                                weather_types = ['rain', 'thunderstorms', 'wind', 'snow', 'lightning', 'ice', 'extreme heat', 'fog']
                                for wt in weather_types:
                                    if wt in title:
                                        weather_type = wt
                                        break
                            
                            # Extract dates
                            start_time = None
                            end_time = None
                            try:
                                start_str = properties.get('validFrom') or properties.get('startTime')
                                end_str = properties.get('validTo') or properties.get('endTime')
                                
                                if start_str:
                                    # Try parsing ISO format or RFC 822
                                    try:
                                        start_time = parsedate_to_datetime(start_str).timestamp()
                                    except:
                                        try:
                                            # Try ISO format parsing
                                            dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                                            start_time = dt.timestamp()
                                        except:
                                            pass
                                if end_str:
                                    try:
                                        end_time = parsedate_to_datetime(end_str).timestamp()
                                    except:
                                        try:
                                            dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                                            end_time = dt.timestamp()
                                        except:
                                            pass
                            except:
                                pass
                            
                            warning = {
                                'id': warning_id or str(time.time()),
                                'title': properties.get('title', 'Weather Warning'),
                                'description': properties.get('description', '') or properties.get('message', ''),
                                'link': properties.get('link', '') or f'https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings',
                                'level': warning_level,
                                'weather_type': weather_type,
                                'affected_areas': properties.get('areas', []) or [],
                                'start_time': start_time,
                                'end_time': end_time,
                                'published': properties.get('issuedTime', ''),
                                'timestamp': time.time(),
                                'polygons': polygons  # Store polygon coordinates for map display
                            }
                            
                            warnings.append(warning)
                            
                        except Exception as e:
                            logger.debug(f"Error parsing Met Office warning feature: {e}")
                            continue
                
                logger.info(f"Fetched {len(warnings)} weather warnings from Met Office API (GeoJSON)")
                
            except json.JSONDecodeError as e:
                logger.warning(f"Error parsing Met Office GeoJSON response: {e}, falling back to RSS")
                # Fall through to RSS fallback
        
        # Fallback to RSS feed if API fails or returns no data
        if not warnings:
            logger.info("Falling back to RSS feed for weather warnings")
            response = requests.get(METOFFICE_RSS_URL, headers=headers, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Met Office RSS feed HTTP error: {response.status_code}")
                return []
            
            # Parse XML RSS feed (original code)
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            
            for item in items:
                try:
                    title = item.find('title')
                    title_text = title.text if title is not None else 'Unknown Warning'
                    
                    description = item.find('description')
                    description_text = description.text if description is not None else ''
                    
                    link = item.find('link')
                    link_text = link.text if link is not None else ''
                    
                    pub_date = item.find('pubDate')
                    pub_date_text = pub_date.text if pub_date is not None else ''
                    
                    guid = item.find('guid')
                    guid_text = guid.text if guid is not None else title_text
                    
                    warning_level = 'yellow'
                    if 'red' in title_text.lower():
                        warning_level = 'red'
                    elif 'amber' in title_text.lower():
                        warning_level = 'amber'
                    elif 'yellow' in title_text.lower():
                        warning_level = 'yellow'
                    
                    weather_type = 'unknown'
                    weather_types = ['rain', 'thunderstorms', 'wind', 'snow', 'lightning', 'ice', 'extreme heat', 'fog']
                    for wt in weather_types:
                        if wt in title_text.lower() or wt in description_text.lower():
                            weather_type = wt
                            break
                    
                    affected_areas = []
                    if description_text:
                        lines = description_text.split('\n')
                        for line in lines:
                            line = line.strip()
                            if line and len(line) > 2:
                                affected_areas.append(line)
                    
                    start_time = None
                    end_time = None
                    try:
                        if pub_date_text:
                            from email.utils import parsedate_to_datetime
                            start_time = parsedate_to_datetime(pub_date_text).timestamp()
                    except:
                        pass
                    
                    warning = {
                        'id': guid_text,
                        'title': title_text,
                        'description': description_text,
                        'link': link_text,
                        'level': warning_level,
                        'weather_type': weather_type,
                        'affected_areas': affected_areas[:10],
                        'start_time': start_time,
                        'end_time': end_time,
                        'published': pub_date_text,
                        'timestamp': time.time(),
                        'polygons': []  # No polygons from RSS feed
                    }
                    
                    warnings.append(warning)
                    
                except Exception as e:
                    logger.debug(f"Error parsing Met Office warning item: {e}")
                    continue
        
        logger.info(f"Fetched {len(warnings)} weather warnings from Met Office")
        return warnings
        
    except Exception as e:
        logger.error(f"Error fetching Met Office warnings: {e}")
        return []

def update_metoffice_warnings():
    """Update Met Office weather warnings and emit to clients"""
    global METOFFICE_WARNINGS, METOFFICE_WARNINGS_ENABLED
    
    if not METOFFICE_WARNINGS_ENABLED:
        return
    
    try:
        warnings = fetch_metoffice_warnings()
        
        # Update global warnings store
        new_warnings = {}
        for warning in warnings:
            warning_id = warning.get('id')
            if warning_id:
                new_warnings[warning_id] = warning
        
        METOFFICE_WARNINGS = new_warnings
        
        # Emit to connected clients
        try:
            socketio.emit('metoffice_warnings', {'warnings': list(METOFFICE_WARNINGS.values())})
            logger.debug(f"Emitted {len(METOFFICE_WARNINGS)} Met Office warnings to clients")
        except Exception as e:
            logger.debug(f"Error emitting Met Office warnings: {e}")
            
    except Exception as e:
        logger.error(f"Error updating Met Office warnings: {e}")

def start_metoffice_updater():
    """Start periodic Met Office warnings updates"""
    def metoffice_updater():
        # Wait 30 seconds after startup before first update
        time.sleep(30)
        
        while not SHUTDOWN_EVENT.is_set():
            if METOFFICE_WARNINGS_ENABLED:
                try:
                    update_metoffice_warnings()
                except Exception as e:
                    logger.error(f"Error in Met Office updater: {e}")
            
            # Wait for next update interval
            time.sleep(METOFFICE_UPDATE_INTERVAL)
    
    updater_thread = threading.Thread(target=metoffice_updater, daemon=True)
    updater_thread.start()
    logger.info(f"Met Office warnings updater started (updates every {METOFFICE_UPDATE_INTERVAL} seconds)")

# ----------------------
# APRS Data Functions
# ----------------------
def fetch_aprs_data(callsigns=None, bounds=None):
    """Fetch APRS station data from aprs.fi API
    Args:
        callsigns: List of callsigns to query (can query up to 20 at once)
        bounds: Optional bounding box dict with 'north', 'south', 'east', 'west' keys
    Returns:
        List of station dictionaries with APRS data
    """
    global APRS_STATIONS, APRS_API_KEY
    
    if not APRS_API_KEY:
        logger.warning("APRS API key not configured. Set APRS_API_KEY environment variable or configure in aprs_config.json")
        return []
    
    # For now, if no callsigns provided, return empty list
    # Future: Could implement area-based queries using aprs.fi's area search (requires different endpoint)
    if not callsigns:
        logger.debug("No APRS callsigns provided for query")
        return []
    
    # Batch callsigns (API supports up to 20 per request)
    all_stations = []
    batch_size = 20
    
    for i in range(0, len(callsigns), batch_size):
        batch = callsigns[i:i+batch_size]
        callsign_str = ','.join(batch)
        
        try:
            url = "https://api.aprs.fi/api/get"
            params = {
                "name": callsign_str,
                "what": "loc",
                "apikey": APRS_API_KEY,
                "format": "json"
            }
            
            # User-Agent header as required by API
            headers = {
                "User-Agent": "mesh-mapper/1.0 (+https://github.com/mesh-mapper)"
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=30)
            
            if response.status_code == 429:
                global _aprs_backoff_interval
                _aprs_backoff_interval = min(_aprs_backoff_interval * 2, _MAX_BACKOFF)
                logger.warning(f"APRS API rate limited (429). Backing off to {_aprs_backoff_interval}s")
                break  # Stop batching, wait for next cycle
            
            if response.status_code != 200:
                logger.error(f"APRS API HTTP error: {response.status_code} - {response.reason}")
                continue
            
            # Success — reset backoff
            _aprs_backoff_interval = APRS_UPDATE_INTERVAL
            
            data = response.json()
            
            if data.get("result") != "ok":
                error_desc = data.get("description", "Unknown error")
                logger.error(f"APRS API error: {error_desc}")
                continue
            
            entries = data.get("entries", [])
            
            for entry in entries:
                callsign = entry.get("name", "")
                if not callsign:
                    continue
                
                # Parse coordinates
                try:
                    lat = float(entry.get("lat", 0))
                    lng = float(entry.get("lng", 0))
                except (ValueError, TypeError):
                    continue
                
                if lat == 0 and lng == 0:
                    continue
                
                # Parse timestamps
                try:
                    time_ts = int(entry.get("time", 0))
                    lasttime_ts = int(entry.get("lasttime", 0))
                except (ValueError, TypeError):
                    time_ts = 0
                    lasttime_ts = 0
                
                station = {
                    "callsign": callsign,
                    "name": entry.get("showname") or callsign,
                    "type": entry.get("type", "l"),  # l=APRS station, i=item, o=object, w=weather
                    "lat": lat,
                    "lng": lng,
                    "altitude": float(entry.get("altitude", 0)) if entry.get("altitude") else None,
                    "course": float(entry.get("course", 0)) if entry.get("course") else None,
                    "speed": float(entry.get("speed", 0)) if entry.get("speed") else None,  # km/h
                    "symbol": entry.get("symbol", ""),
                    "comment": entry.get("comment", ""),
                    "status": entry.get("status", ""),
                    "srccall": entry.get("srccall", ""),
                    "path": entry.get("path", ""),
                    "phg": entry.get("phg", ""),
                    "time": time_ts,
                    "lasttime": lasttime_ts
                }
                
                all_stations.append(station)
            
            logger.debug(f"Fetched {len(entries)} APRS stations from batch")
            
        except requests.exceptions.Timeout:
            logger.warning(f"APRS API request timed out for callsigns: {callsign_str}")
        except requests.exceptions.RequestException as e:
            logger.error(f"APRS API request error: {e}")
        except Exception as e:
            logger.error(f"Error fetching APRS data: {e}")
    
    return all_stations

def update_aprs_data():
    """Update APRS station data and emit to clients"""
    global APRS_STATIONS, APRS_DETECTION_ENABLED
    
    try:
        if not APRS_DETECTION_ENABLED:
            return
        
        # Get callsigns from config file or use empty list
        callsigns = []
        if os.path.exists(APRS_CONFIG_FILE):
            try:
                with open(APRS_CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    callsigns = config.get("callsigns", [])
            except Exception as e:
                logger.error(f"Error reading APRS config for callsigns: {e}")
        
        if not callsigns:
            logger.debug("No APRS callsigns configured, skipping update")
            return
        
        stations = fetch_aprs_data(callsigns=callsigns)
        
        # Update global station data
        new_stations = {}
        for station in stations:
            callsign = station.get("callsign")
            if callsign:
                new_stations[callsign] = station
        
        with APRS_STATIONS_LOCK:
            APRS_STATIONS = new_stations
        
        # Save to database
        for station in stations:
            try:
                save_aprs_station_to_db(station)
            except Exception as e:
                logger.debug(f"Error saving APRS station to database: {e}")
        
        # Emit to connected clients
        try:
            with APRS_STATIONS_LOCK:
                stations_emit = list(APRS_STATIONS.values())
            socketio.emit('aprs_stations', {'stations': stations_emit})
            logger.debug(f"Emitted {len(stations_emit)} APRS stations to clients")
        except Exception as e:
            logger.debug(f"Error emitting APRS stations: {e}")
            
    except Exception as e:
        logger.error(f"Error updating APRS data: {e}")

def start_aprs_updater():
    """Start periodic APRS data updates (with exponential backoff on 429)"""
    def aprs_updater():
        while not SHUTDOWN_EVENT.is_set():
            try:
                if APRS_DETECTION_ENABLED:
                    try:
                        update_aprs_data()
                    except Exception as e:
                        logger.error(f"Error in APRS updater: {e}")
            except Exception as e:
                logger.error(f"Error in APRS updater loop: {e}")
            
            # Wait using backoff interval (increases on 429, resets on success)
            SHUTDOWN_EVENT.wait(_aprs_backoff_interval)
    
    updater_thread = threading.Thread(target=aprs_updater, daemon=True)
    updater_thread.start()
    logger.info(f"APRS updater started (updates every {APRS_UPDATE_INTERVAL} seconds)")

# ----------------------
# ADSB Aircraft Data Functions
# ----------------------
def fetch_adsb_data(lat, lon, radius_km=100):
    """Fetch ADSB aircraft data from airplanes.live API
    Args:
        lat: Center latitude
        lon: Center longitude
        radius_km: Search radius in kilometers
    Returns:
        List of aircraft dictionaries with ADSB data
    """
    global ADSB_AIRCRAFT, _adsb_backoff_interval
    
    try:
        # Convert km to nautical miles (API uses nautical miles)
        radius_nm = radius_km * 0.539957
        
        url = f"https://api.airplanes.live/v2/point/{lat}/{lon}/{radius_nm}"
        headers = {
            "User-Agent": "mesh-mapper/1.0"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 429:
            _adsb_backoff_interval = min(_adsb_backoff_interval * 2, _MAX_BACKOFF)
            logger.warning(f"ADSB API rate limited (429). Backing off to {_adsb_backoff_interval}s")
            return []
        
        if response.status_code != 200:
            logger.warning(f"ADSB API HTTP error: {response.status_code}")
            return []
        
        # Success — reset backoff to configured interval
        _adsb_backoff_interval = ADSB_UPDATE_INTERVAL
        
        data = response.json()
        
        # Log API response details
        total_in_response = data.get("total", 0)
        ac_list = data.get("ac", [])
        logger.info(f"ADSB API response: total={total_in_response}, aircraft in 'ac' array={len(ac_list) if isinstance(ac_list, list) else 0}")
        
        if not data.get("ac") or not isinstance(data.get("ac"), list):
            logger.warning(f"ADSB API returned invalid data structure: {type(data.get('ac'))}")
            return []
        
        aircraft_list = []
        current_time = time.time()
        skipped_count = 0
        
        for ac in data.get("ac", []):
            # Check for missing required fields
            if not ac.get("lat") or not ac.get("lon") or not ac.get("hex"):
                skipped_count += 1
                logger.debug(f"Skipping aircraft entry: missing lat/lon/hex - {ac.get('hex', 'NO_HEX')}")
                continue
            
            hex_code = ac.get("hex", "").upper()
            
            aircraft = {
                "hex": hex_code,
                "callsign": ac.get("flight", "").strip() if ac.get("flight") else hex_code,
                "registration": ac.get("r", ""),
                "aircraft_type": ac.get("t", ""),
                "lat": float(ac.get("lat", 0)),
                "lon": float(ac.get("lon", 0)),
                "altitude_ft": ac.get("alt_baro") if ac.get("alt_baro") != "ground" else 0,
                "altitude_baro": ac.get("alt_baro"),
                "altitude_geom": ac.get("alt_geom"),
                "speed_kts": ac.get("gs", 0) if ac.get("gs") else 0,
                "track": ac.get("track", 0) if ac.get("track") else 0,
                "vertical_rate": ac.get("baro_rate", 0) if ac.get("baro_rate") else 0,
                "squawk": ac.get("squawk", ""),
                "category": ac.get("category", ""),
                "timestamp": current_time,
                "last_seen": current_time,
                "raw_data": ac  # Store full raw data for compatibility
            }
            
            aircraft_list.append(aircraft)
        
        logger.info(f"ADSB fetch result: {len(aircraft_list)} valid aircraft processed, {skipped_count} skipped (missing lat/lon/hex), API reported {total_in_response} total")
        return aircraft_list
        
    except requests.exceptions.Timeout:
        logger.warning("ADSB API request timed out")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"ADSB API request error: {e}")
        return []
    except Exception as e:
        logger.error(f"Error fetching ADSB data: {e}")
        return []

def save_adsb_aircraft_to_db(aircraft):
    """Save ADSB aircraft data to database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO adsb_aircraft (
                    hex, callsign, registration, aircraft_type,
                    lat, lon, altitude_ft, altitude_baro, altitude_geom,
                    speed_kts, track, vertical_rate, squawk, category,
                    timestamp, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                aircraft.get("hex"),
                aircraft.get("callsign"),
                aircraft.get("registration"),
                aircraft.get("aircraft_type"),
                aircraft.get("lat"),
                aircraft.get("lon"),
                aircraft.get("altitude_ft"),
                str(aircraft.get("altitude_baro")) if aircraft.get("altitude_baro") else None,
                aircraft.get("altitude_geom"),
                aircraft.get("speed_kts"),
                aircraft.get("track"),
                aircraft.get("vertical_rate"),
                aircraft.get("squawk"),
                aircraft.get("category"),
                aircraft.get("timestamp"),
                aircraft.get("last_seen")
            ))
            
            conn.commit()
        except Exception as e:
            logger.debug(f"Error saving ADSB aircraft to database: {e}")
        finally:
            conn.close()

def update_adsb_data():
    """Update ADSB aircraft data and emit to clients"""
    global ADSB_AIRCRAFT, ADSB_DETECTION_ENABLED, ADSB_CENTER_LAT, ADSB_CENTER_LON, ADSB_RADIUS_KM
    
    try:
        if not ADSB_DETECTION_ENABLED:
            return
        
        # Determine center point for ADSB search
        search_lat = ADSB_CENTER_LAT
        search_lon = ADSB_CENTER_LON
        
        # If no center set, try to use average of active drone detections
        if search_lat is None or search_lon is None:
            active_detections = [d for d in tracked_pairs.values() 
                               if d.get('status') == 'active' and d.get('drone_lat') and d.get('drone_lon')]
            
            if active_detections:
                avg_lat = sum(d.get('drone_lat', 0) for d in active_detections) / len(active_detections)
                avg_lon = sum(d.get('drone_lon', 0) for d in active_detections) / len(active_detections)
                search_lat = avg_lat
                search_lon = avg_lon
            else:
                # Default to Scotland center (Edinburgh area)
                search_lat = 56.5
                search_lon = -4.0
        
        logger.info(f"ADSB search: center=({search_lat:.4f}, {search_lon:.4f}), radius={ADSB_RADIUS_KM}km")
        aircraft_list = fetch_adsb_data(search_lat, search_lon, ADSB_RADIUS_KM)
        
        # Update global aircraft data
        new_aircraft = {}
        current_time = time.time()
        
        for aircraft in aircraft_list:
            hex_code = aircraft.get("hex")
            if hex_code:
                new_aircraft[hex_code] = aircraft
        
        # Mark old aircraft as stale (not seen in last 2 minutes)
        with ADSB_AIRCRAFT_LOCK:
            for hex_code, aircraft in ADSB_AIRCRAFT.items():
                if hex_code not in new_aircraft:
                    # Check if it's been more than 2 minutes since last seen
                    if current_time - aircraft.get("last_seen", 0) < 120:
                        # Keep it for now (might come back)
                        new_aircraft[hex_code] = aircraft
            
            ADSB_AIRCRAFT = new_aircraft
        
        # Save to database
        for aircraft in aircraft_list:
            try:
                save_adsb_aircraft_to_db(aircraft)
            except Exception as e:
                logger.debug(f"Error saving ADSB aircraft to database: {e}")
        
        # Emit to connected clients
        try:
            with ADSB_AIRCRAFT_LOCK:
                aircraft_list_emit = list(ADSB_AIRCRAFT.values())
            socketio.emit('adsb_aircraft', {'aircraft': aircraft_list_emit})
            logger.debug(f"Emitted {len(aircraft_list_emit)} ADSB aircraft to clients")
        except Exception as e:
            logger.debug(f"Error emitting ADSB aircraft: {e}")
            
    except Exception as e:
        logger.error(f"Error updating ADSB data: {e}")

def start_adsb_updater():
    """Start periodic ADSB data updates (with exponential backoff on 429)"""
    def adsb_updater():
        # Wait 5 seconds after startup before first update
        time.sleep(5)
        
        while not SHUTDOWN_EVENT.is_set():
            try:
                if ADSB_DETECTION_ENABLED:
                    try:
                        update_adsb_data()
                    except Exception as e:
                        logger.error(f"Error in ADSB updater: {e}")
            except Exception as e:
                logger.error(f"Error in ADSB updater loop: {e}")
            
            # Wait using backoff interval (increases on 429, resets on success)
            SHUTDOWN_EVENT.wait(_adsb_backoff_interval)
    
    updater_thread = threading.Thread(target=adsb_updater, daemon=True)
    updater_thread.start()
    logger.info(f"ADSB updater started (updates every {ADSB_UPDATE_INTERVAL} seconds)")

# ----------------------
# Weather Data Functions (Windy API)
# ----------------------
def fetch_weather_data(lat, lon, model="gfs", parameters=None, levels=None):
    """Fetch weather forecast data from Windy API for specific coordinates
    
    Args:
        lat: Latitude (float)
        lon: Longitude (float)
        model: Forecast model (default: "gfs")
        parameters: List of parameters to fetch (default: common ones)
        levels: List of altitude levels (default: ["surface"])
    
    Returns:
        Dictionary with weather data or None on error
    """
    global WEATHER_API_KEY
    
    if not WEATHER_API_KEY:
        # Try to get from environment or config file
        WEATHER_API_KEY = os.environ.get('WINDY_API_KEY', '')
        if not WEATHER_API_KEY and os.path.exists(WEATHER_CONFIG_FILE):
            try:
                with open(WEATHER_CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    WEATHER_API_KEY = config.get("windy_api_key", "")
            except Exception as e:
                logger.debug(f"Error reading weather config: {e}")
    
    if not WEATHER_API_KEY:
        logger.debug("Windy API key not configured")
        return None
    
    if parameters is None:
        parameters = ["wind", "temp", "dewpoint", "rh", "pressure", "precip", "windGust"]
    
    if levels is None:
        levels = ["surface"]
    
    try:
        url = "https://api.windy.com/api/point-forecast/v2"
        
        payload = {
            "lat": round(float(lat), 2),
            "lon": round(float(lon), 2),
            "model": model,
            "parameters": parameters,
            "levels": levels,
            "key": WEATHER_API_KEY
        }
        
        session = create_retry_session()
        response = session.post(url, json=payload, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            return data
        elif response.status_code == 204:
            logger.debug(f"No weather data available for model {model} at {lat},{lon}")
            return None
        elif response.status_code == 400:
            logger.warning(f"Invalid weather API request: {response.text}")
            return None
        else:
            logger.warning(f"Weather API error {response.status_code}: {response.text}")
            return None
            
    except requests.exceptions.Timeout:
        logger.warning(f"Weather API request timed out for {lat},{lon}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Weather API request error: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching weather data: {e}")
        return None

def update_weather_data():
    """Update weather data for configured locations and active drone detections"""
    global WEATHER_DATA, WEATHER_ENABLED, WEATHER_LOCATIONS, tracked_pairs
    
    if not WEATHER_ENABLED:
        return
    
    try:
        # Get manually configured locations
        locations = WEATHER_LOCATIONS.copy()
        
        if not locations:
            # Try to load from config file
            if os.path.exists(WEATHER_CONFIG_FILE):
                try:
                    with open(WEATHER_CONFIG_FILE, "r") as f:
                        config = json.load(f)
                        locations = config.get("locations", [])
                except Exception as e:
                    logger.error(f"Error reading weather config for locations: {e}")
        
        # Also fetch weather for active drone detections
        current_time = time.time()
        active_drone_locations = set()  # Use set to deduplicate nearby locations
        
        for mac, detection in tracked_pairs.items():
            # Only include active detections with valid GPS (within last 5 minutes)
            last_update = detection.get("last_update", 0)
            if current_time - last_update > 300:  # 5 minutes
                continue
            
            drone_lat = detection.get("drone_lat", 0)
            drone_lon = detection.get("drone_long", 0)
            
            if drone_lat != 0 and drone_lon != 0:
                # Round to 2 decimals (~1km precision) to deduplicate nearby detections
                rounded_lat = round(drone_lat, 2)
                rounded_lon = round(drone_lon, 2)
                location_key = (rounded_lat, rounded_lon)
                
                if location_key not in active_drone_locations:
                    active_drone_locations.add(location_key)
                    alias = ALIASES.get(mac, "")
                    name = f"Drone: {alias}" if alias else f"Drone: {mac[:8]}"
                    locations.append({
                        "lat": rounded_lat,
                        "lon": rounded_lon,
                        "name": name,
                        "source": "drone_detection"
                    })
        
        if not locations:
            logger.debug("No weather locations (configured or active drones), skipping update")
            return
        
        new_weather_data = {}
        
        for location in locations:
            lat = location.get("lat")
            lon = location.get("lon")
            name = location.get("name", f"{lat},{lon}")
            
            if lat is None or lon is None:
                continue
            
            location_key = f"{lat}_{lon}"
            weather = fetch_weather_data(lat, lon)
            
            if weather:
                # Add metadata
                weather["location"] = {
                    "lat": lat,
                    "lon": lon,
                    "name": name,
                    "source": location.get("source", "manual")
                }
                weather["last_update"] = time.time()
                new_weather_data[location_key] = weather
                
                # Save to database
                try:
                    save_weather_to_db(
                        location_key,
                        name,
                        lat,
                        lon,
                        location.get("source", "manual"),
                        weather
                    )
                except Exception as e:
                    logger.debug(f"Error saving weather to database: {e}")
        
        WEATHER_DATA = new_weather_data
        
        # Emit to connected clients
        try:
            socketio.emit('weather_data', {'weather': WEATHER_DATA})
            logger.debug(f"Emitted weather data for {len(WEATHER_DATA)} locations to clients")
        except Exception as e:
            logger.debug(f"Error emitting weather data: {e}")
            
    except Exception as e:
        logger.error(f"Error updating weather data: {e}")

def start_weather_updater():
    """Start periodic weather data updates"""
    def weather_updater():
        # Wait 10 seconds after startup before first update
        time.sleep(10)
        
        while not SHUTDOWN_EVENT.is_set():
            try:
                if WEATHER_ENABLED:
                    try:
                        update_weather_data()
                    except Exception as e:
                        logger.error(f"Error in weather updater: {e}")
            except Exception as e:
                logger.error(f"Error in weather updater loop: {e}")
            
            # Wait for update interval
            SHUTDOWN_EVENT.wait(WEATHER_UPDATE_INTERVAL)
    
    updater_thread = threading.Thread(target=weather_updater, daemon=True)
    updater_thread.start()
    logger.info(f"Weather updater started (updates every {WEATHER_UPDATE_INTERVAL} seconds)")

# ----------------------
# Webcams Data Functions (Windy API)
# ----------------------
def fetch_webcams(bounds=None, limit=50):
    """Fetch webcams from Windy API for a given bounding box
    
    Args:
        bounds: Dict with 'north', 'south', 'east', 'west' keys, or None for default UK bounds
        limit: Maximum number of webcams to return
    
    Returns:
        List of webcam dictionaries or None on error
    """
    global WEBCAMS_API_KEY
    
    if not WEBCAMS_API_KEY:
        # Try to get from environment or config file
        WEBCAMS_API_KEY = os.environ.get('WINDY_WEBCAMS_API_KEY', '')
        if not WEBCAMS_API_KEY and os.path.exists(WEBCAMS_CONFIG_FILE):
            try:
                with open(WEBCAMS_CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    WEBCAMS_API_KEY = config.get("windy_webcams_api_key", "")
            except Exception as e:
                logger.debug(f"Error reading webcams config: {e}")
    
    if not WEBCAMS_API_KEY:
        logger.debug("Windy Webcams API key not configured")
        return None
    
    # Default to UK bounds if not provided
    if bounds is None:
        bounds = {
            'north': 61.0,
            'south': 49.0,
            'east': 2.0,
            'west': -8.0
        }
    
    try:
        url = "https://api.windy.com/api/webcams/v2/list"
        
        params = {
            "show": "webcams:url,player",
            "key": WEBCAMS_API_KEY,
            "limit": limit
        }
        
        # Add bounding box if provided
        if bounds:
            params['north'] = bounds['north']
            params['south'] = bounds['south']
            params['east'] = bounds['east']
            params['west'] = bounds['west']
        
        session = create_retry_session()
        response = session.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if 'result' in data and 'webcams' in data['result']:
                return data['result']['webcams']
            return []
        elif response.status_code == 400:
            logger.warning(f"Invalid webcams API request: {response.text}")
            return None
        else:
            logger.warning(f"Webcams API error {response.status_code}: {response.text}")
            return None
            
    except requests.exceptions.Timeout:
        logger.warning(f"Webcams API request timed out")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Webcams API request error: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching webcams: {e}")
        return None

def update_webcams_data():
    """Update webcam data for current map view or default bounds and emit to clients"""
    global WEBCAMS_DATA, WEBCAMS_ENABLED
    
    if not WEBCAMS_ENABLED:
        return
    
    try:
        # For now, use default UK bounds
        # In the future, could get bounds from map view
        bounds = {
            'north': 61.0,
            'south': 49.0,
            'east': 2.0,
            'west': -8.0
        }
        
        webcams = fetch_webcams(bounds=bounds, limit=100)
        
        if webcams is None:
            logger.warning("Failed to fetch webcams (API error or no API key)")
            return
        
        if not webcams:
            logger.debug("No webcams found in the specified area")
            WEBCAMS_DATA = {}
            # Emit empty data to clear existing markers
            try:
                socketio.emit('webcams_data', {'webcams': {}})
            except Exception as e:
                logger.debug(f"Error emitting empty webcams data: {e}")
            return
        
        new_webcams_data = {}
        
        for webcam in webcams:
            webcam_id = webcam.get('id')
            if not webcam_id:
                continue
            
            location = webcam.get('location', {})
            lat = location.get('latitude')
            lon = location.get('longitude')
            
            if not lat or not lon:
                continue
            
            # Store webcam data
            new_webcams_data[webcam_id] = {
                'id': webcam_id,
                'title': webcam.get('title', 'Webcam'),
                'lat': lat,
                'lon': lon,
                'status': webcam.get('status', 'active'),
                'image': webcam.get('image', {}),
                'player': webcam.get('player', {}),
                'last_update': time.time()
            }
        
        WEBCAMS_DATA = new_webcams_data
        
        # Save to database
        for webcam_id, webcam in new_webcams_data.items():
            try:
                save_webcam_to_db(webcam)
            except Exception as e:
                logger.debug(f"Error saving webcam to database: {e}")
        
        # Emit to connected clients
        try:
            socketio.emit('webcams_data', {'webcams': WEBCAMS_DATA})
            logger.info(f"Emitted {len(WEBCAMS_DATA)} webcams to clients")
        except Exception as e:
            logger.debug(f"Error emitting webcams: {e}")
            
    except Exception as e:
        logger.error(f"Error updating webcams data: {e}")

def start_webcams_updater():
    """Start periodic webcam data updates"""
    def webcams_updater():
        # Wait 30 seconds after startup before first update
        time.sleep(30)
        
        while not SHUTDOWN_EVENT.is_set():
            try:
                if WEBCAMS_ENABLED:
                    try:
                        update_webcams_data()
                    except Exception as e:
                        logger.error(f"Error in webcams updater: {e}")
            except Exception as e:
                logger.error(f"Error in webcams updater loop: {e}")
            
            # Wait for update interval
            SHUTDOWN_EVENT.wait(WEBCAMS_UPDATE_INTERVAL)
    
    updater_thread = threading.Thread(target=webcams_updater, daemon=True)
    updater_thread.start()
    logger.info(f"Webcams updater started (updates every {WEBCAMS_UPDATE_INTERVAL} seconds)")

# ----------------------
# Lightning Detection Functions
# ----------------------
def start_lightning_detection():
    """Start real-time lightning detection from LightningMaps.org WebSocket feed"""
    global LIGHTNING_WS_CONNECTION
    
    def lightning_websocket_thread():
        global LIGHTNING_WS_CONNECTION
        # LightningMaps.org redirects ws.lightningmaps.org to www.lightningmaps.org
        # But the WebSocket endpoint may be at a different path
        # Try multiple possible endpoints
        ws_urls = [
            "wss://ws.lightningmaps.org/v2/ws",
            "wss://www.lightningmaps.org/v2/ws", 
            "wss://ws.lightningmaps.org/",
        ]
        ws_url = ws_urls[0]  # Start with the documented endpoint
        reconnect_delay = 5  # seconds
        max_reconnect_delay = 60  # max delay between reconnects
        
        while not SHUTDOWN_EVENT.is_set():
            # Check if lightning detection is enabled
            if not LIGHTNING_DETECTION_ENABLED:
                time.sleep(5)  # Check every 5 seconds
                continue
            try:
                logger.info(f"Connecting to LightningMaps.org WebSocket: {ws_url}")
                
                def on_message(ws, message):
                    try:
                        data = json.loads(message)
                        
                        # Process lightning strike event
                        if isinstance(data, dict) and 'lat' in data and 'lon' in data:
                            process_lightning_strike(data)
                    except json.JSONDecodeError as e:
                        logger.debug(f"Failed to parse lightning message: {e}")
                    except Exception as e:
                        logger.error(f"Error processing lightning strike: {e}")
                
                def on_error(ws, error):
                    logger.warning(f"Lightning WebSocket error: {error}")
                
                def on_close(ws, close_status_code, close_msg):
                    if not SHUTDOWN_EVENT.is_set():
                        logger.warning("Lightning WebSocket closed, will reconnect...")
                
                def on_open(ws):
                    logger.info("Connected to LightningMaps.org WebSocket feed")
                
                # Create WebSocket connection (disable SSL verification for LightningMaps.org)
                ws = websocket.WebSocketApp(
                    ws_url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_open=on_open
                )
                LIGHTNING_WS_CONNECTION = ws
                
                # Run forever with auto-reconnect (disable SSL verification for LightningMaps.org)
                # Note: sslopt parameter should be passed to run_forever, not in WebSocketApp constructor
                ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False})
                LIGHTNING_WS_CONNECTION = None
                
            except Exception as e:
                logger.error(f"Lightning WebSocket connection error: {e}")
            
            # Reconnect with exponential backoff
            if not SHUTDOWN_EVENT.is_set():
                delay = min(reconnect_delay, max_reconnect_delay)
                logger.info(f"Reconnecting to LightningMaps.org in {delay} seconds...")
                time.sleep(delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
    
    lightning_thread = threading.Thread(target=lightning_websocket_thread, daemon=True)
    lightning_thread.start()
    logger.info("Lightning detection started (LightningMaps.org WebSocket)")

def process_lightning_strike(strike_data):
    """Process a lightning strike event and add it to tracked detections"""
    try:
        # Extract strike information
        lat = strike_data.get('lat', 0)
        lon = strike_data.get('lon', 0)
        alt = strike_data.get('alt', 0)  # altitude in meters
        strike_time = strike_data.get('time', time.time())
        strike_type = strike_data.get('type', 0)
        stroke = strike_data.get('stroke', {})
        current = stroke.get('current', 0) if stroke else 0
        polarity = stroke.get('polarity', 0) if stroke else 0
        
        if lat == 0 or lon == 0:
            return  # Invalid coordinates
        
        # Create unique identifier for this strike
        # Use timestamp + coordinates to create a unique MAC-like identifier
        strike_id = f"lightning_{int(strike_time)}_{int(lat*1000)}_{int(lon*1000)}"
        
        # Convert altitude from meters to feet for consistency
        altitude_ft = alt * 3.28084 if alt > 0 else 0
        
        # Create detection object in the same format as drone detections
        detection = {
            "mac": strike_id,
            "drone_lat": lat,
            "drone_long": lon,
            "drone_altitude": altitude_ft,
            "pilot_lat": 0,
            "pilot_long": 0,
            "rssi": 0,
            "basic_id": "",
            "last_update": strike_time,
            "status": "active",
            "detection_type": "lightning",
            "lightning_data": {
                "type": strike_type,
                "current": current,
                "polarity": polarity,
                "altitude_m": alt,
                "timestamp": strike_time
            }
        }
        
        # Update detection (this will handle logging, CSV, KML, etc.)
        update_detection(detection)
        
        logger.info(f"Lightning strike detected: {lat:.4f}, {lon:.4f}, {alt:.0f}m, {current:.1f}kA")
        
        # Emit lightning alert event for audible warning
        try:
            socketio.emit('lightning_alert', {
                'lat': lat,
                'lon': lon,
                'alt': alt,
                'current': current,
                'timestamp': strike_time
            })
        except Exception as e:
            logger.debug(f"Error emitting lightning alert: {e}")
        
        # System beep for audible warning (works on Linux/Unix)
        try:
            import sys
            if sys.platform != 'win32':
                # Use system beep (works on Linux/Raspberry Pi)
                os.system('echo -e "\\a" > /dev/tty 2>/dev/null || printf "\\a"')
            else:
                # Windows beep
                import winsound
                winsound.Beep(1000, 200)  # 1000Hz for 200ms
        except Exception:
            pass  # Silently fail if beep not available
        
    except Exception as e:
        logger.error(f"Error processing lightning strike: {e}")

# ----------------------
# Signal Handlers for Graceful Shutdown
# ----------------------
def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    SHUTDOWN_EVENT.set()
    
    # Close all serial connections
    with serial_objs_lock:
        for port, ser in serial_objs.items():
            try:
                if ser and ser.is_open:
                    logger.info(f"Closing serial connection to {port}")
                    ser.close()
            except Exception as e:
                logger.error(f"Error closing serial port {port}: {e}")
    
    logger.info("Shutdown complete")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Helper: consistent color per MAC via hashing
def get_color_for_mac(mac: str) -> str:
    # Compute hue from MAC string hash
    hue = sum(ord(c) for c in mac) % 360
    r, g, b = colorsys.hsv_to_rgb(hue/360.0, 1.0, 1.0)
    ri, gi, bi = int(r*255), int(g*255), int(b*255)
    # Return ABGR format
    return f"ff{bi:02x}{gi:02x}{ri:02x}"


# Server-side webhook URL (set via API)
WEBHOOK_URL = None

def set_server_webhook_url(url: str):
    global WEBHOOK_URL
    WEBHOOK_URL = url
    save_webhook_url()  # Save to disk whenever URL is updated

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())
socketio = SocketIO(app, cors_allowed_origins="*")  # Enable Socket.IO

# ----------------------
# Basic HTTP Authentication
# ----------------------
AUTH_PASSWORD = os.environ.get('MESH_MAPPER_PASSWORD', 'dronedrone')
AUTH_USERNAME = os.environ.get('MESH_MAPPER_USERNAME', 'admin')

def check_auth(username, password):
    """Check if a username/password combination is valid."""
    return username == AUTH_USERNAME and password == AUTH_PASSWORD

def authenticate():
    """Send a 401 response that enables basic auth."""
    return Response(
        'Authentication required. Please log in.', 401,
        {'WWW-Authenticate': 'Basic realm="Mesh Mapper"'})

def requires_auth(f):
    """Decorator that requires HTTP Basic Auth on a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# Exempt paths that don't need auth
AUTH_EXEMPT_PATHS = {'/health', '/api/health'}

@app.before_request
def before_request_auth():
    """Apply authentication to all routes except health endpoints and SocketIO."""
    if request.path in AUTH_EXEMPT_PATHS:
        return None
    # Skip auth for socketio polling/websocket (handled by SocketIO namespace)
    if request.path.startswith('/socket.io'):
        return None
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return authenticate()

@app.route('/health', methods=['GET'])
def health_check():
    """Unauthenticated health check endpoint."""
    return jsonify({"status": "ok", "uptime": time.time()}), 200

@app.route('/api/health', methods=['GET'])
def api_health_check():
    """Unauthenticated API health check endpoint."""
    return jsonify({"status": "ok", "uptime": time.time()}), 200

# Define emit_serial_status early to avoid NameError in threads
def emit_serial_status():
    try:
        socketio.emit('serial_status', serial_connected_status, )
    except Exception as e:
        logger.debug(f"Error emitting serial status: {e}")
        pass  # Ignore if no clients connected or serialization error

def emit_aliases():
    try:
        socketio.emit('aliases', ALIASES, )
    except Exception as e:
        logger.debug(f"Error emitting aliases: {e}")

def emit_detections():
    try:
        # Convert tracked_pairs to a JSON-serializable format
        serializable_pairs = {}
        for key, value in tracked_pairs.items():
            # Ensure key is a string
            str_key = str(key)
            # Ensure value is JSON-serializable
            if isinstance(value, dict):
                serializable_pairs[str_key] = value
            else:
                serializable_pairs[str_key] = str(value)
        socketio.emit('detections', serializable_pairs, )
    except Exception as e:
        logger.debug(f"Error emitting detections: {e}")

def emit_paths():
    try:
        socketio.emit('paths', get_paths_for_emit(), )
    except Exception as e:
        logger.debug(f"Error emitting paths: {e}")

def emit_cumulative_log():
    try:
        socketio.emit('cumulative_log', get_cumulative_log_for_emit(), )
    except Exception as e:
        logger.debug(f"Error emitting cumulative log: {e}")

def emit_faa_cache():
    try:
        # Convert FAA_CACHE to JSON-serializable format
        serializable_cache = {}
        for key, value in FAA_CACHE.items():
            # Convert tuple keys to strings
            str_key = str(key) if isinstance(key, tuple) else key
            serializable_cache[str_key] = value
        socketio.emit('faa_cache', serializable_cache, )
    except Exception as e:
        logger.debug(f"Error emitting FAA cache: {e}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------------
# Database Configuration
# ----------------------
DB_FILE = os.path.join(BASE_DIR, "mesh_mapper.db")
DB_LOCK = threading.Lock()

def init_database():
    """Initialize SQLite database with schema"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
    cursor = conn.cursor()
    
    try:
        # Read and execute schema
        schema_file = os.path.join(BASE_DIR, "database_schema.sql")
        if os.path.exists(schema_file):
            with open(schema_file, 'r') as f:
                schema = f.read()
                # Use executescript for multi-statement execution
                cursor.executescript(schema)
                logger.info("Database schema loaded from file")
        else:
            # Create schema inline if file doesn't exist
            logger.info("Schema file not found, using inline schema")
            cursor.executescript("""
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
            faa_data TEXT,
            status TEXT DEFAULT 'active',
            last_update REAL,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mac ON detections(mac);
        CREATE INDEX IF NOT EXISTS idx_timestamp ON detections(timestamp);
        CREATE INDEX IF NOT EXISTS idx_status ON detections(status);
        CREATE INDEX IF NOT EXISTS idx_last_update ON detections(last_update);
        
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
            last_seen REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_timestamp_ais ON ais_vessels(timestamp);
        CREATE INDEX IF NOT EXISTS idx_last_seen_ais ON ais_vessels(last_seen);
        
        CREATE TABLE IF NOT EXISTS weather_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_key TEXT NOT NULL UNIQUE,
            location_name TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            source TEXT,
            weather_json TEXT,
            last_update REAL NOT NULL,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_last_update_weather ON weather_data(last_update);
        CREATE INDEX IF NOT EXISTS idx_location_weather ON weather_data(lat, lon);
        
        CREATE TABLE IF NOT EXISTS webcams (
            webcam_id TEXT PRIMARY KEY,
            title TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            status TEXT,
            image_url TEXT,
            player_url TEXT,
            webcam_json TEXT,
            last_update REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_last_update_webcams ON webcams(last_update);
        CREATE INDEX IF NOT EXISTS idx_location_webcams ON webcams(lat, lon);
        
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
            last_seen REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_last_seen_aprs ON aprs_stations(last_seen);
        CREATE INDEX IF NOT EXISTS idx_location_aprs ON aprs_stations(lat, lon);
        
        CREATE TABLE IF NOT EXISTS faa_cache (
            mac TEXT NOT NULL,
            remote_id TEXT NOT NULL,
            faa_response TEXT,
            cached_at REAL DEFAULT (strftime('%s', 'now')),
            PRIMARY KEY (mac, remote_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mac_faa ON faa_cache(mac);
        CREATE INDEX IF NOT EXISTS idx_remote_id_faa ON faa_cache(remote_id);
        
        CREATE TABLE IF NOT EXISTS aliases (
            mac TEXT PRIMARY KEY,
            alias TEXT NOT NULL,
            updated_at REAL DEFAULT (strftime('%s', 'now'))
        );
        
        CREATE TABLE IF NOT EXISTS zones (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            zone_type TEXT,
            coordinates TEXT,
            lower_altitude_ft REAL,
            upper_altitude_ft REAL,
            enabled INTEGER DEFAULT 1,
            source TEXT,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_enabled_zones ON zones(enabled);
        
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_type TEXT NOT NULL,
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
            faa_data TEXT,
            details TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_timestamp_incidents ON incidents(timestamp);
        CREATE INDEX IF NOT EXISTS idx_type_incidents ON incidents(incident_type);
        CREATE INDEX IF NOT EXISTS idx_mac_incidents ON incidents(mac);
        """)
        
        conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        conn.rollback()
    finally:
        conn.close()

def get_db_connection():
    """Get a database connection (thread-safe)"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ----------------------
# Database Helper Functions
# ----------------------
def save_detection_to_db(detection: Dict[str, Any]):
    """Save or update a detection in the database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO detections 
                (mac, alias, timestamp, rssi, drone_lat, drone_lon, drone_altitude, 
                 pilot_lat, pilot_lon, basic_id, faa_data, status, last_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                detection.get('mac'),
                detection.get('alias'),
                detection.get('timestamp', time.time()),
                detection.get('rssi'),
                detection.get('drone_lat'),
                detection.get('drone_lon'),
                detection.get('drone_altitude'),
                detection.get('pilot_lat'),
                detection.get('pilot_lon'),
                detection.get('basic_id'),
                json.dumps(detection.get('faa_data', {})) if detection.get('faa_data') else None,
                detection.get('status', 'active'),
                detection.get('last_update', time.time())
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving detection to database: {e}")
        finally:
            conn.close()

def get_recent_detections_from_db(minutes=5):
    """Get recent detections from database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = time.time() - (minutes * 60)
            cursor.execute("""
                SELECT * FROM detections 
                WHERE last_update > ? 
                ORDER BY last_update DESC
            """, (cutoff,))
            rows = cursor.fetchall()
            detections = []
            for row in rows:
                det = dict(row)
                if det.get('faa_data'):
                    try:
                        det['faa_data'] = json.loads(det['faa_data'])
                    except:
                        det['faa_data'] = {}
                detections.append(det)
            return detections
        except Exception as e:
            logger.error(f"Error getting recent detections: {e}")
            return []
        finally:
            conn.close()

def save_ais_vessel_to_db(vessel: Dict[str, Any]):
    """Save or update an AIS vessel in the database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO ais_vessels 
                (mmsi, name, vessel_type, lat, lon, course, speed, heading, 
                 length, width, timestamp, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                vessel.get('mmsi'),
                vessel.get('name', f"Vessel {vessel.get('mmsi')}"),
                vessel.get('vessel_type', 'Unknown'),
                vessel.get('lat'),
                vessel.get('lon'),
                vessel.get('course'),
                vessel.get('speed'),
                vessel.get('heading'),
                vessel.get('length'),
                vessel.get('width'),
                vessel.get('timestamp', time.time()),
                time.time()
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving AIS vessel to database: {e}")
        finally:
            conn.close()

def get_recent_ais_vessels_from_db(minutes=10):
    """Get recent AIS vessels from database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = time.time() - (minutes * 60)
            cursor.execute("""
                SELECT * FROM ais_vessels 
                WHERE last_seen > ? 
                ORDER BY last_seen DESC
            """, (cutoff,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting recent AIS vessels: {e}")
            return []
        finally:
            conn.close()

def save_weather_to_db(location_key: str, location_name: str, lat: float, lon: float, 
                       source: str, weather_data: Dict[str, Any]):
    """Save or update weather data in the database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO weather_data 
                (location_key, location_name, lat, lon, source, weather_json, last_update)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                location_key,
                location_name,
                lat,
                lon,
                source,
                json.dumps(weather_data),
                time.time()
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving weather to database: {e}")
        finally:
            conn.close()

def get_recent_weather_from_db(minutes=10):
    """Get recent weather data from database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = time.time() - (minutes * 60)
            cursor.execute("""
                SELECT * FROM weather_data 
                WHERE last_update > ? 
                ORDER BY last_update DESC
            """, (cutoff,))
            rows = cursor.fetchall()
            weather_list = []
            for row in rows:
                w = dict(row)
                if w.get('weather_json'):
                    try:
                        w['weather'] = json.loads(w['weather_json'])
                        w['weather']['location'] = {
                            'lat': w['lat'],
                            'lon': w['lon'],
                            'name': w['location_name'],
                            'source': w['source']
                        }
                    except:
                        pass
                weather_list.append(w)
            return weather_list
        except Exception as e:
            logger.error(f"Error getting recent weather: {e}")
            return []
        finally:
            conn.close()

def save_webcam_to_db(webcam: Dict[str, Any]):
    """Save or update a webcam in the database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO webcams 
                (webcam_id, title, lat, lon, status, image_url, player_url, webcam_json, last_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                webcam.get('id'),
                webcam.get('title', 'Webcam'),
                webcam.get('lat'),
                webcam.get('lon'),
                webcam.get('status', 'active'),
                webcam.get('image', {}).get('current', {}).get('preview') if webcam.get('image') else None,
                webcam.get('player', {}).get('live', {}).get('embed') if webcam.get('player') else None,
                json.dumps(webcam),
                time.time()
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving webcam to database: {e}")
        finally:
            conn.close()

def get_recent_webcams_from_db(minutes=60):
    """Get recent webcams from database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = time.time() - (minutes * 60)
            cursor.execute("""
                SELECT * FROM webcams 
                WHERE status = 'active' AND last_update > ? 
                ORDER BY last_update DESC
            """, (cutoff,))
            rows = cursor.fetchall()
            webcams = []
            for row in rows:
                w = dict(row)
                if w.get('webcam_json'):
                    try:
                        w.update(json.loads(w['webcam_json']))
                    except:
                        pass
                webcams.append(w)
            return webcams
        except Exception as e:
            logger.error(f"Error getting recent webcams: {e}")
            return []
        finally:
            conn.close()

def save_aprs_station_to_db(station: Dict[str, Any]):
    """Save or update an APRS station in the database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO aprs_stations 
                (callsign, name, type, lat, lon, altitude, course, speed, 
                 symbol, comment, status, timestamp, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                station.get('callsign'),
                station.get('name', station.get('callsign')),
                station.get('type', 'l'),
                station.get('lat'),
                station.get('lng'),
                station.get('altitude'),
                station.get('course'),
                station.get('speed'),
                station.get('symbol'),
                station.get('comment'),
                station.get('status'),
                station.get('time', 0),
                time.time()
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving APRS station to database: {e}")
        finally:
            conn.close()

def get_recent_aprs_stations_from_db(minutes=10):
    """Get recent APRS stations from database"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = time.time() - (minutes * 60)
            cursor.execute("""
                SELECT * FROM aprs_stations 
                WHERE last_seen > ? 
                ORDER BY last_seen DESC
            """, (cutoff,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting recent APRS stations: {e}")
            return []
        finally:
            conn.close()

# ----------------------
# AIS Configuration
# ----------------------
AIS_CONFIG_FILE = os.path.join(BASE_DIR, "ais_config.json")  # AIS configuration file

# ----------------------
# Lightning Detection Settings Persistence
# ----------------------
LIGHTNING_SETTINGS_FILE = os.path.join(BASE_DIR, "lightning_settings.json")
AIS_SETTINGS_FILE = os.path.join(BASE_DIR, "ais_settings.json")
APRS_CONFIG_FILE = os.path.join(BASE_DIR, "aprs_config.json")  # APRS configuration file
APRS_SETTINGS_FILE = os.path.join(BASE_DIR, "aprs_settings.json")
WEATHER_CONFIG_FILE = os.path.join(BASE_DIR, "weather_config.json")  # Weather configuration file
WEATHER_SETTINGS_FILE = os.path.join(BASE_DIR, "weather_settings.json")
WEBCAMS_CONFIG_FILE = os.path.join(BASE_DIR, "webcams_config.json")  # Webcams configuration file
WEBCAMS_SETTINGS_FILE = os.path.join(BASE_DIR, "webcams_settings.json")
METOFFICE_SETTINGS_FILE = os.path.join(BASE_DIR, "metoffice_settings.json")  # Met Office alert settings

def load_lightning_settings():
    """Load lightning detection settings from disk"""
    global LIGHTNING_DETECTION_ENABLED
    if os.path.exists(LIGHTNING_SETTINGS_FILE):
        try:
            with open(LIGHTNING_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                LIGHTNING_DETECTION_ENABLED = data.get("enabled", True)
                logger.info(f"Loaded lightning detection setting: {'enabled' if LIGHTNING_DETECTION_ENABLED else 'disabled'}")
        except Exception as e:
            logger.error(f"Error loading lightning settings: {e}")
            LIGHTNING_DETECTION_ENABLED = True

def save_lightning_settings():
    """Save lightning detection settings to disk"""
    global LIGHTNING_DETECTION_ENABLED
    try:
        with open(LIGHTNING_SETTINGS_FILE, "w") as f:
            json.dump({"enabled": LIGHTNING_DETECTION_ENABLED}, f)
        logger.debug(f"Lightning settings saved to {LIGHTNING_SETTINGS_FILE}")
    except Exception as e:
        logger.error(f"Error saving lightning settings: {e}")

def load_ais_settings():
    """Load AIS detection settings from disk"""
    global AIS_DETECTION_ENABLED
    if os.path.exists(AIS_SETTINGS_FILE):
        try:
            with open(AIS_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                AIS_DETECTION_ENABLED = data.get("enabled", True)
                logger.info(f"Loaded AIS detection setting: {'enabled' if AIS_DETECTION_ENABLED else 'disabled'}")
        except Exception as e:
            logger.error(f"Error loading AIS settings: {e}")
            AIS_DETECTION_ENABLED = True

def save_ais_settings():
    """Save AIS detection settings to disk"""
    global AIS_DETECTION_ENABLED
    try:
        with open(AIS_SETTINGS_FILE, "w") as f:
            json.dump({"enabled": AIS_DETECTION_ENABLED}, f)
        logger.debug(f"AIS settings saved to {AIS_SETTINGS_FILE}")
    except Exception as e:
        logger.error(f"Error saving AIS settings: {e}")

# Met Office Weather Warnings Settings
METOFFICE_ALERT_SETTINGS = {
    "eas_tones_enabled": True,
    "amber_alerts_enabled": False,
    "yellow_alerts_enabled": False,
    "repeat_alerts_enabled": False,
    "eas_volume": 40,  # 0-100
    "update_frequency": 1800  # seconds
}

def load_metoffice_settings():
    """Load Met Office alert settings from disk"""
    global METOFFICE_ALERT_SETTINGS, METOFFICE_UPDATE_INTERVAL
    if os.path.exists(METOFFICE_SETTINGS_FILE):
        try:
            with open(METOFFICE_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                METOFFICE_ALERT_SETTINGS.update(data)
                # Update update interval if changed
                if "update_frequency" in data:
                    METOFFICE_UPDATE_INTERVAL = data["update_frequency"]
                logger.info(f"Loaded Met Office alert settings")
        except Exception as e:
            logger.error(f"Error loading Met Office settings: {e}")

def save_metoffice_settings():
    """Save Met Office alert settings to disk"""
    global METOFFICE_ALERT_SETTINGS, METOFFICE_UPDATE_INTERVAL
    try:
        METOFFICE_ALERT_SETTINGS["update_frequency"] = METOFFICE_UPDATE_INTERVAL
        with open(METOFFICE_SETTINGS_FILE, "w") as f:
            json.dump(METOFFICE_ALERT_SETTINGS, f, indent=2)
        logger.debug(f"Met Office settings saved to {METOFFICE_SETTINGS_FILE}")
    except Exception as e:
        logger.error(f"Error saving Met Office settings: {e}")

def load_ais_config():
    """Load AIS API keys from config file"""
    global AIS_API_KEY
    if os.path.exists(AIS_CONFIG_FILE):
        try:
            with open(AIS_CONFIG_FILE, "r") as f:
                data = json.load(f)
                # Check environment variables first, then config file
                AIS_API_KEY = os.environ.get('AISSTREAM_API_KEY') or os.environ.get('AIS_API_KEY') or data.get('aisstream_api_key', '')
                logger.info("Loaded AIS configuration from file")
        except Exception as e:
            logger.error(f"Error loading AIS config: {e}")
            AIS_API_KEY = os.environ.get('AISSTREAM_API_KEY') or os.environ.get('AIS_API_KEY', '')
    else:
        # Try environment variables
        AIS_API_KEY = os.environ.get('AISSTREAM_API_KEY') or os.environ.get('AIS_API_KEY', '')

def save_ais_config():
    """Save AIS API keys to config file"""
    global AIS_API_KEY
    try:
        # Read existing config if it exists
        config = {}
        if os.path.exists(AIS_CONFIG_FILE):
            try:
                with open(AIS_CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except:
                pass
        
        # Update with current API key (only if not from environment)
        if not os.environ.get('AISSTREAM_API_KEY') and not os.environ.get('AIS_API_KEY'):
            config['aisstream_api_key'] = AIS_API_KEY
        
        with open(AIS_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.debug(f"AIS config saved to {AIS_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Error saving AIS config: {e}")

# ----------------------
# APRS Configuration Functions
# ----------------------
def load_aprs_settings():
    """Load APRS detection settings from disk"""
    global APRS_DETECTION_ENABLED
    if os.path.exists(APRS_SETTINGS_FILE):
        try:
            with open(APRS_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                APRS_DETECTION_ENABLED = data.get("enabled", True)
                logger.info(f"Loaded APRS detection setting: {'enabled' if APRS_DETECTION_ENABLED else 'disabled'}")
        except Exception as e:
            logger.error(f"Error loading APRS settings: {e}")
            APRS_DETECTION_ENABLED = True

def save_aprs_settings():
    """Save APRS detection settings to disk"""
    global APRS_DETECTION_ENABLED
    try:
        with open(APRS_SETTINGS_FILE, "w") as f:
            json.dump({"enabled": APRS_DETECTION_ENABLED}, f)
        logger.debug(f"APRS settings saved to {APRS_SETTINGS_FILE}")
    except Exception as e:
        logger.error(f"Error saving APRS settings: {e}")

def load_aprs_config():
    """Load APRS API keys and callsigns from config file"""
    global APRS_API_KEY
    if os.path.exists(APRS_CONFIG_FILE):
        try:
            with open(APRS_CONFIG_FILE, "r") as f:
                data = json.load(f)
                # Check environment variables first, then config file
                APRS_API_KEY = os.environ.get('APRS_API_KEY') or data.get('aprs_api_key', '')
                logger.info("Loaded APRS configuration from file")
        except Exception as e:
            logger.error(f"Error loading APRS config: {e}")
            APRS_API_KEY = os.environ.get('APRS_API_KEY', '')
    else:
        # Try environment variables
        APRS_API_KEY = os.environ.get('APRS_API_KEY', '')

def save_aprs_config():
    """Save APRS API keys and callsigns to config file"""
    global APRS_API_KEY
    try:
        # Read existing config if it exists
        config = {}
        if os.path.exists(APRS_CONFIG_FILE):
            try:
                with open(APRS_CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except:
                pass
        
        # Update with current API key (only if not from environment)
        if not os.environ.get('APRS_API_KEY'):
            config['aprs_api_key'] = APRS_API_KEY
        
        with open(APRS_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.debug(f"APRS config saved to {APRS_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Error saving APRS config: {e}")

# ----------------------
# Weather Configuration Functions
# ----------------------
def load_weather_settings():
    """Load weather detection settings from disk"""
    global WEATHER_ENABLED
    if os.path.exists(WEATHER_SETTINGS_FILE):
        try:
            with open(WEATHER_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                WEATHER_ENABLED = data.get("enabled", True)
                logger.info(f"Loaded weather setting: {'enabled' if WEATHER_ENABLED else 'disabled'}")
        except Exception as e:
            logger.error(f"Error loading weather settings: {e}")
            WEATHER_ENABLED = True

def save_weather_settings():
    """Save weather detection settings to disk"""
    global WEATHER_ENABLED
    try:
        with open(WEATHER_SETTINGS_FILE, "w") as f:
            json.dump({"enabled": WEATHER_ENABLED}, f)
        logger.debug(f"Weather settings saved to {WEATHER_SETTINGS_FILE}")
    except Exception as e:
        logger.error(f"Error saving weather settings: {e}")

def load_weather_config():
    """Load weather API keys and locations from config file"""
    global WEATHER_API_KEY, WEATHER_LOCATIONS
    if os.path.exists(WEATHER_CONFIG_FILE):
        try:
            with open(WEATHER_CONFIG_FILE, "r") as f:
                data = json.load(f)
                # Check environment variables first, then config file
                WEATHER_API_KEY = os.environ.get('WINDY_API_KEY') or data.get('windy_api_key', '')
                WEATHER_LOCATIONS = data.get('locations', [])
                logger.info("Loaded weather configuration from file")
        except Exception as e:
            logger.error(f"Error loading weather config: {e}")
            WEATHER_API_KEY = os.environ.get('WINDY_API_KEY', '')
            WEATHER_LOCATIONS = []
    else:
        # Try environment variables
        WEATHER_API_KEY = os.environ.get('WINDY_API_KEY', '')
        WEATHER_LOCATIONS = []

def save_weather_config():
    """Save weather API keys and locations to config file"""
    global WEATHER_API_KEY, WEATHER_LOCATIONS
    try:
        # Read existing config if it exists
        config = {}
        if os.path.exists(WEATHER_CONFIG_FILE):
            try:
                with open(WEATHER_CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except:
                pass
        
        # Update with current API key (only if not from environment)
        if not os.environ.get('WINDY_API_KEY'):
            config['windy_api_key'] = WEATHER_API_KEY
        
        # Update locations
        config['locations'] = WEATHER_LOCATIONS
        
        with open(WEATHER_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.debug(f"Weather config saved to {WEATHER_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Error saving weather config: {e}")

# ----------------------
# Webcams Configuration Functions
# ----------------------
def load_webcams_settings():
    """Load webcams detection settings from disk"""
    global WEBCAMS_ENABLED
    if os.path.exists(WEBCAMS_SETTINGS_FILE):
        try:
            with open(WEBCAMS_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                WEBCAMS_ENABLED = data.get("enabled", True)
                logger.info(f"Loaded webcams setting: {'enabled' if WEBCAMS_ENABLED else 'disabled'}")
        except Exception as e:
            logger.error(f"Error loading webcams settings: {e}")
            WEBCAMS_ENABLED = True

def save_webcams_settings():
    """Save webcams detection settings to disk"""
    global WEBCAMS_ENABLED
    try:
        with open(WEBCAMS_SETTINGS_FILE, "w") as f:
            json.dump({"enabled": WEBCAMS_ENABLED}, f)
        logger.debug(f"Webcams settings saved to {WEBCAMS_SETTINGS_FILE}")
    except Exception as e:
        logger.error(f"Error saving webcams settings: {e}")

def load_webcams_config():
    """Load webcams API keys from config file"""
    global WEBCAMS_API_KEY
    if os.path.exists(WEBCAMS_CONFIG_FILE):
        try:
            with open(WEBCAMS_CONFIG_FILE, "r") as f:
                data = json.load(f)
                # Check environment variables first, then config file
                WEBCAMS_API_KEY = os.environ.get('WINDY_WEBCAMS_API_KEY') or data.get('windy_webcams_api_key', '')
                logger.info("Loaded webcams configuration from file")
        except Exception as e:
            logger.error(f"Error loading webcams config: {e}")
            WEBCAMS_API_KEY = os.environ.get('WINDY_WEBCAMS_API_KEY', '')
    else:
        # Try environment variables
        WEBCAMS_API_KEY = os.environ.get('WINDY_WEBCAMS_API_KEY', '')

def save_webcams_config():
    """Save webcams API keys to config file"""
    global WEBCAMS_API_KEY
    try:
        # Read existing config if it exists
        config = {}
        if os.path.exists(WEBCAMS_CONFIG_FILE):
            try:
                with open(WEBCAMS_CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except:
                pass
        
        # Update with current API key (only if not from environment)
        if not os.environ.get('WINDY_WEBCAMS_API_KEY'):
            config['windy_webcams_api_key'] = WEBCAMS_API_KEY
        
        with open(WEBCAMS_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.debug(f"Webcams config saved to {WEBCAMS_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Error saving webcams config: {e}")

# ----------------------
# Webhook URL Persistence (must be early in file)
# ----------------------
WEBHOOK_URL_FILE = os.path.join(BASE_DIR, "webhook_url.json")

def save_webhook_url():
    """Save the current webhook URL to disk"""
    global WEBHOOK_URL
    try:
        with open(WEBHOOK_URL_FILE, "w") as f:
            json.dump({"webhook_url": WEBHOOK_URL}, f)
        logger.debug(f"Webhook URL saved to {WEBHOOK_URL_FILE}")
    except Exception as e:
        logger.error(f"Error saving webhook URL: {e}")

def load_webhook_url():
    """Load the webhook URL from disk on startup"""
    global WEBHOOK_URL
    if os.path.exists(WEBHOOK_URL_FILE):
        try:
            with open(WEBHOOK_URL_FILE, "r") as f:
                data = json.load(f)
                WEBHOOK_URL = data.get("webhook_url", None)
                if WEBHOOK_URL:
                    logger.info(f"Loaded saved webhook URL: {WEBHOOK_URL}")
                else:
                    logger.info("No webhook URL found in saved file")
        except Exception as e:
            logger.error(f"Error loading webhook URL: {e}")
            WEBHOOK_URL = None
    else:
        logger.info("No saved webhook URL file found")
        WEBHOOK_URL = None

# ----------------------
# Global Variables & Files
# ----------------------
tracked_pairs = {}
detection_history = deque(maxlen=MAX_DETECTION_HISTORY)  # Limit size to prevent memory growth

# Changed: Instead of one selected port, we allow up to three.
SELECTED_PORTS = {}  # key will be 'port1', 'port2', 'port3'
BAUD_RATE = 115200
staleThreshold = 60  # Global stale threshold in seconds (changed from 300 seconds -> 1 minute)
# For each port, we track its connection status.
serial_connected_status = {}  # e.g. {"port1": True, "port2": False, ...}
# Mapping to merge fragmented detections: port -> last seen mac
last_mac_by_port = {}

# Track open serial objects for cleanup
serial_objs = {}
serial_objs_lock = threading.Lock()

startup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
# Updated detections CSV header to include faa_data.
CSV_FILENAME = os.path.join(BASE_DIR, f"detections_{startup_timestamp}.csv")
KML_FILENAME = os.path.join(BASE_DIR, f"detections_{startup_timestamp}.kml")
FAA_LOG_FILENAME = os.path.join(BASE_DIR, "faa_log.csv")  # FAA log CSV remains basic

# Cumulative KML file for all detections
CUMULATIVE_KML_FILENAME = os.path.join(BASE_DIR, "cumulative.kml")
# Initialize cumulative KML on first run
if not os.path.exists(CUMULATIVE_KML_FILENAME):
    with open(CUMULATIVE_KML_FILENAME, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">\n')
        f.write('<Document>\n')
        f.write(f'<name>Cumulative Detections</name>\n')
        f.write('</Document>\n</kml>')

# Write CSV header for detections.
with open(CSV_FILENAME, mode='w', newline='') as csvfile:
    fieldnames = [
        'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
        'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
    ]
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

# Cumulative CSV file for all detections
CUMULATIVE_CSV_FILENAME = os.path.join(BASE_DIR, f"cumulative_detections.csv")
# Initialize cumulative CSV on first run
if not os.path.exists(CUMULATIVE_CSV_FILENAME):
    with open(CUMULATIVE_CSV_FILENAME, mode='w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[
            'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
            'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
        ])
        writer.writeheader()

# Create FAA log CSV with header if not exists.
if not os.path.exists(FAA_LOG_FILENAME):
    with open(FAA_LOG_FILENAME, mode='w', newline='') as csvfile:
        fieldnames = ['timestamp', 'mac', 'remote_id', 'faa_response']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

# --- Alias Persistence ---
ALIASES_FILE = os.path.join(BASE_DIR, "aliases.json")
PORTS_FILE = os.path.join(BASE_DIR, "selected_ports.json")
ZONES_FILE = os.path.join(BASE_DIR, "zones.json")
INCIDENT_LOG_FILE = os.path.join(BASE_DIR, "incident_log.json")
ALIASES = {}
if os.path.exists(ALIASES_FILE):
    try:
        with open(ALIASES_FILE, "r") as f:
            ALIASES = json.load(f)
    except Exception as e:
        print("Error loading aliases:", e)

def save_aliases():
    global ALIASES
    try:
        with open(ALIASES_FILE, "w") as f:
            json.dump(ALIASES, f)
    except Exception as e:
        print("Error saving aliases:", e)

# --- Port Persistence ---
def save_selected_ports():
    global SELECTED_PORTS
    try:
        with open(PORTS_FILE, "w") as f:
            json.dump(SELECTED_PORTS, f)
    except Exception as e:
        print("Error saving selected ports:", e)

def load_selected_ports():
    global SELECTED_PORTS
    if os.path.exists(PORTS_FILE):
        try:
            with open(PORTS_FILE, "r") as f:
                SELECTED_PORTS = json.load(f)
        except Exception as e:
            print("Error loading selected ports:", e)

# ----------------------
# Geofencing & Zones
# ----------------------
ZONES = []
drone_zones = {}  # mac -> set of zone IDs currently in

def load_zones():
    global ZONES
    if os.path.exists(ZONES_FILE):
        try:
            with open(ZONES_FILE, "r") as f:
                ZONES = json.load(f)
            # Filter out expired NOTAM zones
            filter_expired_notam_zones()
        except Exception as e:
            logger.warning(f"Error loading zones: {e}")
            ZONES = []

def filter_expired_notam_zones():
    """Remove expired NOTAM zones from the zones list"""
    global ZONES
    current_time = datetime.now()
    initial_count = len(ZONES)
    
    ZONES = [zone for zone in ZONES if not is_notam_expired(zone, current_time)]
    
    removed_count = initial_count - len(ZONES)
    if removed_count > 0:
        logger.info(f"Removed {removed_count} expired NOTAM zones")
        save_zones()

def is_notam_expired(zone, current_time=None):
    """Check if a NOTAM zone has expired"""
    if zone.get('source') != 'notam':
        return False  # Not a NOTAM zone
    
    if current_time is None:
        current_time = datetime.now()
    
    end_date_str = zone.get('end_date')
    if not end_date_str:
        return False  # No end date, assume still active
    
    try:
        # Handle different date formats
        if 'Z' in end_date_str or '+' in end_date_str:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        else:
            end_date = datetime.fromisoformat(end_date_str)
        
        return end_date < current_time
    except Exception as e:
        logger.debug(f"Error checking NOTAM expiration for zone {zone.get('id')}: {e}")
        return False  # On error, assume still active

def save_zones():
    global ZONES
    try:
        with open(ZONES_FILE, "w") as f:
            json.dump(ZONES, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving zones: {e}")

def point_in_polygon(lat, lon, polygon):
    """Check if a point is inside a polygon using ray casting algorithm"""
    if not polygon or len(polygon) < 3:
        return False
    
    n = len(polygon)
    inside = False
    j = n - 1
    
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    
    return inside

def check_zone_events(detection):
    """Check if drone entered/exited any zones and log incidents"""
    global drone_zones, ZONES, INCIDENT_LOG
    
    mac = detection.get("mac")
    drone_lat = detection.get("drone_lat", 0)
    drone_long = detection.get("drone_long", 0)
    
    if not mac or drone_lat == 0 or drone_long == 0:
        return
    
    current_zones = set()
    drone_altitude = detection.get("drone_altitude", 0)
    
    # Check which zones the drone is currently in
    for zone in ZONES:
        if not zone.get("enabled", True):
            continue
        
        polygon = zone.get("coordinates", [])
        if not polygon or len(polygon) < 3:
            continue
        
        # Check if drone is within polygon
        if not point_in_polygon(drone_lat, drone_long, polygon):
            continue
        
        # Check altitude restrictions if zone has them
        lower_alt = zone.get("lower_altitude_ft")
        upper_alt = zone.get("upper_altitude_ft")
        
        if lower_alt is not None or upper_alt is not None:
            # Zone has altitude restrictions
            if drone_altitude > 0:  # Only check if we have altitude data
                if lower_alt is not None and drone_altitude < lower_alt:
                    continue  # Drone below zone
                if upper_alt is not None and drone_altitude > upper_alt:
                    continue  # Drone above zone
            # If no altitude data, still consider it a match (conservative approach)
        
        current_zones.add(zone.get("id"))
    
    # Get previous zones for this drone
    previous_zones = drone_zones.get(mac, set())
    
    # Check for zone entries
    entered_zones = current_zones - previous_zones
    for zone_id in entered_zones:
        zone = next((z for z in ZONES if z.get("id") == zone_id), None)
        if zone:
            log_incident({
                "type": "zone_entry",
                "timestamp": datetime.now().isoformat(),
                "mac": mac,
                "alias": ALIASES.get(mac, ""),
                "zone_id": zone_id,
                "zone_name": zone.get("name", "Unknown"),
                "zone_type": zone.get("type", "warning"),
                "drone_lat": drone_lat,
                "drone_long": drone_long,
                "drone_altitude": detection.get("drone_altitude", 0),
                "basic_id": detection.get("basic_id", ""),
                "rssi": detection.get("rssi", 0)
            })
            
            # Emit zone entry event
            socketio.emit('zone_event', {
                "type": "entry",
                "mac": mac,
                "zone": zone,
                "detection": detection
            })
    
    # Check for zone exits
    exited_zones = previous_zones - current_zones
    for zone_id in exited_zones:
        zone = next((z for z in ZONES if z.get("id") == zone_id), None)
        if zone:
            log_incident({
                "type": "zone_exit",
                "timestamp": datetime.now().isoformat(),
                "mac": mac,
                "alias": ALIASES.get(mac, ""),
                "zone_id": zone_id,
                "zone_name": zone.get("name", "Unknown"),
                "zone_type": zone.get("type", "warning"),
                "drone_lat": drone_lat,
                "drone_long": drone_long,
                "drone_altitude": detection.get("drone_altitude", 0),
                "basic_id": detection.get("basic_id", ""),
                "rssi": detection.get("rssi", 0)
            })
            
            # Emit zone exit event
            socketio.emit('zone_event', {
                "type": "exit",
                "mac": mac,
                "zone": zone,
                "detection": detection
            })
    
    # Update current zones for this drone
    drone_zones[mac] = current_zones

# ----------------------
# UK Airspace (OpenAir) Download & Parsing
# ----------------------
OPENAIR_URL = "https://asselect.uk/default/openair.txt"
OPENAIR_FILE = os.path.join(BASE_DIR, "openair.txt")
OPENAIR_CACHE_FILE = os.path.join(BASE_DIR, "openair_cache.json")

# ----------------------
# UK NOTAM Download & Parsing
# ----------------------
NOTAM_URL = "https://raw.githubusercontent.com/Jonty/uk-notam-archive/main/data/PIB.xml"
NOTAM_FILE = os.path.join(BASE_DIR, "notam.xml")

def download_openair_file():
    """Download the UK airspace OpenAir file"""
    try:
        logger.info(f"Downloading UK airspace data from {OPENAIR_URL}")
        response = requests.get(OPENAIR_URL, timeout=30)
        response.raise_for_status()
        
        with open(OPENAIR_FILE, 'w', encoding='utf-8') as f:
            f.write(response.text)
        
        logger.info(f"Successfully downloaded OpenAir file to {OPENAIR_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error downloading OpenAir file: {e}")
        return False

def parse_dms_to_decimal(dms_str):
    """Parse degrees:minutes:seconds format to decimal degrees
    Format: DD:MM:SS N/S/E/W
    """
    try:
        parts = dms_str.strip().split()
        if len(parts) < 2:
            return None
        
        coord_str = parts[0]
        direction = parts[1].upper()
        
        # Split by colon
        dms_parts = coord_str.split(':')
        if len(dms_parts) != 3:
            return None
        
        degrees = float(dms_parts[0])
        minutes = float(dms_parts[1])
        seconds = float(dms_parts[2])
        
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        
        if direction in ('S', 'W'):
            decimal = -decimal
        
        return decimal
    except Exception as e:
        logger.debug(f"Error parsing DMS '{dms_str}': {e}")
        return None

def parse_altitude(alt_str):
    """Parse altitude string to feet
    Formats: SFC, FL###, ### ft, ###ft
    """
    if not alt_str:
        return None
    
    alt_str = alt_str.strip().upper()
    
    if alt_str == 'SFC':
        return 0
    
    if alt_str.startswith('FL'):
        # Flight level (FL100 = 10,000 ft)
        try:
            fl_num = float(alt_str[2:])
            return int(fl_num * 100)
        except:
            return None
    
    # Try to extract number
    try:
        # Remove 'ft' if present
        alt_str = alt_str.replace('FT', '').replace('FT', '').strip()
        return int(float(alt_str))
    except:
        return None

def parse_openair_file():
    """Parse OpenAir format file and extract airspace definitions"""
    if not os.path.exists(OPENAIR_FILE):
        logger.warning(f"OpenAir file not found: {OPENAIR_FILE}")
        return []
    
    airspaces = []
    current_airspace = None
    current_points = []
    current_arcs = []
    
    try:
        with open(OPENAIR_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                # Skip comments and empty lines
                if not line or line.startswith('*'):
                    # End of airspace definition
                    if line.startswith('*') and current_airspace:
                        if current_points:
                            current_airspace['coordinates'] = current_points
                            airspaces.append(current_airspace)
                        current_airspace = None
                        current_points = []
                        current_arcs = []
                    continue
                
                # Airspace class
                if line.startswith('AC '):
                    # Save previous airspace if exists
                    if current_airspace and current_points:
                        current_airspace['coordinates'] = current_points
                        airspaces.append(current_airspace)
                    
                    ac_type = line[3:].strip()
                    current_airspace = {
                        'class': ac_type,
                        'name': '',
                        'lower_alt': None,
                        'upper_alt': None,
                        'frequency': None,
                        'coordinates': [],
                        'arcs': []
                    }
                    current_points = []
                    current_arcs = []
                
                # Airspace name
                elif line.startswith('AN ') and current_airspace:
                    current_airspace['name'] = line[3:].strip()
                
                # Airspace frequency
                elif line.startswith('AF ') and current_airspace:
                    current_airspace['frequency'] = line[3:].strip()
                
                # Lower altitude
                elif line.startswith('AL ') and current_airspace:
                    alt_str = line[3:].strip()
                    current_airspace['lower_alt'] = parse_altitude(alt_str)
                
                # Upper altitude
                elif line.startswith('AH ') and current_airspace:
                    alt_str = line[3:].strip()
                    current_airspace['upper_alt'] = parse_altitude(alt_str)
                
                # Data point (coordinate)
                elif line.startswith('DP ') and current_airspace:
                    coord_str = line[3:].strip()
                    # Format: "DD:MM:SS N DD:MM:SS W" or "DD:MM:SS N DD:MM:SS E"
                    parts = coord_str.split()
                    if len(parts) >= 4:
                        lat_str = f"{parts[0]} {parts[1]}"
                        lon_str = f"{parts[2]} {parts[3]}"
                        
                        lat = parse_dms_to_decimal(lat_str)
                        lon = parse_dms_to_decimal(lon_str)
                        
                        if lat is not None and lon is not None:
                            current_points.append([lat, lon])
                
                # Circle definition (for circular airspaces like glider fields)
                elif line.startswith('V X=') and current_airspace:
                    # Center point - format: "V X=DD:MM:SS N DD:MM:SS W"
                    coord_str = line[4:].strip()  # Skip "V X="
                    parts = coord_str.split()
                    if len(parts) >= 4:
                        lat_str = f"{parts[0]} {parts[1]}"
                        lon_str = f"{parts[2]} {parts[3]}"
                        
                        lat = parse_dms_to_decimal(lat_str)
                        lon = parse_dms_to_decimal(lon_str)
                        
                        if lat is not None and lon is not None:
                            current_airspace['circle_center'] = [lat, lon]
                
                # Circle radius
                elif line.startswith('DC ') and current_airspace:
                    try:
                        radius_nm = float(line[3:].strip())
                        current_airspace['circle_radius_nm'] = radius_nm
                    except:
                        pass
                
                # Variable arc (for curved boundaries)
                elif line.startswith('V D=') and current_airspace:
                    # Arc direction
                    direction = line[3:].strip()
                    current_airspace['arc_direction'] = direction
                
                # Database arc (connects points with arc)
                elif line.startswith('DB ') and current_airspace:
                    # Arc between two points - we'll approximate with straight line for now
                    # Full arc parsing is complex, so we skip the arc and use straight lines
                    pass
        
        # Save last airspace if exists
        if current_airspace and current_points:
            current_airspace['coordinates'] = current_points
            airspaces.append(current_airspace)
        
        logger.info(f"Parsed {len(airspaces)} airspace definitions from OpenAir file")
        return airspaces
    
    except Exception as e:
        logger.error(f"Error parsing OpenAir file: {e}")
        return []

def generate_circle_polygon(center_lat, center_lon, radius_nm, num_points=32):
    """Generate a polygon approximating a circle"""
    # Convert nautical miles to degrees (approximate)
    # 1 nm ≈ 0.0167 degrees at equator, but varies with latitude
    lat_radius = radius_nm / 60.0  # 1 degree latitude ≈ 60 nm
    lon_radius = radius_nm / (60.0 * math.cos(math.radians(center_lat)))
    
    points = []
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        lat = center_lat + lat_radius * math.sin(angle)
        lon = center_lon + lon_radius * math.cos(angle)
        points.append([lat, lon])
    
    return points

def convert_airspaces_to_zones(airspaces, max_altitude_ft=400):
    """Convert airspace definitions to zones, filtering for drone-relevant airspaces
    max_altitude_ft: Maximum altitude to consider (default 400ft for typical drone operations)
    """
    zones = []
    zone_id_counter = 1
    
    # Airspace classes relevant to drones
    # FRZ = Flight Restriction Zone (critical)
    # CTA = Control Area (warning)
    # TMZ = Transponder Mandatory Zone (warning)
    # A = Class A airspace (critical)
    # G = Glider airfields (warning)
    # P = Prohibited areas (critical)
    # RMZ = Radio Mandatory Zone (warning)
    
    drone_relevant_classes = ['FRZ', 'CTA', 'TMZ', 'A', 'G', 'P', 'RMZ', 'D']
    
    for airspace in airspaces:
        ac_class = airspace.get('class', '').strip()
        
        # Only process drone-relevant airspace classes
        if ac_class not in drone_relevant_classes:
            continue
        
        # Check if altitude range is relevant for drones
        lower_alt = airspace.get('lower_alt')
        upper_alt = airspace.get('upper_alt')
        
        # Skip if airspace is entirely above max drone altitude
        if lower_alt is not None and lower_alt > max_altitude_ft:
            continue
        
        # Determine zone type based on airspace class
        if ac_class in ['FRZ', 'A', 'P']:
            zone_type = 'critical'
        else:
            zone_type = 'warning'
        
        # Get coordinates
        coordinates = []
        
        # Handle circular airspaces (glider fields, etc.)
        if 'circle_center' in airspace and 'circle_radius_nm' in airspace:
            center = airspace['circle_center']
            radius = airspace['circle_radius_nm']
            coordinates = generate_circle_polygon(center[0], center[1], radius)
        elif airspace.get('coordinates'):
            coordinates = airspace['coordinates']
        
        if not coordinates or len(coordinates) < 3:
            continue
        
        # Create zone name
        name = airspace.get('name', f"{ac_class} Airspace")
        if upper_alt or lower_alt:
            alt_info = []
            if lower_alt:
                alt_info.append(f"{lower_alt}ft")
            if upper_alt:
                alt_info.append(f"FL{int(upper_alt/100)}" if upper_alt >= 10000 else f"{upper_alt}ft")
            if alt_info:
                name += f" ({'-'.join(alt_info)})"
        
        # Create zone
        zone = {
            'id': f"openair_{zone_id_counter}",
            'name': name,
            'type': zone_type,
            'coordinates': coordinates,
            'enabled': True,
            'source': 'openair',
            'airspace_class': ac_class,
            'lower_altitude_ft': lower_alt,
            'upper_altitude_ft': upper_alt,
            'frequency': airspace.get('frequency')
        }
        
        zones.append(zone)
        zone_id_counter += 1
    
    logger.info(f"Converted {len(zones)} airspaces to zones")
    return zones

def update_zones_from_openair(max_altitude_ft=400, merge_with_existing=True):
    """Download and update zones from UK OpenAir airspace data"""
    global ZONES
    
    # Download latest file
    if not download_openair_file():
        logger.error("Failed to download OpenAir file")
        return False
    
    # Parse airspaces
    airspaces = parse_openair_file()
    if not airspaces:
        logger.warning("No airspaces parsed from OpenAir file")
        return False
    
    # Convert to zones
    openair_zones = convert_airspaces_to_zones(airspaces, max_altitude_ft)
    
    if not openair_zones:
        logger.warning("No drone-relevant zones created from OpenAir data")
        return False
    
    # Merge with existing zones or replace
    if merge_with_existing:
        # Remove old OpenAir zones
        ZONES = [z for z in ZONES if z.get('source') != 'openair']
        # Add new OpenAir zones
        ZONES.extend(openair_zones)
        logger.info(f"Merged {len(openair_zones)} OpenAir zones with existing zones")
    else:
        ZONES = openair_zones
        logger.info(f"Replaced all zones with {len(openair_zones)} OpenAir zones")
    
    # Save zones
    save_zones()
    
    # Emit zones update to connected clients
    try:
        socketio.emit('zones_updated', {"zones": ZONES, "count": len(ZONES)})
    except:
        pass
    
    return True

def download_notam_file():
    """Download the UK NOTAM PIB.xml file"""
    try:
        logger.info(f"Downloading UK NOTAM data from {NOTAM_URL}")
        response = requests.get(NOTAM_URL, timeout=30)
        response.raise_for_status()
        
        with open(NOTAM_FILE, 'w', encoding='utf-8') as f:
            f.write(response.text)
        
        logger.info(f"Successfully downloaded NOTAM file to {NOTAM_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error downloading NOTAM file: {e}")
        return False

def parse_notam_coordinates(coord_str, radius_nm=None):
    """Parse NOTAM coordinate string to decimal degrees
    Format: "DDMMN/SDDDMME/W" or "DDMMN/S DDDMME/W"
    Examples: "5229N01900W", "601619N 0200334W"
    """
    if not coord_str:
        return None, None
    
    try:
        # Remove spaces and convert to uppercase
        coord_str = coord_str.replace(' ', '').upper()
        
        # Find N/S and E/W positions
        lat_end = max(coord_str.find('N'), coord_str.find('S'))
        if lat_end == -1:
            return None, None
        
        lon_start = lat_end + 1
        lon_end = max(coord_str.find('E', lon_start), coord_str.find('W', lon_start))
        if lon_end == -1:
            return None, None
        
        lat_str = coord_str[:lat_end+1]
        lon_str = coord_str[lon_start:lon_end+1]
        
        # Parse latitude (format: DDMMN or DDDMMN)
        lat_dir = lat_str[-1]
        lat_deg_min = lat_str[:-1]
        
        if len(lat_deg_min) == 4:  # DDMM
            lat_deg = float(lat_deg_min[:2])
            lat_min = float(lat_deg_min[2:])
        elif len(lat_deg_min) == 5:  # DDDMM
            lat_deg = float(lat_deg_min[:3])
            lat_min = float(lat_deg_min[3:])
        elif len(lat_deg_min) == 6:  # DDMMSS
            lat_deg = float(lat_deg_min[:2])
            lat_min = float(lat_deg_min[2:4])
            lat_sec = float(lat_deg_min[4:])
            lat = lat_deg + lat_min / 60.0 + lat_sec / 3600.0
        else:
            return None, None
        
        if len(lat_deg_min) < 6:
            lat = lat_deg + lat_min / 60.0
        
        if lat_dir == 'S':
            lat = -lat
        
        # Parse longitude (format: DDDMME or DDDDMME)
        lon_dir = lon_str[-1]
        lon_deg_min = lon_str[:-1]
        
        if len(lon_deg_min) == 5:  # DDDMM
            lon_deg = float(lon_deg_min[:3])
            lon_min = float(lon_deg_min[3:])
        elif len(lon_deg_min) == 6:  # DDDDMM
            lon_deg = float(lon_deg_min[:4])
            lon_min = float(lon_deg_min[4:])
        elif len(lon_deg_min) == 7:  # DDDMMSS
            lon_deg = float(lon_deg_min[:3])
            lon_min = float(lon_deg_min[3:5])
            lon_sec = float(lon_deg_min[5:])
            lon = lon_deg + lon_min / 60.0 + lon_sec / 3600.0
        elif len(lon_deg_min) == 8:  # DDDDMMSS
            lon_deg = float(lon_deg_min[:4])
            lon_min = float(lon_deg_min[4:6])
            lon_sec = float(lon_deg_min[6:])
            lon = lon_deg + lon_min / 60.0 + lon_sec / 3600.0
        else:
            return None, None
        
        if len(lon_deg_min) < 7:
            lon = lon_deg + lon_min / 60.0
        
        if lon_dir == 'W':
            lon = -lon
        
        return lat, lon
    except Exception as e:
        logger.debug(f"Error parsing NOTAM coordinate '{coord_str}': {e}")
        return None, None

def parse_notam_date(date_str):
    """Parse NOTAM date string to datetime
    Format: YYMMDDHHMM (e.g., "2512021800" = 2025-12-02 18:00)
    """
    if not date_str or len(date_str) < 10:
        return None
    
    try:
        year = 2000 + int(date_str[0:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        hour = int(date_str[6:8])
        minute = int(date_str[8:10])
        return datetime(year, month, day, hour, minute)
    except Exception as e:
        logger.debug(f"Error parsing NOTAM date '{date_str}': {e}")
        return None

def parse_notam_file():
    """Parse UK NOTAM PIB.xml file and extract NOTAMs"""
    if not os.path.exists(NOTAM_FILE):
        logger.warning(f"NOTAM file not found: {NOTAM_FILE}")
        return []
    
    notams = []
    current_time = datetime.now()
    
    try:
        tree = ET.parse(NOTAM_FILE)
        root = tree.getroot()
        
        # Find all NOTAM elements (they can be in different sections)
        # First, try to find all NOTAMs directly
        all_notam_elements = root.findall('.//Notam')
        
        # Also try to find by section
        section_notams = {}
        for section_name in ['Aerodrome', 'En-route', 'Warnings']:
            section_elem = root.find(f'.//{section_name}')
            if section_elem is not None:
                notam_list = section_elem.find('NotamList')
                if notam_list is not None:
                    section_notams[section_name] = notam_list.findall('Notam')
        
        # Use section-specific NOTAMs if found, otherwise use all
        if any(section_notams.values()):
            notam_elements_by_section = section_notams
        else:
            # Fallback: use all NOTAMs and assign to a default section
            notam_elements_by_section = {'All': all_notam_elements}
        
        # Process NOTAMs by section
        for section_name, notam_elements in notam_elements_by_section.items():
            if not notam_elements:
                continue
            
            for notam_elem in notam_elements:
                try:
                    # Extract NOTAM data
                    coordinates_elem = notam_elem.find('Coordinates')
                    radius_elem = notam_elem.find('Radius')
                    start_validity_elem = notam_elem.find('StartValidity')
                    end_validity_elem = notam_elem.find('EndValidity')
                    item_elem = notam_elem.find('ItemE')  # Description
                    qline_elem = notam_elem.find('QLine')
                    
                    # Check if coordinates exist and have text
                    if coordinates_elem is None:
                        continue
                    coord_text = coordinates_elem.text
                    if not coord_text or coord_text.strip() == '':
                        continue
                    
                    # Parse coordinates
                    lat, lon = parse_notam_coordinates(
                        coordinates_elem.text,
                        float(radius_elem.text) if radius_elem is not None and radius_elem.text else None
                    )
                    
                    if lat is None or lon is None:
                        continue
                    
                    # Parse validity dates
                    start_date = None
                    end_date = None
                    if start_validity_elem is not None and start_validity_elem.text:
                        start_date = parse_notam_date(start_validity_elem.text)
                    if end_validity_elem is not None and end_validity_elem.text:
                        end_date = parse_notam_date(end_validity_elem.text)
                    
                    # Skip if NOTAM is expired
                    if end_date and end_date < current_time:
                        continue
                    
                    # Get description (keep full length for popup, truncate only for storage)
                    description = ""
                    if item_elem is not None and item_elem.text:
                        description = item_elem.text.strip()  # Keep full description
                    
                    # Get NOTAM identifier
                    series_elem = notam_elem.find('Series')
                    number_elem = notam_elem.find('Number')
                    year_elem = notam_elem.find('Year')
                    notam_id = ""
                    if series_elem is not None and number_elem is not None and year_elem is not None:
                        notam_id = f"{series_elem.text}{number_elem.text}/{year_elem.text}"
                    
                    # Get radius
                    radius_nm = None
                    if radius_elem is not None and radius_elem.text:
                        try:
                            radius_nm = float(radius_elem.text)
                        except:
                            pass
                    
                    # Get altitude limits
                    lower_alt = None
                    upper_alt = None
                    if qline_elem is not None:
                        lower_elem = qline_elem.find('Lower')
                        upper_elem = qline_elem.find('Upper')
                        if lower_elem is not None and lower_elem.text:
                            try:
                                lower_alt = int(lower_elem.text) * 100  # Convert FL to feet
                            except:
                                pass
                        if upper_elem is not None and upper_elem.text:
                            try:
                                upper_alt = int(upper_elem.text) * 100  # Convert FL to feet
                            except:
                                pass
                    
                    notam = {
                        'id': notam_id,
                        'coordinates': [lat, lon],
                        'radius_nm': radius_nm,
                        'description': description,
                        'start_date': start_date.isoformat() if start_date else None,
                        'end_date': end_date.isoformat() if end_date else None,
                        'lower_altitude_ft': lower_alt,
                        'upper_altitude_ft': upper_alt,
                        'section': section_name
                    }
                    
                    notams.append(notam)
                except Exception as e:
                    logger.debug(f"Error parsing NOTAM element: {e}")
                    continue
        
        logger.info(f"Parsed {len(notams)} active NOTAMs from file")
        return notams
    
    except Exception as e:
        logger.error(f"Error parsing NOTAM file: {e}")
        return []

def convert_notams_to_zones(notams, max_altitude_ft=400):
    """Convert NOTAM definitions to zones, filtering for drone-relevant NOTAMs"""
    zones = []
    zone_id_counter = 1
    
    for notam in notams:
        # Check if altitude range is relevant for drones
        lower_alt = notam.get('lower_altitude_ft')
        upper_alt = notam.get('upper_altitude_ft')
        
        # Skip if NOTAM is entirely above max drone altitude
        if lower_alt is not None and lower_alt > max_altitude_ft:
            continue
        
        # Get coordinates
        center = notam.get('coordinates')
        radius_nm = notam.get('radius_nm')
        
        if not center or len(center) < 2:
            continue
        
        # Determine zone type based on NOTAM section and content
        section = notam.get('section', '')
        description = notam.get('description', '').upper()
        
        # Critical NOTAMs (restrictions, prohibitions, etc.)
        if any(keyword in description for keyword in ['PROHIBITED', 'RESTRICTED', 'DANGER', 'HAZARD', 'SECURITY', 'MILITARY']):
            zone_type = 'critical'
        else:
            zone_type = 'warning'
        
        # Create coordinates - circular zone if radius provided, otherwise point
        coordinates = []
        if radius_nm and radius_nm > 0 and radius_nm < 999:  # 999 often means "all" or "unlimited"
            coordinates = generate_circle_polygon(center[0], center[1], radius_nm)
        else:
            # For point NOTAMs or very large radius, create a small circle (1nm)
            coordinates = generate_circle_polygon(center[0], center[1], 1.0)
        
        if not coordinates or len(coordinates) < 3:
            continue
        
        # Create zone name
        notam_id = notam.get('id', f'NOTAM-{zone_id_counter}')
        name = f"NOTAM {notam_id}"
        if radius_nm and radius_nm < 999:
            name += f" ({radius_nm}nm)"
        
        # Add validity info
        end_date = notam.get('end_date')
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                name += f" until {end_dt.strftime('%Y-%m-%d %H:%M')}"
            except:
                pass
        
        # Create zone
        zone = {
            'id': f"notam_{zone_id_counter}",
            'name': name,
            'type': zone_type,
            'coordinates': coordinates,
            'enabled': True,
            'source': 'notam',
            'notam_id': notam_id,
            'description': notam.get('description', ''),  # Full description for popup
            'lower_altitude_ft': lower_alt,
            'upper_altitude_ft': upper_alt,
            'end_date': end_date
        }
        
        zones.append(zone)
        zone_id_counter += 1
    
    logger.info(f"Converted {len(zones)} NOTAMs to zones")
    return zones

def update_zones_from_notam(max_altitude_ft=400, merge_with_existing=True):
    """Download and update zones from UK NOTAM data"""
    global ZONES
    
    # Download latest file
    if not download_notam_file():
        logger.error("Failed to download NOTAM file")
        return False
    
    # Parse NOTAMs
    notams = parse_notam_file()
    if not notams:
        logger.warning("No NOTAMs parsed from file")
        return False
    
    # Convert to zones
    notam_zones = convert_notams_to_zones(notams, max_altitude_ft)
    
    if not notam_zones:
        logger.warning("No drone-relevant zones created from NOTAM data")
        return False
    
    # Merge with existing zones or replace
    if merge_with_existing:
        # Remove old NOTAM zones
        ZONES = [z for z in ZONES if z.get('source') != 'notam']
        # Add new NOTAM zones
        ZONES.extend(notam_zones)
        logger.info(f"Merged {len(notam_zones)} NOTAM zones with existing zones")
    else:
        ZONES = notam_zones
        logger.info(f"Replaced all zones with {len(notam_zones)} NOTAM zones")
    
    # Save zones
    save_zones()
    
    # Emit zones update to connected clients
    try:
        socketio.emit('zones_updated', {"zones": ZONES, "count": len(ZONES)})
    except:
        pass
    
    return True

# ----------------------
# Incident Logging
# ----------------------
INCIDENT_LOG = []
MAX_INCIDENT_LOG_SIZE = 10000  # Keep last 10k incidents

def load_incident_log():
    global INCIDENT_LOG
    if os.path.exists(INCIDENT_LOG_FILE):
        try:
            with open(INCIDENT_LOG_FILE, "r") as f:
                INCIDENT_LOG = json.load(f)
        except Exception as e:
            logger.warning(f"Error loading incident log: {e}")
            INCIDENT_LOG = []

def save_incident_log():
    global INCIDENT_LOG
    try:
        # Keep only recent incidents to prevent file from growing too large
        if len(INCIDENT_LOG) > MAX_INCIDENT_LOG_SIZE:
            INCIDENT_LOG = INCIDENT_LOG[-MAX_INCIDENT_LOG_SIZE:]
        
        with open(INCIDENT_LOG_FILE, "w") as f:
            json.dump(INCIDENT_LOG, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving incident log: {e}")

def log_incident(incident_data):
    """Log an incident to the incident log"""
    global INCIDENT_LOG
    
    # Add incident
    INCIDENT_LOG.append(incident_data)
    
    # Auto-save periodically (every 10 incidents)
    if len(INCIDENT_LOG) % 10 == 0:
        save_incident_log()
    
    # Emit to connected clients
    socketio.emit('new_incident', incident_data)

# Initialize zones and incident log
load_zones()
load_incident_log()

def auto_connect_to_saved_ports():
    """
    Check if any previously saved ports are available and auto-connect to them.
    Returns True if at least one port was connected, False otherwise.
    """
    global SELECTED_PORTS
    
    if not SELECTED_PORTS:
        logger.info("No saved ports found for auto-connection")
        return False
    
    # Get currently available ports
    available_ports = {p.device for p in serial.tools.list_ports.comports()}
    logger.debug(f"Available ports: {available_ports}")
    
    # Check which saved ports are still available
    available_saved_ports = {}
    for port_key, port_device in SELECTED_PORTS.items():
        if port_device in available_ports:
            available_saved_ports[port_key] = port_device
    
    if not available_saved_ports:
        logger.warning("No previously used ports are currently available")
        return False
    
    logger.info(f"Auto-connecting to previously used ports: {list(available_saved_ports.values())}")
    
    # Update SELECTED_PORTS to only include available ports
    SELECTED_PORTS = available_saved_ports
    
    # Start serial threads for available ports
    for port in SELECTED_PORTS.values():
        serial_connected_status[port] = False
        start_serial_thread(port)
        logger.info(f"Started serial thread for port: {port}")
    
    # Send watchdog reset to each microcontroller over USB
    time.sleep(2)  # Give threads time to establish connections
    with serial_objs_lock:
        for port, ser in serial_objs.items():
            try:
                if ser and ser.is_open:
                    ser.write(b'WATCHDOG_RESET\n')
                    logger.debug(f"Sent watchdog reset to {port}")
            except Exception as e:
                logger.error(f"Failed to send watchdog reset to {port}: {e}")
    
    return True

# ----------------------
# Enhanced Port Monitoring
# ----------------------
def monitor_ports():
    """
    Continuously monitor for port availability changes and auto-connect when possible.
    This runs in a separate thread for headless operation.
    """
    logger.info("Starting port monitoring thread...")
    last_available_ports = set()
    
    while not SHUTDOWN_EVENT.is_set():
        try:
            # Get currently available ports
            current_ports = {p.device for p in serial.tools.list_ports.comports()}
            
            # Check if port availability has changed
            if current_ports != last_available_ports:
                logger.info(f"Port availability changed. Current ports: {current_ports}")
                
                # If we have saved ports but no active connections, try to auto-connect
                if SELECTED_PORTS and not any(serial_connected_status.values()):
                    logger.info("Attempting auto-connection to saved ports...")
                    if auto_connect_to_saved_ports():
                        logger.info("Auto-connection successful! Mapping is now active.")
                    else:
                        logger.info("Auto-connection failed. Waiting for ports...")
                
                # Check for disconnected ports
                for port in list(serial_connected_status.keys()):
                    if port not in current_ports and serial_connected_status.get(port, False):
                        logger.warning(f"Port {port} disconnected")
                        serial_connected_status[port] = False
                        
                        # Broadcast the updated status immediately
                        emit_serial_status()
                        
                        with serial_objs_lock:
                            if port in serial_objs:
                                try:
                                    serial_objs[port].close()
                                except:
                                    pass
                                del serial_objs[port]
                
                last_available_ports = current_ports.copy()
            
            # Wait before next check
            SHUTDOWN_EVENT.wait(PORT_MONITOR_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in port monitoring: {e}")
            SHUTDOWN_EVENT.wait(5)  # Wait 5 seconds before retrying

def start_port_monitoring():
    """Start the port monitoring thread"""
    if AUTO_START_ENABLED:
        monitor_thread = threading.Thread(target=monitor_ports, daemon=True)
        monitor_thread.start()
        logger.info("Port monitoring thread started")

# ----------------------
# Enhanced Status Reporting
# ----------------------
def log_system_status():
    """Log current system status for headless monitoring"""
    logger.info("=== SYSTEM STATUS ===")
    logger.info(f"Selected ports: {SELECTED_PORTS}")
    logger.info(f"Serial connection status: {serial_connected_status}")
    logger.info(f"Active detections: {len(detection_history)}")
    logger.info(f"Tracked MACs: {len(set(d.get('mac') for d in detection_history if d.get('mac')))}")
    logger.info(f"Headless mode: {HEADLESS_MODE}")
    logger.info("====================")

def start_status_logging():
    """Start periodic status logging for headless operation"""
    def status_logger():
        while not SHUTDOWN_EVENT.is_set():
            log_system_status()
            SHUTDOWN_EVENT.wait(300)  # Log status every 5 minutes
    
    if HEADLESS_MODE:
        status_thread = threading.Thread(target=status_logger, daemon=True)
        status_thread.start()
        logger.info("Status logging thread started")

def start_websocket_broadcaster():
    """Start background task to broadcast WebSocket updates every 5 seconds (optimized)"""
    def broadcaster():
        while not SHUTDOWN_EVENT.is_set():
            try:
                # Only emit if there are connected clients to reduce CPU usage
                if hasattr(socketio, 'server') and hasattr(socketio.server, 'manager'):
                    # Emit critical data more frequently
                    emit_detections()
                    emit_serial_status()
                    
                    # Emit less critical data less frequently
                    if int(time.time()) % 10 == 0:  # Every 10 seconds
                        emit_paths()
                        emit_aliases()
                    
                    if int(time.time()) % 30 == 0:  # Every 30 seconds
                        emit_cumulative_log()
                        emit_faa_cache()
                        emit_aprs_stations()
            except Exception as e:
                # Ignore errors if no clients connected
                pass
            
            # Wait 5 seconds instead of 2 to reduce CPU usage
            for _ in range(50):  # 50 * 0.1 = 5 seconds, but check shutdown every 0.1s
                if SHUTDOWN_EVENT.is_set():
                    break
                time.sleep(0.1)
    
    
    broadcaster_thread = threading.Thread(target=broadcaster, daemon=True)
    broadcaster_thread.start()
    logger.info("WebSocket broadcaster thread started")

# ----------------------
# FAA Cache Persistence
# ----------------------
FAA_CACHE_FILENAME = os.path.join(BASE_DIR, "faa_cache.csv")
FAA_CACHE = {}

# Load FAA cache from disk if it exists
if os.path.exists(FAA_CACHE_FILENAME):
    try:
        with open(FAA_CACHE_FILENAME, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                key = (row['mac'], row['remote_id'])
                FAA_CACHE[key] = json.loads(row['faa_response'])
    except Exception as e:
        print("Error loading FAA cache:", e)

def write_to_faa_cache(mac, remote_id, faa_data):
    key = (mac, remote_id)
    FAA_CACHE[key] = faa_data
    try:
        file_exists = os.path.isfile(FAA_CACHE_FILENAME)
        with open(FAA_CACHE_FILENAME, "a", newline='') as csvfile:
            fieldnames = ["mac", "remote_id", "faa_response"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "mac": mac,
                "remote_id": remote_id,
                "faa_response": json.dumps(faa_data)
            })
    except Exception as e:
        print("Error writing to FAA cache:", e)

# ----------------------
# KML Generation (including FAA data)
# ----------------------
def generate_kml():
    # Build sorted list of all MACs seen so far
    macs = sorted({d['mac'] for d in detection_history})

    # Use consistent color generation function
    mac_colors = {}
    for mac in macs:
        mac_colors[mac] = get_color_for_mac(mac)

    # Start KML document template
    kml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">',
        '<Document>',
        f'<name>Detections {startup_timestamp}</name>'
    ]

    for mac in macs:
        alias = ALIASES.get(mac, "")
        aliasStr = f"{alias} " if alias else ""
        color    = mac_colors[mac]

        # --- Flights grouped by staleThreshold, each in its own Folder ---
        flight_idx = 1
        last_ts = None
        current_flight = []
        for det in detection_history:
            if det.get('mac') != mac:
                continue
            lat, lon = det.get('drone_lat'), det.get('drone_long')
            ts = det.get('last_update')
            if lat and lon:
                # break flight on time gap
                if last_ts and (ts - last_ts) > staleThreshold:
                    # flush current flight
                    if len(current_flight) >= 1:
                        # start folder
                        kml_lines.append('<Folder>')
                        # include start timestamp for this flight
                        start_dt  = datetime.fromtimestamp(current_flight[0][2])
                        start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
                        kml_lines.append(f'<name>Flight {flight_idx} {aliasStr}{mac} ({start_str})</name>')
                        # drone path
                        coords = " ".join(f"{x[0]},{x[1]},0" for x in current_flight)
                        kml_lines.append(f'<Placemark><Style><LineStyle><color>{color}</color><width>2</width></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>')
                        # drone start icon
                        start_lon, start_lat, start_ts = current_flight[0]
                        kml_lines.append(f'<Placemark><name>Drone Start {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/airports.png</href></IconStyle></Style><Point><coordinates>{start_lon},{start_lat},0</coordinates></Point></Placemark>')
                        # drone end icon
                        end_lon, end_lat, end_ts = current_flight[-1]
                        kml_lines.append(f'<Placemark><name>Drone End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/heliport.png</href></IconStyle></Style><Point><coordinates>{end_lon},{end_lat},0</coordinates></Point></Placemark>')
                        # pilot path inside same flight
                        start_ts = current_flight[0][2]
                        pilot_pts = [(d['pilot_long'], d['pilot_lat']) for d in detection_history if d.get('mac')==mac and d.get('pilot_lat') and d.get('pilot_long') and d.get('last_update')>=start_ts and d.get('last_update')<=end_ts]
                        if len(pilot_pts) >= 1:
                            pc = " ".join(f"{p[0]},{p[1]},0" for p in pilot_pts)
                            kml_lines.append(f'<Placemark><name>Pilot Path {flight_idx} {aliasStr}{mac}</name><Style><LineStyle><color>{color}</color><width>2</width><gx:dash/></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{pc}</coordinates></LineString></Placemark>')
                            plon, plat = pilot_pts[-1]
                            kml_lines.append(f'<Placemark><name>Pilot End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/man.png</href></IconStyle></Style><Point><coordinates>{plon},{plat},0</coordinates></Point></Placemark>')
                        kml_lines.append('</Folder>')
                        flight_idx += 1
                    current_flight = []
                # accumulate this point
                current_flight.append((lon, lat, ts))
                last_ts = ts
        # flush final flight if any
        if current_flight:
            kml_lines.append('<Folder>')
            # include start timestamp for this flight
            start_dt  = datetime.fromtimestamp(current_flight[0][2])
            start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
            kml_lines.append(f'<name>Flight {flight_idx} {aliasStr}{mac} ({start_str})</name>')
            coords = " ".join(f"{x[0]},{x[1]},0" for x in current_flight)
            kml_lines.append(f'<Placemark><Style><LineStyle><color>{color}</color><width>2</width></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>')
            # drone start icon
            start_lon, start_lat, start_ts = current_flight[0]
            kml_lines.append(f'<Placemark><name>Drone Start {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/airports.png</href></IconStyle></Style><Point><coordinates>{start_lon},{start_lat},0</coordinates></Point></Placemark>')
            end_lon, end_lat, end_ts = current_flight[-1]
            kml_lines.append(f'<Placemark><name>Drone End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/heliport.png</href></IconStyle></Style><Point><coordinates>{end_lon},{end_lat},0</coordinates></Point></Placemark>')
            pilot_pts = [(d['pilot_long'], d['pilot_lat']) for d in detection_history if d.get('mac')==mac and d.get('pilot_lat') and d.get('pilot_long') and d.get('last_update')>=current_flight[0][2] and d.get('last_update')<=end_ts]
            if pilot_pts:
                pc = " ".join(f"{p[0]},{p[1]},0" for p in pilot_pts)
                kml_lines.append(f'<Placemark><name>Pilot Path {flight_idx} {aliasStr}{mac}</name><Style><LineStyle><color>{color}</color><width>2</width><gx:dash/></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{pc}</coordinates></LineString></Placemark>')
                plon, plat = pilot_pts[-1]
                kml_lines.append(f'<Placemark><name>Pilot End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/man.png</href></IconStyle></Style><Point><coordinates>{plon},{plat},0</coordinates></Point></Placemark>')
            kml_lines.append('</Folder>')
    # Close document
    kml_lines.append('</Document></kml>')

    # Write only session KML
    with open(KML_FILENAME, "w") as f:
        f.write("\n".join(kml_lines))
    print("Updated session KML:", KML_FILENAME)

def generate_kml_throttled():
    """Only regenerate KML if enough time has passed"""
    global last_kml_generation
    current_time = time.time()
    
    if current_time - last_kml_generation > KML_GENERATION_INTERVAL:
        generate_kml()
        last_kml_generation = current_time

def generate_cumulative_kml_throttled():
    """Only regenerate cumulative KML if enough time has passed"""
    global last_cumulative_kml_generation
    current_time = time.time()
    
    if current_time - last_cumulative_kml_generation > KML_GENERATION_INTERVAL:
        generate_cumulative_kml()
        last_cumulative_kml_generation = current_time

# New generate_cumulative_kml function
def generate_cumulative_kml():
    """
    Build cumulative KML by reading the cumulative CSV and grouping detections into flights.
    """
    # Check if cumulative CSV exists
    if not os.path.exists(CUMULATIVE_CSV_FILENAME):
        print(f"Warning: Cumulative CSV file {CUMULATIVE_CSV_FILENAME} does not exist yet.")
        return
    
    # Read cumulative CSV history
    history = []
    try:
        with open(CUMULATIVE_CSV_FILENAME, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                # Parse timestamp
                ts = datetime.fromisoformat(row['timestamp'])
                row['last_update'] = ts
                # Convert coordinates
                row['drone_lat'] = float(row['drone_lat']) if row['drone_lat'] else 0.0
                row['drone_long'] = float(row['drone_long']) if row['drone_long'] else 0.0
                row['pilot_lat'] = float(row['pilot_lat']) if row['pilot_lat'] else 0.0
                row['pilot_long'] = float(row['pilot_long']) if row['pilot_long'] else 0.0
                history.append(row)
    except Exception as e:
        print(f"Error reading cumulative CSV: {e}")
        return

    # Determine unique MACs and assign consistent colors
    macs = sorted({d['mac'] for d in history})
    mac_colors = {}
    for mac in macs:
        mac_colors[mac] = get_color_for_mac(mac)

    # Start KML
    kml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">',
        '<Document>',
        '<name>Cumulative Detections</name>'
    ]

    # For each MAC, group history into flights with staleThreshold
    for mac in macs:
        alias = ALIASES.get(mac, "")
        aliasStr = f"{alias} " if alias else ""
        color = mac_colors[mac]

        flight_idx = 1
        last_ts = None
        current_flight = []

        for det in history:
            if det.get('mac') != mac:
                continue
            lat = det['drone_lat']
            lon = det['drone_long']
            ts = det['last_update']
            if lat and lon:
                if last_ts and (ts - last_ts).total_seconds() > staleThreshold:
                    # flush flight
                    if current_flight:
                        # open folder
                        kml_lines.append('<Folder>')
                        # include start timestamp for this flight
                        start_dt  = current_flight[0][2]  # already a datetime
                        start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
                        kml_lines.append(f'<name>Flight {flight_idx} {aliasStr}{mac} ({start_str})</name>')
                        # drone path
                        coords = " ".join(f"{lo},{la},0" for lo, la, _ in current_flight)
                        kml_lines.append(f'<Placemark><Style><LineStyle><color>{color}</color><width>2</width></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>')
                        # drone start icon
                        start_lo, start_la, start_ts = current_flight[0]
                        kml_lines.append(f'<Placemark><name>Drone Start {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/airports.png</href></IconStyle></Style><Point><coordinates>{start_lo},{start_la},0</coordinates></Point></Placemark>')
                        # drone end icon
                        end_lo, end_la, end_ts = current_flight[-1]
                        kml_lines.append(f'<Placemark><name>Drone End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/heliport.png</href></IconStyle></Style><Point><coordinates>{end_lo},{end_la},0</coordinates></Point></Placemark>')
                        # pilot path
                        start_ts = current_flight[0][2]
                        pilot_pts = [(d['pilot_long'], d['pilot_lat']) for d in history if d.get('mac')==mac and d.get('pilot_lat') and d.get('pilot_long') and start_ts <= d['last_update'] <= end_ts]
                        if pilot_pts:
                            pc = " ".join(f"{plo},{pla},0" for plo, pla in pilot_pts)
                            kml_lines.append(f'<Placemark><name>Pilot Path {flight_idx} {aliasStr}{mac}</name><Style><LineStyle><color>{color}</color><width>2</width><gx:dash/></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{pc}</coordinates></LineString></Placemark>')
                            plon, plat = pilot_pts[-1]
                            kml_lines.append(f'<Placemark><name>Pilot End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/man.png</href></IconStyle></Style><Point><coordinates>{plon},{plat},0</coordinates></Point></Placemark>')
                        # close folder
                        kml_lines.append('</Folder>')
                        flight_idx += 1
                    current_flight = []
                # accumulate
                current_flight.append((lon, lat, ts))
                last_ts = ts

        # flush last flight
        if current_flight:
            kml_lines.append('<Folder>')
            # include start timestamp for this flight
            start_dt  = current_flight[0][2]  # already a datetime
            start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
            kml_lines.append(f'<name>Flight {flight_idx} {aliasStr}{mac} ({start_str})</name>')
            coords = " ".join(f"{lo},{la},0" for lo, la, _ in current_flight)
            kml_lines.append(f'<Placemark><Style><LineStyle><color>{color}</color><width>2</width></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>')
            # drone start icon
            start_lo, start_la, start_ts = current_flight[0]
            kml_lines.append(f'<Placemark><name>Drone Start {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/airports.png</href></IconStyle></Style><Point><coordinates>{start_lo},{start_la},0</coordinates></Point></Placemark>')
            end_lo, end_la, end_ts = current_flight[-1]
            kml_lines.append(f'<Placemark><name>Drone End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/heliport.png</href></IconStyle></Style><Point><coordinates>{end_lo},{end_la},0</coordinates></Point></Placemark>')
            start_ts = current_flight[0][2]
            pilot_pts = [(d['pilot_long'], d['pilot_lat']) for d in history if d.get('mac')==mac and d.get('pilot_lat') and d.get('pilot_long') and start_ts <= d['last_update'] <= end_ts]
            if pilot_pts:
                pc = " ".join(f"{plo},{pla},0" for plo, pla in pilot_pts)
                kml_lines.append(f'<Placemark><name>Pilot Path {flight_idx} {aliasStr}{mac}</name><Style><LineStyle><color>{color}</color><width>2</width><gx:dash/></LineStyle></Style><LineString><tessellate>1</tessellate><coordinates>{pc}</coordinates></LineString></Placemark>')
                plon, plat = pilot_pts[-1]
                kml_lines.append(f'<Placemark><name>Pilot End {flight_idx} {aliasStr}{mac}</name><Style><IconStyle><color>{color}</color><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/man.png</href></IconStyle></Style><Point><coordinates>{plon},{plat},0</coordinates></Point></Placemark>')
            kml_lines.append('</Folder>')

    # Close document
    kml_lines.append('</Document></kml>')

    # Write cumulative KML
    with open(CUMULATIVE_KML_FILENAME, "w") as f:
        f.write("\n".join(kml_lines))
    print("Updated cumulative KML:", CUMULATIVE_KML_FILENAME)


# Generate initial KML so the file exists from startup
generate_kml()
generate_cumulative_kml()


# ----------------------
# Detection Update & CSV Logging
# ----------------------
def update_detection(detection):
    mac = detection.get("mac")
    if not mac:
        return
    prev = tracked_pairs.get(mac)

    # Retrieve new drone coordinates from the detection
    new_drone_lat = detection.get("drone_lat", 0)
    new_drone_long = detection.get("drone_long", 0)
    valid_drone = (new_drone_lat != 0 and new_drone_long != 0)

    if not valid_drone:
        print(f"No-GPS detection for {mac}; forwarding for processing.")
        # Set last_update for no-GPS detections so they can be tracked for timeout
        detection["last_update"] = time.time()
        # Mark as active since this is a fresh detection
        detection["status"] = "active"
        
        # Preserve previous basic_id if new detection lacks one (same logic as GPS section)
        if not detection.get("basic_id") and mac in tracked_pairs and tracked_pairs[mac].get("basic_id"):
            detection["basic_id"] = tracked_pairs[mac]["basic_id"]
        
        # Comprehensive FAA data persistence logic for no-GPS detections
        remote_id = detection.get("basic_id")
        if mac:
            # Exact match if basic_id provided
            if remote_id:
                key = (mac, remote_id)
                if key in FAA_CACHE:
                    detection["faa_data"] = FAA_CACHE[key]
            # Fallback: any cached FAA data for this mac (regardless of basic_id)
            if "faa_data" not in detection:
                for (c_mac, _), faa_data in FAA_CACHE.items():
                    if c_mac == mac:
                        detection["faa_data"] = faa_data
                        break
            # Fallback: last known FAA data in tracked_pairs
            if "faa_data" not in detection and mac in tracked_pairs and "faa_data" in tracked_pairs[mac]:
                detection["faa_data"] = tracked_pairs[mac]["faa_data"]
            # Always cache FAA data by MAC and current basic_id for future lookups
            if "faa_data" in detection:
                write_to_faa_cache(mac, detection.get("basic_id", ""), detection["faa_data"])
        
        # Forward this no-GPS detection to the client
        tracked_pairs[mac] = detection
        
        # Log no-GPS detection as incident
        log_incident({
            "type": "detection",
            "timestamp": datetime.now().isoformat(),
            "mac": mac,
            "alias": ALIASES.get(mac, ""),
            "drone_lat": 0,
            "drone_long": 0,
            "drone_altitude": 0,
            "pilot_lat": detection.get("pilot_lat", 0),
            "pilot_long": detection.get("pilot_long", 0),
            "basic_id": detection.get("basic_id", ""),
            "rssi": detection.get("rssi", 0),
            "no_gps": True
        })
        
        detection_history.append(detection.copy())
        
        # Backend webhook logic for all detections (GPS and no-GPS) - enabled
        should_trigger, is_new = should_trigger_webhook_earliest(detection, mac)
        if should_trigger:
            trigger_backend_webhook_earliest(detection, is_new)
        
        # Write to session CSV even for no-GPS
        with open(CSV_FILENAME, mode='a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=[
                'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
                'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
            ])
            writer.writerow({
                'timestamp': datetime.now().isoformat(),
                'alias': ALIASES.get(mac, ''),
                'mac': mac,
                'rssi': detection.get('rssi', ''),
                'drone_lat': new_drone_lat,
                'drone_long': new_drone_long,
                'drone_altitude': detection.get('drone_altitude', ''),
                'pilot_lat': detection.get('pilot_lat', ''),
                'pilot_long': detection.get('pilot_long', ''),
                'basic_id': detection.get('basic_id', ''),
                'faa_data': json.dumps(detection.get('faa_data', {}))
            })

        # Append to cumulative CSV for no-GPS
        with open(CUMULATIVE_CSV_FILENAME, mode='a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=[
                'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
                'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
            ])
            writer.writerow({
                'timestamp': datetime.now().isoformat(),
                'alias': ALIASES.get(mac, ''),
                'mac': mac,
                'rssi': detection.get('rssi', ''),
                'drone_lat': new_drone_lat,
                'drone_long': new_drone_long,
                'drone_altitude': detection.get('drone_altitude', ''),
                'pilot_lat': detection.get('pilot_lat', ''),
                'pilot_long': detection.get('pilot_long', ''),
                'basic_id': detection.get('basic_id', ''),
                'faa_data': json.dumps(detection.get('faa_data', {}))
            })
        # Regenerate full cumulative KML
        generate_cumulative_kml_throttled()
        generate_kml_throttled()
        
        # Reduce WebSocket emissions - only emit detection, not all data types
        try:
            socketio.emit('detection', detection, )
        except Exception:
            pass
        
        # Cache FAA data even for no-GPS
        if detection.get('basic_id'):
            write_to_faa_cache(mac, detection['basic_id'], detection.get('faa_data', {}))
        return

    # Otherwise, use the provided non-zero coordinates.
    detection["drone_lat"] = new_drone_lat
    detection["drone_long"] = new_drone_long
    detection["drone_altitude"] = detection.get("drone_altitude", 0)
    detection["pilot_lat"] = detection.get("pilot_lat", 0)
    detection["pilot_long"] = detection.get("pilot_long", 0)
    detection["last_update"] = time.time()
    # Mark as active since this is a fresh detection
    detection["status"] = "active"

    # Preserve previous basic_id if new detection lacks one
    if not detection.get("basic_id") and mac in tracked_pairs and tracked_pairs[mac].get("basic_id"):
        detection["basic_id"] = tracked_pairs[mac]["basic_id"]
    remote_id = detection.get("basic_id")
    # Try exact cache lookup by (mac, remote_id), then fallback to any cached data for this mac, then to previous tracked_pairs entry
    if mac:
        # Exact match if basic_id provided
        if remote_id:
            key = (mac, remote_id)
            if key in FAA_CACHE:
                detection["faa_data"] = FAA_CACHE[key]
        # Fallback: any cached FAA data for this mac
        if "faa_data" not in detection:
            for (c_mac, _), faa_data in FAA_CACHE.items():
                if c_mac == mac:
                    detection["faa_data"] = faa_data
                    break
        # Fallback: last known FAA data in tracked_pairs
        if "faa_data" not in detection and mac in tracked_pairs and "faa_data" in tracked_pairs[mac]:
            detection["faa_data"] = tracked_pairs[mac]["faa_data"]
        # Always cache FAA data by MAC and current basic_id for fallback
        if "faa_data" in detection:
            write_to_faa_cache(mac, detection.get("basic_id", ""), detection["faa_data"])

    tracked_pairs[mac] = detection
    
    # Save to database
    try:
        detection_db = detection.copy()
        detection_db['alias'] = ALIASES.get(mac, '')
        detection_db['timestamp'] = time.time()
        save_detection_to_db(detection_db)
    except Exception as e:
        logger.debug(f"Error saving detection to database: {e}")
    
    # Check for zone entry/exit events
    check_zone_events(detection)
    
    # Log all detections as incidents
    if valid_drone:
        log_incident({
            "type": "detection",
            "timestamp": datetime.now().isoformat(),
            "mac": mac,
            "alias": ALIASES.get(mac, ""),
            "drone_lat": new_drone_lat,
            "drone_long": new_drone_long,
            "drone_altitude": detection.get("drone_altitude", 0),
            "pilot_lat": detection.get("pilot_lat", 0),
            "pilot_long": detection.get("pilot_long", 0),
            "basic_id": detection.get("basic_id", ""),
            "rssi": detection.get("rssi", 0),
            "faa_data": detection.get("faa_data", {})
        })
    
    # Backend webhook logic for GPS detections - enabled
    should_trigger, is_new = should_trigger_webhook_earliest(detection, mac)
    if should_trigger:
        trigger_backend_webhook_earliest(detection, is_new)
    
    # Broadcast this detection to all connected clients and peer servers
    try:
        socketio.emit('detection', detection, )
    except Exception:
        pass
    detection_history.append(detection.copy())
    print("Updated tracked_pairs:", tracked_pairs)
    with open(CSV_FILENAME, mode='a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[
            'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
            'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
        ])
        writer.writerow({
            'timestamp': datetime.now().isoformat(),
            'alias': ALIASES.get(mac, ''),
            'mac': mac,
            'rssi': detection.get('rssi', ''),
            'drone_lat': detection.get('drone_lat', ''),
            'drone_long': detection.get('drone_long', ''),
            'drone_altitude': detection.get('drone_altitude', ''),
            'pilot_lat': detection.get('pilot_lat', ''),
            'pilot_long': detection.get('pilot_long', ''),
            'basic_id': detection.get('basic_id', ''),
            'faa_data': json.dumps(detection.get('faa_data', {}))
        })
    # Append to cumulative CSV
    with open(CUMULATIVE_CSV_FILENAME, mode='a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[
            'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
            'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
        ])
        writer.writerow({
            'timestamp': datetime.now().isoformat(),
            'alias': ALIASES.get(mac, ''),
            'mac': mac,
            'rssi': detection.get('rssi', ''),
            'drone_lat': detection.get('drone_lat', ''),
            'drone_long': detection.get('drone_long', ''),
            'drone_altitude': detection.get('drone_altitude', ''),
            'pilot_lat': detection.get('pilot_lat', ''),
            'pilot_long': detection.get('pilot_long', ''),
            'basic_id': detection.get('basic_id', ''),
            'faa_data': json.dumps(detection.get('faa_data', {}))
        })
    # Regenerate full cumulative KML
    generate_cumulative_kml_throttled()
    generate_kml_throttled()
    
    # Emit real-time updates via WebSocket (if available in this context)
    try:
        emit_detections()
        emit_paths()
        emit_cumulative_log()
        emit_faa_cache()
    except NameError:
        # Emit functions not available in this thread context
        pass
    except Exception as e:
        # Handle JSON serialization errors gracefully
        logger.debug(f"WebSocket emit error: {e}")
        pass

# ----------------------
# Global Follow Lock & Color Overrides
# ----------------------
followLock = {"type": None, "id": None, "enabled": False}
colorOverrides = {}

# Backend webhook tracking variables
backend_seen_drones = set()
backend_previous_active = {}
backend_alerted_no_gps = set()

# ----------------------
# Webhook Functions (EARLY DEFINITION - must be before update_detection)
# ----------------------

def should_trigger_webhook_earliest(detection, mac):
    """
    Determine if a webhook should be triggered based on the same logic as frontend popups.
    Returns (should_trigger, is_new_detection)
    """
    global backend_seen_drones, backend_previous_active, backend_alerted_no_gps
    
    current_time = time.time()
    
    # Debug logging
    logging.debug(f"Webhook check for {mac}: detection={detection}")
    logging.debug(f"Webhook check: current_time={current_time}, last_update={detection.get('last_update')}")
    
    # Check if detection is within stale threshold (30 seconds)
    if not detection.get('last_update') or (current_time - detection['last_update'] > 30):
        logging.debug(f"Webhook check for {mac}: FAILED stale check - last_update={detection.get('last_update')}")
        return False, False
    
    # GPS drone logic
    drone_lat = detection.get('drone_lat', 0)
    drone_long = detection.get('drone_long', 0)
    pilot_lat = detection.get('pilot_lat', 0) 
    pilot_long = detection.get('pilot_long', 0)
    
    valid_drone = (drone_lat != 0 and drone_long != 0)
    has_gps = valid_drone or (pilot_lat != 0 and pilot_long != 0)
    has_recent_transmission = detection.get('last_update') and (current_time - detection['last_update'] <= 5)
    is_no_gps_drone = not has_gps and has_recent_transmission
    
    # Calculate state
    active_now = valid_drone and detection.get('last_update') and (current_time - detection['last_update'] <= 30)
    was_active = backend_previous_active.get(mac, False)
    is_new = mac not in backend_seen_drones
    
    logging.debug(f"Webhook check for {mac}: valid_drone={valid_drone}, active_now={active_now}, was_active={was_active}, is_new={is_new}")
    
    should_trigger = False
    popup_is_new = False
    
    # GPS drone webhook logic - trigger on transition from inactive to active
    if not was_active and active_now:
        should_trigger = True
        alias = ALIASES.get(mac)
        popup_is_new = not alias and is_new
        logging.info(f"Webhook trigger for {mac}: GPS drone transition to active")
    
    # No-GPS drone webhook logic - trigger once per detection session
    elif is_no_gps_drone and mac not in backend_alerted_no_gps:
        should_trigger = True
        popup_is_new = True
        backend_alerted_no_gps.add(mac)
        logging.info(f"Webhook trigger for {mac}: No-GPS drone detected")
    
    logging.debug(f"Webhook check for {mac}: should_trigger={should_trigger}, popup_is_new={popup_is_new}")
    
    # Update tracking state
    if should_trigger:
        backend_seen_drones.add(mac)
    backend_previous_active[mac] = active_now
    
    # Clean up no-GPS alerts when transmission stops
    if not has_recent_transmission:
        backend_alerted_no_gps.discard(mac)
    
    return should_trigger, popup_is_new

def trigger_backend_webhook_earliest(detection, is_new_detection):
    """
    Send webhook with same payload format as frontend popups
    """
    logging.info(f"Backend webhook called for {detection.get('mac')} - WEBHOOK_URL: {WEBHOOK_URL}")
    
    if not WEBHOOK_URL or not WEBHOOK_URL.startswith("http"):
        logging.warning(f"Backend webhook skipped - invalid URL: {WEBHOOK_URL}")
        return
    
    try:
        mac = detection.get('mac')
        alias = ALIASES.get(mac) if mac else None
        
        # Determine header message (same logic as frontend)
        if not detection.get('drone_lat') or not detection.get('drone_long') or detection.get('drone_lat') == 0 or detection.get('drone_long') == 0:
            header = 'Drone with no GPS lock detected'
        elif alias:
            header = f'Known drone detected – {alias}'
        else:
            header = 'New drone detected' if is_new_detection else 'Previously seen non-aliased drone detected'
        
        logging.info(f"Backend webhook for {mac}: {header}")
        
        # Build payload (same format as frontend)
        payload = {
            'alert': header,
            'mac': mac,
            'basic_id': detection.get('basic_id'),
            'alias': alias,
            'drone_lat': detection.get('drone_lat') if detection.get('drone_lat') != 0 else None,
            'drone_long': detection.get('drone_long') if detection.get('drone_long') != 0 else None,
            'pilot_lat': detection.get('pilot_lat') if detection.get('pilot_lat') != 0 else None,
            'pilot_long': detection.get('pilot_long') if detection.get('pilot_long') != 0 else None,
            'faa_data': None,  # Will be populated below
            'drone_gmap': None,
            'pilot_gmap': None,
            'isNew': is_new_detection
        }
        
        # Add FAA data if available
        faa_data = detection.get('faa_data')
        if faa_data and isinstance(faa_data, dict) and faa_data.get('data') and isinstance(faa_data['data'].get('items'), list) and len(faa_data['data']['items']) > 0:
            payload['faa_data'] = faa_data['data']['items'][0]
        
        # Add Google Maps links
        if payload['drone_lat'] and payload['drone_long']:
            payload['drone_gmap'] = f"https://www.google.com/maps?q={payload['drone_lat']},{payload['drone_long']}"
        if payload['pilot_lat'] and payload['pilot_long']:
            payload['pilot_gmap'] = f"https://www.google.com/maps?q={payload['pilot_lat']},{payload['pilot_long']}"
        
        # Send webhook
        logging.info(f"Sending webhook to {WEBHOOK_URL} with payload: {payload}")
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        logging.info(f"Backend webhook sent for {mac}: {response.status_code}")
        
    except requests.exceptions.Timeout:
        logging.error(f"Backend webhook timeout for {detection.get('mac', 'unknown')}: URL {WEBHOOK_URL} timed out after 10 seconds")
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Backend webhook connection error for {detection.get('mac', 'unknown')}: Unable to reach {WEBHOOK_URL} - {e}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Backend webhook request error for {detection.get('mac', 'unknown')}: {e}")
    except Exception as e:
        logging.error(f"Backend webhook error for {detection.get('mac', 'unknown')}: {e}")


# ----------------------
# FAA Query Helper Functions
# ----------------------
def create_retry_session(retries=3, backoff_factor=2, status_forcelist=(502, 503, 504)):
    logging.debug("Creating retry-enabled session with custom headers for FAA query.")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://uasdoc.faa.gov/listdocs",
        "client": "external"
    })
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session

def refresh_cookie(session):
    homepage_url = "https://uasdoc.faa.gov/listdocs"
    logging.debug("Refreshing FAA cookie by requesting homepage: %s", homepage_url)
    try:
        response = session.get(homepage_url, timeout=30)
        logging.debug("FAA homepage response code: %s", response.status_code)
    except requests.exceptions.RequestException as e:
        logging.exception("Error refreshing FAA cookie: %s", e)

def query_remote_id(session, remote_id):
    endpoint = "https://uasdoc.faa.gov/api/v1/serialNumbers"
    params = {
        "itemsPerPage": 8,
        "pageIndex": 0,
        "orderBy[0]": "updatedAt",
        "orderBy[1]": "DESC",
        "findBy": "serialNumber",
        "serialNumber": remote_id
    }
    logging.debug("Querying FAA API endpoint: %s with params: %s", endpoint, params)
    try:
        response = session.get(endpoint, params=params, timeout=30)
        logging.debug("FAA Request URL: %s", response.url)
        if response.status_code != 200:
            logging.error("FAA HTTP error: %s - %s", response.status_code, response.reason)
            return None
        return response.json()
    except Exception as e:
        logging.exception("Error querying FAA API: %s", e)
        return None

# ----------------------
# Webhook popup API Endpoint 
# ----------------------
@app.route('/api/webhook_popup', methods=['POST'])
def webhook_popup():
    data = request.get_json()
    webhook_url = data.get("webhook_url")
    if not webhook_url:
        return jsonify({"status": "error", "reason": "No webhook URL provided"}), 400
    try:
        clean_data = data.get("payload", {})
        response = requests.post(webhook_url, json=clean_data, timeout=10)
        return jsonify({"status": "ok", "response": response.status_code}), 200
    except requests.exceptions.Timeout:
        logging.error(f"Webhook timeout for URL: {webhook_url}")
        return jsonify({"status": "error", "message": "Webhook request timed out after 10 seconds"}), 408
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Webhook connection error for URL {webhook_url}: {e}")
        return jsonify({"status": "error", "message": f"Connection error: Unable to reach webhook URL"}), 503
    except requests.exceptions.RequestException as e:
        logging.error(f"Webhook request error for URL {webhook_url}: {e}")
        return jsonify({"status": "error", "message": f"Request error: {str(e)}"}), 500
    except Exception as e:
        logging.error(f"Webhook send error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------
# New FAA Query API Endpoint
# ----------------------
@app.route('/api/query_faa', methods=['POST'])
def api_query_faa(): 
    data = request.get_json()
    mac = data.get("mac")
    remote_id = data.get("remote_id")
    if not mac or not remote_id:
        return jsonify({"status": "error", "message": "Missing mac or remote_id"}), 400
    session = create_retry_session()
    refresh_cookie(session)
    faa_result = query_remote_id(session, remote_id)
    # Fallback: if FAA API query failed or returned no records, try cached FAA data by MAC
    if not faa_result or not faa_result.get("data", {}).get("items"):
        for (c_mac, _), cached_data in FAA_CACHE.items():
            if c_mac == mac:
                faa_result = cached_data
                break
    if faa_result is None:
        return jsonify({"status": "error", "message": "FAA query failed"}), 500
    if mac in tracked_pairs:
        tracked_pairs[mac]["faa_data"] = faa_result
    else:
        tracked_pairs[mac] = {"basic_id": remote_id, "faa_data": faa_result}
    write_to_faa_cache(mac, remote_id, faa_result)
    timestamp = datetime.now().isoformat()
    try:
        with open(FAA_LOG_FILENAME, "a", newline='') as csvfile:
            fieldnames = ["timestamp", "mac", "remote_id", "faa_response"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writerow({
                "timestamp": timestamp,
                "mac": mac,
                "remote_id": remote_id,
                "faa_response": json.dumps(faa_result)
            })
    except Exception as e:
        print("Error writing to FAA log CSV:", e)
    generate_kml()
    return jsonify({"status": "ok", "faa_data": faa_result})

# ----------------------
# FAA Data GET API Endpoint (by MAC or basic_id)
# ----------------------

@app.route('/api/faa/<identifier>', methods=['GET'])
def api_get_faa(identifier):
    """
    Retrieve cached FAA data by MAC address or by basic_id (remote ID).
    """
    # First try lookup by MAC
    if identifier in tracked_pairs and 'faa_data' in tracked_pairs[identifier]:
        return jsonify({'status': 'ok', 'faa_data': tracked_pairs[identifier]['faa_data']})
    # Then try lookup by basic_id
    for mac, det in tracked_pairs.items():
        if det.get('basic_id') == identifier and 'faa_data' in det:
            return jsonify({'status': 'ok', 'faa_data': det['faa_data']})
    # Fallback: search cached FAA data by remote_id first, then by MAC
    for (c_mac, c_rid), faa_data in     FAA_CACHE.items():
        if c_rid == identifier:
            return jsonify({'status': 'ok', 'faa_data': faa_data})
    for (c_mac, c_rid), faa_data in FAA_CACHE.items():
        if c_mac == identifier:
            return jsonify({'status': 'ok', 'faa_data': faa_data})
    return jsonify({'status': 'error', 'message': 'No FAA data found for this identifier'}), 404



# ----------------------


# ----------------------
# HTML & JS (UI) Section
# ----------------------
# Updated: The selection page now has three dropdowns.
PORT_SELECTION_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>System Configuration - Port Selection</title>
  <style>
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      padding: 20px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif;
      background-color: #1a1a1a;
      color: #e0e0e0;
      text-align: center;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
    }
    .container {
      background: rgba(26, 26, 26, 0.95);
      border: 1px solid #4a4a4a;
      border-radius: 4px;
      padding: 30px;
      max-width: 600px;
      width: 100%;
      box-shadow: 0 4px 16px rgba(0,0,0,0.6);
    }
    h1 {
      font-size: 1.5em;
      font-weight: 600;
      margin: 0 0 8px 0;
      color: #ffb347;
      text-transform: uppercase;
      letter-spacing: 1px;
      border-bottom: 1px solid #4a4a4a;
      padding-bottom: 12px;
      margin-bottom: 24px;
    }
    .subtitle {
      font-size: 0.9em;
      color: #9a9a9a;
      margin-bottom: 24px;
      font-weight: 400;
    }
    form {
      text-align: left;
    }
    .form-group {
      margin-bottom: 20px;
    }
    label {
      display: block;
      font-size: 0.9em;
      font-weight: 600;
      color: #e0e0e0;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    select {
      width: 100%;
      background-color: #2a2a2a;
      color: #e0e0e0;
      border: 1px solid #4a4a4a;
      padding: 10px;
      font-size: 0.9em;
      border-radius: 3px;
      transition: all 0.2s;
    }
    select:hover {
      border-color: #6a6a6a;
    }
    select:focus {
      outline: none;
      border-color: #ffb347;
    }
    .webhook-section {
      margin-top: 30px;
      padding-top: 24px;
      border-top: 1px solid #4a4a4a;
    }
    .webhook-section label {
      color: #ffb347;
    }
    input[type="text"] {
      width: 100%;
      background-color: #2a2a2a;
      color: #e0e0e0;
      border: 1px solid #4a4a4a;
      padding: 10px;
      font-size: 0.9em;
      border-radius: 3px;
      margin-bottom: 12px;
      transition: all 0.2s;
    }
    input[type="text"]:focus {
      outline: none;
      border-color: #ffb347;
    }
    input[type="text"]::placeholder {
      color: #6a6a6a;
    }
    button {
      padding: 10px 20px;
      font-size: 0.9em;
      border: 1px solid #4a4a4a;
      background-color: #2a2a2a;
      color: #e0e0e0;
      cursor: pointer;
      border-radius: 3px;
      transition: all 0.2s;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    button:hover {
      background-color: #3a3a3a;
      border-color: #6a6a6a;
    }
    button:focus {
      outline: none;
      border-color: #ffb347;
    }
    #updateWebhookButton {
      width: 100%;
      margin-bottom: 20px;
    }
    #beginMapping {
      display: block;
      width: 100%;
      margin: 24px auto 0;
      padding: 12px 24px;
      font-size: 1em;
      background-color: #4a9eff;
      border: 1px solid #4a9eff;
      color: #ffffff;
      font-weight: 600;
    }
    #beginMapping:hover {
      background-color: #5aaeff;
      border-color: #5aaeff;
    }
    pre.logo-art, pre.ascii-art {
      display: none;
    }
    .status-message {
      margin-top: 16px;
      padding: 8px;
      border-radius: 3px;
      font-size: 0.85em;
      display: none;
    }
    .status-message.success {
      background-color: rgba(74, 158, 255, 0.2);
      border: 1px solid #4a9eff;
      color: #4a9eff;
      display: block;
    }
    .status-message.error {
      background-color: rgba(255, 68, 68, 0.2);
      border: 1px solid #ff4444;
      color: #ff4444;
      display: block;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>System Configuration</h1>
    <div class="subtitle">Select USB Serial Ports for Detection Units</div>
    <form method="POST" action="/select_ports">
      <div class="form-group">
        <label for="port1">Detection Unit 1</label>
        <select id="port1" name="port1">
          <option value="">-- None Selected --</option>
          {% for port in ports %}
            <option value="{{ port.device }}">{{ port.device }} - {{ port.description }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="form-group">
        <label for="port2">Detection Unit 2</label>
        <select id="port2" name="port2">
          <option value="">-- None Selected --</option>
          {% for port in ports %}
            <option value="{{ port.device }}">{{ port.device }} - {{ port.description }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="form-group">
        <label for="port3">Detection Unit 3</label>
        <select id="port3" name="port3">
          <option value="">-- None Selected --</option>
          {% for port in ports %}
            <option value="{{ port.device }}">{{ port.device }} - {{ port.description }}</option>
          {% endfor %}
        </select>
      </div>
      
      <div class="webhook-section">
        <label for="webhookUrl">Webhook URL (Backend Integration)</label>
        <input type="text" id="webhookUrl" placeholder="https://example.com/webhook" />
        <button id="updateWebhookButton" type="button">Update Webhook</button>
        <div id="webhookStatus" class="status-message"></div>
      </div>
      
      <div class="webhook-section">
        <label for="audioAlertStyle">Audio Alert Style</label>
        <select id="audioAlertStyle">
          <option value="soft">Soft Tones (Default)</option>
          <option value="eas">EAS Alert System</option>
          <option value="siren">Siren</option>
          <option value="pulse">Pulse Alert</option>
        </select>
        <button id="testAudioStyle" type="button" style="margin-top: 8px;">Test Audio Style</button>
        <div id="audioTestStatus" class="status-message"></div>
      </div>
      
      <button id="beginMapping" type="submit">Initialize System</button>
    </form>
  </div>
  <script>
    function refreshPortOptions() {
      fetch('/api/ports')
        .then(res => res.json())
        .then(data => {
          ['port1','port2','port3'].forEach(name => {
            const select = document.getElementById(name);
            if (!select) return;
            const current = select.value;
            select.innerHTML = '<option value="">--None--</option>' +
              data.ports.map(p => `<option value="${p.device}">${p.device} - ${p.description}</option>`).join('');
            select.value = current;
          });
        })
        .catch(err => console.error('Error refreshing ports:', err));
    }

    function loadSelectedPorts() {
      fetch('/api/selected_ports')
        .then(res => res.json())
        .then(data => {
          const selectedPorts = data.selected_ports || {};
          // Populate dropdowns with currently selected ports
          ['port1', 'port2', 'port3'].forEach(name => {
            const select = document.getElementById(name);
            if (select && selectedPorts[name]) {
              select.value = selectedPorts[name];
            }
          });
        })
        .catch(err => console.error('Error loading selected ports:', err));
    }

    var refreshInterval = setInterval(refreshPortOptions, 2000);
    ['port1','port2','port3'].forEach(function(name) {
      var select = document.getElementById(name);
      if (select) {
        ['focus', 'mousedown'].forEach(function(evt) {
          select.addEventListener(evt, function() { clearInterval(refreshInterval); });
        });
        select.addEventListener('change', function() { clearInterval(refreshInterval); });
      }
    });
    window.onload = function() {
      refreshPortOptions();
      // Load currently selected ports after refreshing port options
      setTimeout(loadSelectedPorts, 100);
    }
    const webhookInput = document.getElementById('webhookUrl');
    
    // Load current webhook URL and audio settings from backend on page load
    loadCurrentWebhookUrl();
    loadAudioSettings();
    
    async function loadCurrentWebhookUrl() {
      try {
        const response = await fetch('/api/get_webhook_url');
        const result = await response.json();
        console.log('Webhook URL load result:', result);
        if (result.status === 'ok') {
          document.getElementById('webhookUrl').value = result.webhook_url || '';
          console.log('Webhook URL loaded:', result.webhook_url || '(empty)');
        } else {
          console.warn('Failed to load webhook URL:', result.message);
        }
      } catch (e) {
        console.warn('Could not load webhook URL:', e);
      }
    }
    
    function loadAudioSettings() {
      const savedStyle = localStorage.getItem('audioAlertStyle') || 'soft';
      const styleSelect = document.getElementById('audioAlertStyle');
      if (styleSelect) {
        styleSelect.value = savedStyle;
      }
    }
    
    // Save audio alert style when changed
    const audioStyleSelect = document.getElementById('audioAlertStyle');
    if (audioStyleSelect) {
      audioStyleSelect.addEventListener('change', function() {
        localStorage.setItem('audioAlertStyle', this.value);
      });
    }
    
    // Test audio style button
    const testAudioButton = document.getElementById('testAudioStyle');
    if (testAudioButton) {
      testAudioButton.addEventListener('click', function() {
        const style = audioStyleSelect.value;
        const statusDiv = document.getElementById('audioTestStatus');
        statusDiv.className = 'status-message success';
        statusDiv.textContent = 'Playing test alert...';
        
        // Create a test audio context and play the selected style
        testAudioAlertStyle(style);
        
        setTimeout(() => {
          statusDiv.className = 'status-message';
          statusDiv.textContent = '';
        }, 2000);
      });
    }
    
    function testAudioAlertStyle(style) {
      try {
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        
        if (style === 'eas') {
          // EAS Alert System - three-tone pattern
          playEASTone(audioContext, 0);
          setTimeout(() => playEASTone(audioContext, 0.5), 1000);
          setTimeout(() => playEASTone(audioContext, 1.0), 2000);
        } else if (style === 'siren') {
          // Siren - oscillating frequency
          playSiren(audioContext, 2.0);
        } else if (style === 'pulse') {
          // Pulse - repeating beeps
          playPulseAlert(audioContext, 3);
        } else {
          // Soft tones (default)
          playSoftTone(audioContext, 800, 0.3);
        }
      } catch (e) {
        console.error('Audio test failed:', e);
      }
    }
    
    function playEASTone(audioContext, startTime) {
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);
      
      oscillator.frequency.value = 853;
      oscillator.type = 'sine';
      
      const duration = 0.25;
      gainNode.gain.setValueAtTime(0, audioContext.currentTime + startTime);
      gainNode.gain.linearRampToValueAtTime(0.5, audioContext.currentTime + startTime + 0.01);
      gainNode.gain.linearRampToValueAtTime(0, audioContext.currentTime + startTime + duration);
      
      oscillator.start(audioContext.currentTime + startTime);
      oscillator.stop(audioContext.currentTime + startTime + duration);
    }
    
    function playSiren(audioContext, duration) {
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);
      
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(800, audioContext.currentTime);
      oscillator.frequency.exponentialRampToValueAtTime(1200, audioContext.currentTime + duration / 2);
      oscillator.frequency.exponentialRampToValueAtTime(800, audioContext.currentTime + duration);
      
      gainNode.gain.setValueAtTime(0, audioContext.currentTime);
      gainNode.gain.linearRampToValueAtTime(0.4, audioContext.currentTime + 0.1);
      gainNode.gain.linearRampToValueAtTime(0, audioContext.currentTime + duration);
      
      oscillator.start(audioContext.currentTime);
      oscillator.stop(audioContext.currentTime + duration);
    }
    
    function playPulseAlert(audioContext, count) {
      for (let i = 0; i < count; i++) {
        setTimeout(() => {
          playSoftTone(audioContext, 1000, 0.15);
        }, i * 300);
      }
    }
    
    function playSoftTone(audioContext, frequency, duration) {
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);
      
      oscillator.frequency.value = frequency;
      oscillator.type = 'sine';
      
      gainNode.gain.setValueAtTime(0, audioContext.currentTime);
      gainNode.gain.linearRampToValueAtTime(0.3, audioContext.currentTime + 0.01);
      gainNode.gain.linearRampToValueAtTime(0, audioContext.currentTime + duration);
      
      oscillator.start(audioContext.currentTime);
      oscillator.stop(audioContext.currentTime + duration);
    }
    
    document.getElementById('updateWebhookButton').addEventListener('click', async function(e) {
      e.preventDefault();
      const url = document.getElementById('webhookUrl').value.trim();
      const button = this;
      
      try {
        // Send webhook URL update via API
        const response = await fetch('/api/set_webhook_url', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ webhook_url: url })
        });
        
        const result = await response.json();
        
        const statusDiv = document.getElementById('webhookStatus');
        if (result.status === 'ok') {
          // Show success message
          statusDiv.className = 'status-message success';
          statusDiv.textContent = 'Webhook URL updated successfully';
          
          // Also update the hidden input for when Begin Mapping is clicked
          let webhookInput = document.getElementById('hiddenWebhookUrl');
          if (!webhookInput) {
            webhookInput = document.createElement('input');
            webhookInput.type = 'hidden';
            webhookInput.id = 'hiddenWebhookUrl';
            webhookInput.name = 'webhook_url';
            document.querySelector('form').appendChild(webhookInput);
          }
          webhookInput.value = url;
          
          // Hide message after 3 seconds
          setTimeout(() => {
            statusDiv.className = 'status-message';
            statusDiv.textContent = '';
          }, 3000);
          
        } else {
          console.error('Error updating webhook:', result.message);
          statusDiv.className = 'status-message error';
          statusDiv.textContent = 'Error: ' + (result.message || 'Failed to update webhook');
          
          setTimeout(() => {
            statusDiv.className = 'status-message';
            statusDiv.textContent = '';
          }, 5000);
        }
      } catch (error) {
        console.error('Error updating webhook:', error);
        const statusDiv = document.getElementById('webhookStatus');
        statusDiv.className = 'status-message error';
        statusDiv.textContent = 'Error: Connection failed';
        
        setTimeout(() => {
          statusDiv.className = 'status-message';
          statusDiv.textContent = '';
        }, 5000);
      }
    });

    // Ensure webhook URL is included when Begin Mapping form is submitted
    document.getElementById('beginMapping').addEventListener('click', function(e) {
      const url = document.getElementById('webhookUrl').value.trim();
      
      // Add webhook URL to the form as a hidden input
      const form = document.querySelector('form');
      let webhookInput = document.getElementById('hiddenWebhookUrl');
      if (!webhookInput) {
        webhookInput = document.createElement('input');
        webhookInput.type = 'hidden';
        webhookInput.id = 'hiddenWebhookUrl';
        webhookInput.name = 'webhook_url';
        form.appendChild(webhookInput);
      }
      webhookInput.value = url;
      
      // Let the form submit normally
    });
  // Zone Management Functions
  let zoneLayers = {};
  let drawingZone = false;
  let currentZonePolygon = null;
  let zoneDrawPoints = [];
  let zonesVisible = true;  // Default to showing zones

  function openZonesPanel() {
    document.getElementById('zonesModal').style.display = 'block';
    loadZones();
  }

  function closeZonesPanel() {
    document.getElementById('zonesModal').style.display = 'none';
    if (drawingZone) {
      cancelZoneDrawing();
    }
  }

  function loadZones() {
    fetch('/api/zones')
      .then(res => res.json())
      .then(data => {
        // Update zones list in panel if panel exists
        const zonesList = document.getElementById('zonesList');
        if (zonesList) {
          zonesList.innerHTML = '';
          
          if (data.zones.length === 0) {
            zonesList.innerHTML = '<div style="text-align:center; color:#9a9a9a; padding:20px;">No zones defined</div>';
          } else {
            data.zones.forEach(zone => {
              const zoneDiv = document.createElement('div');
              zoneDiv.style.cssText = 'border:1px solid #4a4a4a; background:#2a2a2a; padding:12px; margin-bottom:10px; border-radius:3px;';
              zoneDiv.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <div>
                    <strong style="color:#e0e0e0;">${zone.name || 'Unnamed Zone'}</strong>
                    <span style="color:#9a9a9a; font-size:0.85em; margin-left:10px;">${zone.type || 'warning'}</span>
                    ${zone.enabled ? '<span style="color:#4a9eff; font-size:0.8em; margin-left:10px;">ENABLED</span>' : '<span style="color:#9a9a9a; font-size:0.8em; margin-left:10px;">DISABLED</span>'}
                  </div>
                  <div>
                    <button onclick="toggleZone('${zone.id}')" style="margin-right:5px; padding:4px 8px; background:#2a2a2a; border:1px solid #4a4a4a; color:#e0e0e0; border-radius:3px; cursor:pointer; font-size:0.8em;">${zone.enabled ? 'Disable' : 'Enable'}</button>
                    <button onclick="deleteZone('${zone.id}')" style="padding:4px 8px; background:#ff4444; border:1px solid #ff4444; color:#fff; border-radius:3px; cursor:pointer; font-size:0.8em;">Delete</button>
                  </div>
                </div>
              `;
              zonesList.appendChild(zoneDiv);
            });
          }
        }
        
        // Always draw zones on map (even if panel isn't open)
        drawZonesOnMap(data.zones);
        
        // Update toggle button state
        const toggleBtn = document.getElementById('toggleZonesButton');
        if (toggleBtn) {
          toggleBtn.textContent = zonesVisible ? 'Hide Zones' : 'Show Zones';
          toggleBtn.style.backgroundColor = zonesVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
          toggleBtn.style.color = zonesVisible ? 'var(--color-bg)' : 'var(--color-text)';
        }
      })
      .catch(err => {
        console.error('Error loading zones:', err);
      });
  }

  function drawZonesOnMap(zones) {
    // Clear existing zones
    Object.values(zoneLayers).forEach(layer => map.removeLayer(layer));
    zoneLayers = {};
    
    // Don't draw if zones are hidden
    if (!zonesVisible) return;
    
    zones.forEach(zone => {
      if (!zone.enabled) return;
      
      // Skip expired NOTAM zones
      if (zone.source === 'notam' && zone.end_date) {
        try {
          const endDate = new Date(zone.end_date);
          if (endDate < new Date()) {
            return; // Skip expired NOTAMs
          }
        } catch(e) {
          // If date parsing fails, include it anyway
        }
      }
      
      const coords = zone.coordinates || [];
      if (coords.length < 3) return;
      
      const color = zone.type === 'critical' ? '#ff4444' : zone.type === 'warning' ? '#ffb347' : '#4a9eff';
      
      const polygon = L.polygon(coords, {
        color: color,
        fillColor: color,
        fillOpacity: 0.2,
        weight: 2
      });
      
      // Only add to map if zones are visible
      if (zonesVisible) {
        polygon.addTo(map);
      }
      
      // Create popup content with full details for NOTAMs
      let popupContent = `<strong>${zone.name || 'Unnamed Zone'}</strong><br>Type: ${zone.type || 'warning'}`;
      
      if (zone.lower_altitude_ft !== undefined || zone.upper_altitude_ft !== undefined) {
        const lower = zone.lower_altitude_ft || 0;
        const upper = zone.upper_altitude_ft || 'unlimited';
        popupContent += `<br>Altitude: ${lower}ft - ${upper}ft`;
      }
      
      // Add full NOTAM details if it's a NOTAM zone
      if (zone.source === 'notam') {
        if (zone.description) {
          popupContent += `<br><br><strong>NOTAM Details:</strong><br><div style="max-width:300px; word-wrap:break-word; font-size:0.9em;">${zone.description}</div>`;
        }
        if (zone.notam_id) {
          popupContent += `<br><small>NOTAM ID: ${zone.notam_id}</small>`;
        }
        if (zone.end_date) {
          try {
            const endDate = new Date(zone.end_date);
            popupContent += `<br><small>Valid until: ${endDate.toLocaleString()}</small>`;
          } catch(e) {
            popupContent += `<br><small>Valid until: ${zone.end_date}</small>`;
          }
        }
      }
      
      // Add airspace class and frequency for OpenAir zones
      if (zone.source === 'openair') {
        if (zone.airspace_class) {
          popupContent += `<br><small>Class: ${zone.airspace_class}</small>`;
        }
        if (zone.frequency) {
          popupContent += `<br><small>Frequency: ${zone.frequency} MHz</small>`;
        }
      }
      
      polygon.bindPopup(popupContent);
      zoneLayers[zone.id] = polygon;
    });
  }
  
  function toggleZonesVisibility() {
    zonesVisible = !zonesVisible;
    const btn = document.getElementById('toggleZonesButton');
    
    if (zonesVisible) {
      // Show zones
      Object.values(zoneLayers).forEach(layer => {
        if (!map.hasLayer(layer)) {
          layer.addTo(map);
        }
      });
      if (btn) {
        btn.textContent = 'Hide Zones';
        btn.style.backgroundColor = 'var(--accent-cyan)';
        btn.style.color = 'var(--color-bg)';
      }
    } else {
      // Hide zones
      Object.values(zoneLayers).forEach(layer => {
        if (map.hasLayer(layer)) {
          map.removeLayer(layer);
        }
      });
      if (btn) {
        btn.textContent = 'Show Zones';
        btn.style.backgroundColor = 'var(--color-text-dim)';
        btn.style.color = 'var(--color-text)';
      }
    }
  }

  function startDrawingZone() {
    drawingZone = true;
    zoneDrawPoints = [];
    alert('Click on the map to draw zone polygon. Right-click or press ESC to finish.');
    
    const clickHandler = function(e) {
      zoneDrawPoints.push([e.latlng.lat, e.latlng.lng]);
      
      if (currentZonePolygon) {
        map.removeLayer(currentZonePolygon);
      }
      
      if (zoneDrawPoints.length >= 3) {
        currentZonePolygon = L.polygon(zoneDrawPoints, {
          color: '#4a9eff',
          fillColor: '#4a9eff',
          fillOpacity: 0.3,
          weight: 2
        }).addTo(map);
      }
    };
    
    const contextHandler = function(e) {
      if (zoneDrawPoints.length >= 3) {
        finishZoneDrawing();
      }
      map.off('click', clickHandler);
      map.off('contextmenu', contextHandler);
    };
    
    map.on('click', clickHandler);
    map.on('contextmenu', contextHandler);
    
    document.addEventListener('keydown', function escHandler(e) {
      if (e.key === 'Escape') {
        if (zoneDrawPoints.length >= 3) {
          finishZoneDrawing();
        } else {
          cancelZoneDrawing();
        }
        document.removeEventListener('keydown', escHandler);
      }
    });
  }

  function finishZoneDrawing() {
    if (zoneDrawPoints.length < 3) {
      alert('Zone needs at least 3 points');
      return;
    }
    
    const name = prompt('Enter zone name:');
    if (!name) {
      cancelZoneDrawing();
      return;
    }
    
    const type = prompt('Enter zone type (warning/critical/info):', 'warning');
    
    const zone = {
      name: name,
      type: type || 'warning',
      coordinates: zoneDrawPoints,
      enabled: true
    };
    
    fetch('/api/zones', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(zone)
    })
      .then(res => res.json())
      .then(data => {
        if (data.status === 'ok') {
          loadZones();
          cancelZoneDrawing();
        }
      });
  }

  function cancelZoneDrawing() {
    drawingZone = false;
    zoneDrawPoints = [];
    if (currentZonePolygon) {
      map.removeLayer(currentZonePolygon);
      currentZonePolygon = null;
    }
  }

  function toggleZone(zoneId) {
    fetch('/api/zones')
      .then(res => res.json())
      .then(data => {
        const zone = data.zones.find(z => z.id === zoneId);
        if (zone) {
          zone.enabled = !zone.enabled;
          fetch(`/api/zones/${zoneId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(zone)
          })
            .then(() => loadZones());
        }
      });
  }

  function deleteZone(zoneId) {
    if (confirm('Delete this zone?')) {
      fetch(`/api/zones/${zoneId}`, {method: 'DELETE'})
        .then(() => loadZones());
    }
  }

  // Incident Log Functions
  function openIncidentsPanel() {
    document.getElementById('incidentsModal').style.display = 'block';
    loadIncidents();
  }

  function closeIncidentsPanel() {
    document.getElementById('incidentsModal').style.display = 'none';
  }

  function loadIncidents() {
    const type = document.getElementById('incidentTypeFilter').value;
    const limit = document.getElementById('incidentLimit').value;
    
    let url = `/api/incidents?limit=${limit}`;
    if (type) url += `&type=${type}`;
    
    fetch(url)
      .then(res => res.json())
      .then(data => {
        const incidentsList = document.getElementById('incidentsList');
        incidentsList.innerHTML = '';
        
        if (data.incidents.length === 0) {
          incidentsList.innerHTML = '<div style="text-align:center; color:#9a9a9a; padding:20px;">No incidents found</div>';
          return;
        }
        
        data.incidents.forEach(incident => {
          const incDiv = document.createElement('div');
          incDiv.style.cssText = 'border:1px solid #4a4a4a; background:#2a2a2a; padding:12px; margin-bottom:8px; border-radius:3px; border-left:3px solid ' + 
            (incident.type === 'zone_entry' ? '#ff4444' : incident.type === 'zone_exit' ? '#ffb347' : '#4a9eff') + ';';
          
          const time = new Date(incident.timestamp).toLocaleString();
          const typeLabel = incident.type === 'zone_entry' ? 'ZONE ENTRY' : incident.type === 'zone_exit' ? 'ZONE EXIT' : 'DETECTION';
          
          incDiv.innerHTML = `
            <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
              <strong style="color:#e0e0e0;">${typeLabel}</strong>
              <span style="color:#9a9a9a; font-size:0.85em;">${time}</span>
            </div>
            <div style="color:#e0e0e0; font-size:0.9em;">
              MAC: ${incident.mac} ${incident.alias ? `(${incident.alias})` : ''}<br>
              ${incident.zone_name ? `Zone: ${incident.zone_name}<br>` : ''}
              ${incident.drone_lat && incident.drone_lat !== 0 ? `Location: ${incident.drone_lat.toFixed(6)}, ${incident.drone_long.toFixed(6)}<br>` : ''}
              ${incident.basic_id ? `RID: ${incident.basic_id}<br>` : ''}
              ${incident.rssi ? `RSSI: ${incident.rssi} dBm` : ''}
            </div>
          `;
          incidentsList.appendChild(incDiv);
        });
      });
  }

  // Load zones on page load
  socket.on('connect', function() {
    loadZones();
  });
  
  // Listen for zones updates
  socket.on('zones_updated', function(data) {
    loadZones();
  });

  // Listen for zone events
  socket.on('zone_event', function(data) {
    const eventType = data.type === 'entry' ? 'ZONE ENTRY' : 'ZONE EXIT';
    const zoneName = data.zone.name || 'Unknown Zone';
    showToast(`${eventType}: ${zoneName}`, `Drone ${data.mac} ${data.type === 'entry' ? 'entered' : 'exited'} ${zoneName}`, data.type === 'entry' ? 'no-gps' : 'known-drone');
  });

  // Listen for new incidents
  socket.on('new_incident', function(incident) {
    if (document.getElementById('incidentsModal').style.display === 'block') {
      loadIncidents();
    }
  });
  </script>
</body>
</html>
'''

    # Updated: The main mapping page now shows serial statuses for all selected USB devices.
HTML_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Drone Detection System</title>
  <!-- Add Socket.IO client script for real-time updates -->
  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
  <style>
    /* Hide tile seams on all map layers */
    .leaflet-tile {
      border: none !important;
      box-shadow: none !important;
      background-color: transparent !important;
      image-rendering: crisp-edges !important;
      transition: none !important;
    }
    .leaflet-container {
      background-color: black !important;
    }
    /* Toggle switch styling */
    .switch { position: relative; display: inline-block; vertical-align: middle; width: 40px; height: 20px; }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #555; transition: .4s; border-radius: 20px; }
    .slider:before {
      position: absolute;
      content: "";
      height: 16px;
      width: 16px;
      left: 2px;
      top: 50%;
      background-color: lime;
      border: 1px solid #9B30FF;
      transition: .4s;
      border-radius: 50%;
      transform: translateY(-50%);
    }
    .switch input:checked + .slider { background-color: lime; }
    .switch input:checked + .slider:before {
      transform: translateX(20px) translateY(-50%);
      border: 1px solid #9B30FF;
    }
    body, html {
      margin: 0;
      padding: 0;
      background-color: #1a1a1a;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif;
      color: #e0e0e0;
    }
    #map { height: 100vh; }
    /* Layer control styling (bottom left) */
    #layerControl {
      position: absolute;
      bottom: 10px;
      left: 10px;
      background: rgba(26, 26, 26, 0.95);
      padding: 6px;
      border: 1px solid #4a4a4a;
      border-radius: 4px;
      color: #e0e0e0;
      font-size: 0.75em;
      z-index: 1000;
      box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    }
    #layerControl > label {
      color: #ffb347;
      font-weight: 600;
      display: block;
      margin-bottom: 4px;
    }
    #layerControl select,
    #layerControl select option {
      background-color: #2a2a2a;
      color: #e0e0e0;
      border: 1px solid #4a4a4a;
      padding: 4px;
      font-size: 0.85em;
    }
    
        #filterBox {
          position: absolute;
          top: 10px;
          right: 10px;
          background: rgba(0, 20, 0, 0.98);
          padding: 0;
          width: 320px;
          max-width: 25vw;
          border: 2px solid #00ff41;
          border-top: 3px solid #00ff41;
          color: #00ff41;
          max-height: 95vh;
          overflow-y: auto;
          overflow-x: hidden;
          z-index: 1000;
          box-shadow: 0 0 20px rgba(0, 255, 65, 0.3), inset 0 0 10px rgba(0, 255, 65, 0.1);
          font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
        }
        @media (max-width: 600px) {
          #filterBox {
            width: 37.5vw;
            max-width: 90vw;
          }
        }
        /* Auto-size inputs inside filterBox */
        #filterBox input[type="text"],
        #filterBox input[type="password"],
        #filterBox input[type="range"],
        #filterBox select {
          width: auto !important;
          min-width: 0;
        }
    #filterBox.collapsed #filterContent {
      display: none;
    }
    /* Tighten header when collapsed */
    #filterBox.collapsed {
      padding: 4px;
      width: auto;
    }
    #filterBox.collapsed #filterHeader {
      padding: 0;
    }
    #filterBox.collapsed #filterHeader h3 {
      display: inline-block;
      flex: none;
      width: auto;
      margin: 0;
      color: #00ff41;
      font-weight: 700;
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
    }
# Add margin to filterToggle when collapsed
    #filterBox.collapsed #filterHeader #filterToggle {
      margin-left: 5px;
    }
    #filterBox:not(.collapsed) #filterHeader h3 {
      display: none;
    }
    
    /* Aircraft & Ships Box styling (similar to filterBox) */
    #aircraftShipsBox.collapsed #aircraftShipsContent {
      display: none;
    }
    #aircraftShipsBox.collapsed {
      padding: 4px;
      width: auto;
    }
    #aircraftShipsBox.collapsed #aircraftShipsHeader {
      padding: 0;
    }
    #aircraftShipsBox.collapsed #aircraftShipsHeader h3 {
      display: inline-block;
      flex: none;
      width: auto;
      margin: 0;
      color: #36C3FF;
      font-weight: 700;
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
    }
    #aircraftShipsBox.collapsed #aircraftShipsHeader #aircraftShipsToggle {
      margin-left: 5px;
    }
    #aircraftShipsBox:not(.collapsed) #aircraftShipsHeader h3 {
      display: inline-block;
    }
    
    #filterHeader {
      display: flex;
      align-items: center;
      background: rgba(0, 20, 0, 0.95);
      border-bottom: 2px solid #00ff41;
    }
    #filterBox:not(.collapsed) #filterHeader {
      justify-content: flex-end;
      padding: 0;
    }
    #filterToggle {
      color: #00ff41;
      font-weight: 700;
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
      padding: 10px 12px;
      cursor: pointer;
      text-shadow: 0 0 5px rgba(0, 255, 65, 0.5);
    }
    #filterToggle:hover {
      background: rgba(0, 255, 65, 0.2);
    }
    #filterHeader h3 {
      flex: none;
      text-align: center;
      margin: 0;
      font-size: 1.2em;
      display: block;
      width: 100%;
      color: #00ff41;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 2px;
      border-bottom: 2px solid #00ff41;
      padding: 10px 12px;
      margin-bottom: 0;
      background: rgba(0, 255, 65, 0.1);
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
      text-shadow: 0 0 10px rgba(0, 255, 65, 0.5);
    }
    
    /* USB status styling - now integrated in filter window */
    #serialStatus div { 
      margin-bottom: 4px; 
      padding: 4px 8px;
      border-left: 2px solid #00ff41;
      background: rgba(0, 255, 65, 0.05);
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
      font-size: 0.85em;
    }
    #serialStatus div:last-child { margin-bottom: 0; }
    
    .usb-name { 
      color: #00ff41; 
      font-weight: 700; 
      text-shadow: 0 0 5px rgba(0, 255, 65, 0.5);
    }
    .drone-item {
      display: inline-block;
      border: 1px solid #00ff41;
      margin: 3px;
      padding: 6px 10px;
      cursor: pointer;
      background-color: rgba(0, 20, 0, 0.5);
      border-radius: 0;
      font-size: 0.85em;
      transition: all 0.2s;
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
      color: #00ff41;
      font-weight: 500;
    }
    .drone-item:hover {
      background-color: rgba(0, 255, 65, 0.2);
      border-color: #00ff41;
      box-shadow: 0 0 8px rgba(0, 255, 65, 0.4);
    }
    .drone-item.no-gps {
      position: relative;
      border: 2px solid #ffaa00 !important;
      background-color: rgba(255, 170, 0, 0.15);
      color: #ffaa00;
    }
    /* Highlight recently seen drones (but not no-GPS drones) */
    .drone-item.recent:not(.no-gps) {
      border-color: #00ff41;
      box-shadow: 0 0 8px rgba(0, 255, 65, 0.6);
      background-color: rgba(0, 255, 65, 0.15);
    }
    .placeholder {
      border: 1px solid #00ff41;
      border-radius: 0;
      min-height: 100px;
      margin-top: 8px;
      overflow-y: auto;
      max-height: 200px;
      background-color: rgba(0, 10, 0, 0.8);
      padding: 6px;
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
    }
    .selected { 
      background-color: rgba(0, 255, 65, 0.3) !important; 
      border-color: #00ff41 !important; 
      box-shadow: 0 0 10px rgba(0, 255, 65, 0.5) !important;
    }
    .leaflet-popup > .leaflet-popup-content-wrapper { 
      background-color: #1a1a1a; 
      color: #e0e0e0; 
      border: 1px solid #4a4a4a; 
      border-radius: 4px;
      width: 240px !important;
      max-width: 240px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.6);
    }
    .leaflet-popup-content {
      font-size: 0.75em;
      line-height: 1.2em;
      white-space: normal;
    }
    .leaflet-popup-tip { background: #1a1a1a; border: 1px solid #4a4a4a; }
    /* Collapse inner Leaflet popup layers into the outer wrapper */
    .leaflet-popup-content {
      background: transparent !important;
      padding: 0 !important;
      box-shadow: none !important;
      color: inherit !important;
    }
    .leaflet-popup-tip-container,
    .leaflet-popup-tip {
      background: transparent !important;
      box-shadow: none !important;
    }
    /* Collapse inner popup layers for no-GPS popups */
    .leaflet-popup.no-gps-popup > .leaflet-popup-content-wrapper {
      /* ensure outer wrapper styling persists */
      background-color: #1a1a1a !important;
      color: #e0e0e0 !important;
      border-color: #4a9eff !important;
    }
    .leaflet-popup.no-gps-popup .leaflet-popup-content {
      background: transparent !important;
      padding: 0 !important;
      box-shadow: none !important;
      color: inherit !important;
    }
    .leaflet-popup.no-gps-popup .leaflet-popup-tip-container,
    .leaflet-popup.no-gps-popup .leaflet-popup-tip {
      background: transparent !important;
      box-shadow: none !important;
    }
    button {
      margin-top: 4px;
      padding: 6px 12px;
      font-size: 0.85em;
      border: 1px solid #4a4a4a;
      background-color: #2a2a2a;
      color: #e0e0e0;
      cursor: pointer;
      width: auto;
      border-radius: 3px;
      transition: all 0.2s;
      font-weight: 500;
    }
    button:hover {
      background-color: #3a3a3a;
      border-color: #6a6a6a;
    }
    select {
      background-color: #2a2a2a;
      color: #e0e0e0;
      border: 1px solid #4a4a4a;
      padding: 6px;
      border-radius: 3px;
    }
    .leaflet-control-zoom-in, .leaflet-control-zoom-out {
      background: rgba(26, 26, 26, 0.95);
      color: #e0e0e0;
      border: 1px solid #4a4a4a;
      border-radius: 3px;
    }
    /* Style zoom control container to match drone box */
    .leaflet-control-zoom.leaflet-bar {
      background: rgba(26, 26, 26, 0.95);
      border: 1px solid #4a4a4a;
      border-radius: 4px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    }
    .leaflet-control-zoom.leaflet-bar a {
      background: transparent;
      color: #e0e0e0;
      border: none;
      width: 32px;
      height: 32px;
      line-height: 32px;
      text-align: center;
      padding: 0;
      user-select: none;
      caret-color: transparent;
      cursor: pointer;
      outline: none;
      font-weight: 600;
    }
    .leaflet-control-zoom.leaflet-bar a:focus {
      outline: none;
      caret-color: transparent;
    }
    .leaflet-control-zoom.leaflet-bar a:hover {
      background: rgba(255, 255, 255, 0.1);
      color: #ffb347;
    }
    .leaflet-control-zoom-in:hover, .leaflet-control-zoom-out:hover { background-color: #3a3a3a; }
    input#aliasInput {
      background-color: #2a2a2a;
      color: #e0e0e0;
      border: 1px solid #4a4a4a;
      padding: 6px;
      font-size: 0.9em;
      caret-color: #ffb347;
      outline: none;
      border-radius: 3px;
    }
    .leaflet-popup-content-wrapper input:not(#aliasInput) {
      caret-color: transparent;
    }
    /* Popup button styling */
    .leaflet-popup-content-wrapper button {
      display: inline-block;
      margin: 4px 4px 4px 0;
      padding: 6px 10px;
      font-size: 0.85em;
      width: auto;
      background-color: #2a2a2a;
      border: 1px solid #4a4a4a;
      color: #e0e0e0;
      box-shadow: none;
      text-shadow: none;
      border-radius: 3px;
      font-weight: 500;
      transition: all 0.2s;
    }

    /* Locked button styling */
    .leaflet-popup-content-wrapper button[style*="background-color: green"] {
      background-color: #4a9eff;
      color: #ffffff;
      border-color: #4a9eff;
    }

    /* Hover effect */
    .leaflet-popup-content-wrapper button:hover {
      background-color: #3a3a3a;
      border-color: #6a6a6a;
    }
    .leaflet-popup-content-wrapper input[type="text"],
    .leaflet-popup-content-wrapper input[type="range"] {
      font-size: 0.75em;
      padding: 2px;
    }
    /* Disable tile transitions to prevent blur and hide tile seams */
    .leaflet-tile {
      display: block;
      margin: 0;
      padding: 0;
      transition: none !important;
      image-rendering: crisp-edges;
      background-color: black;
      border: none !important;
      box-shadow: none !important;
    }
    .leaflet-container {
      background-color: black;
    }
    /* Disable text cursor in drone list and filter toggle */
    .drone-item, #filterToggle {
      user-select: none;
      caret-color: transparent;
      outline: none;
    }
    .drone-item:focus, #filterToggle:focus {
      outline: none;
      caret-color: transparent;
    }
    /* Professional styling for filter headings */
    #filterContent > h3:nth-of-type(1) {
      color: #4a9eff;         /* Active Drones in professional blue */
      text-align: center;
      font-size: 1em;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin: 12px 0 8px 0;
      padding-bottom: 6px;
      border-bottom: 1px solid #4a4a4a;
    }
    #filterContent > h3:nth-of-type(2) {
      color: #9a9a9a;        /* Inactive Drones in gray */
      text-align: center;
      font-size: 1em;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin: 12px 0 8px 0;
      padding-bottom: 6px;
      border-bottom: 1px solid #4a4a4a;
    }
    #filterContent > h3 {
      display: block;
      width: 100%;
      text-align: center;
    }
    #filterContent > h3::before,
    #filterContent > h3::after {
      content: '';
      margin: 0;
    }
    /* Download buttons styling */
    #downloadButtons {
      display: flex;
      width: 100%;
      gap: 4px;
      margin-top: 8px;
    }
    #downloadButtons button {
      flex: 1;
      margin: 0;
      padding: 6px;
      font-size: 0.8em;
      border: 1px solid #4a4a4a;
      border-radius: 3px;
      background-color: #2a2a2a;
      color: #e0e0e0;
      cursor: pointer;
      font-weight: 500;
      transition: all 0.2s;
    }
    #downloadButtons button:hover {
      background-color: rgba(0, 255, 65, 0.2);
      border-color: #00ff41;
      box-shadow: 0 0 8px rgba(0, 255, 65, 0.4);
    }
    #downloadButtons button:focus {
      outline: none;
      caret-color: transparent;
    }
    #downloadCumulativeButtons button {
      border: 1px solid #00ff41;
      background: rgba(0, 20, 0, 0.6);
      color: #00ff41;
      font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-radius: 0;
      padding: 6px 8px;
      font-size: 0.75em;
    }
    #downloadCumulativeButtons button:hover {
      background-color: rgba(0, 255, 65, 0.2);
      border-color: #00ff41;
      box-shadow: 0 0 8px rgba(0, 255, 65, 0.4);
    }
    /* Gradient blue border flush with heading */
    #downloadSection {
      padding: 0 8px 8px 8px;  /* no top padding so border is flush with heading */
      margin-top: 12px;
    }
    /* Professional Download Logs header */
    #downloadSection .downloadHeader {
      margin: 10px 0 8px 0;
      text-align: center;
      color: #ffb347;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      font-size: 0.9em;
      border-bottom: 1px solid #4a4a4a;
      padding-bottom: 6px;
    }
    /* Staleout slider styling – match popup sliders */
    #staleoutSlider {
      -webkit-appearance: none;
      width: 100%;
      height: 3px;
      background: transparent;
      border: none;
      outline: none;
    }
    #staleoutSlider::-webkit-slider-runnable-track {
      width: 100%;
      height: 4px;
      background: #4a4a4a;
      border: none;
      border-radius: 2px;
    }
    #staleoutSlider::-webkit-slider-thumb {
      -webkit-appearance: none;
      height: 16px;
      width: 16px;
      background: #ffb347;
      border: 2px solid #2a2a2a;
      margin-top: -6px;
      border-radius: 50%;
      cursor: pointer;
    }
    /* Firefox */
    #staleoutSlider::-moz-range-track {
      width: 100%;
      height: 4px;
      background: #4a4a4a;
      border: none;
      border-radius: 2px;
    }
    #staleoutSlider::-moz-range-thumb {
      height: 16px;
      width: 16px;
      background: #ffb347;
      border: 2px solid #2a2a2a;
      margin-top: -6px;
      border-radius: 50%;
      cursor: pointer;
    }
    /* IE */
    #staleoutSlider::-ms-fill-lower,
    #staleoutSlider::-ms-fill-upper {
      background: #4a4a4a;
      border: none;
      border-radius: 2px;
    }
    #staleoutSlider::-ms-thumb {
      height: 16px;
      width: 16px;
      background: #ffb347;
      border: 2px solid #2a2a2a;
      border-radius: 50%;
      cursor: pointer;
      margin-top: -6px;
    }

    /* Popup range sliders styling */
    .leaflet-popup-content-wrapper input[type="range"] {
      -webkit-appearance: none;
      width: 100%;
      height: 3px;
      background: transparent;
      border: none;
    }
    .leaflet-popup-content-wrapper input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance: none;
      height: 16px;
      width: 16px;
      background: lime;
      border: 1px solid #9B30FF;
      margin-top: -6.5px;
      border-radius: 50%;
      cursor: pointer;
    }
    .leaflet-popup-content-wrapper input[type="range"]::-moz-range-thumb {
      height: 16px;
      width: 16px;
      background: lime;
      border: 1px solid #9B30FF;
      margin-top: -6.5px;
      border-radius: 50%;
      cursor: pointer;
    }
    /* Ensure popup sliders have the same track styling */
    .leaflet-popup-content-wrapper input[type="range"]::-webkit-slider-runnable-track {
      width: 100%;
      height: 3px;
      background: #9B30FF;
      border: 1px solid lime;
      border-radius: 0;
    }
    .leaflet-popup-content-wrapper input[type="range"]::-moz-range-track {
      width: 100%;
      height: 3px;
      background: #9B30FF;
      border: 1px solid lime;
      border-radius: 0;
    }

    /* 1) Remove rounded corners from all sliders */
    /* WebKit */
    input[type="range"]::-webkit-slider-runnable-track,
    input[type="range"]::-webkit-slider-thumb {
      border-radius: 0;
    }
    /* Firefox */
    input[type="range"]::-moz-range-track,
    input[type="range"]::-moz-range-thumb {
      border-radius: 0;
    }
    /* IE */
    input[type="range"]::-ms-fill-lower,
    input[type="range"]::-ms-fill-upper,
    input[type="range"]::-ms-thumb {
      border-radius: 0;
    }

    /* 2) Smaller, side-by-side Observer buttons */
    .leaflet-popup-content-wrapper #lock-observer,
    .leaflet-popup-content-wrapper #unlock-observer {
      display: inline-block;
      font-size: 0.9em;
      padding: 4px 6px;
      margin: 2px 4px 2px 0;
    }
    /* Cumulative download buttons styling to match regular download buttons */
    #downloadCumulativeButtons button {
      flex: 1;
      margin: 0;
      padding: 6px;
      font-size: 0.8em;
      border: 1px solid #4a4a4a;
      border-radius: 3px;
      background-color: #2a2a2a;
      color: #e0e0e0;
      cursor: pointer;
      font-weight: 500;
      transition: all 0.2s;
    }
    #downloadCumulativeButtons button:hover {
      background-color: #3a3a3a;
      border-color: #6a6a6a;
    }
    #downloadCumulativeButtons button:focus {
      outline: none;
      caret-color: transparent;
    }
    /* Audio alert toggle control */
    .audio-control {
      position: absolute;
      top: 10px;
      left: 10px;
      background: rgba(26, 26, 26, 0.95);
      border: 1px solid #4a4a4a;
      border-radius: 4px;
      padding: 8px 12px;
      z-index: 1000;
      box-shadow: 0 2px 8px rgba(0,0,0,0.5);
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.85em;
    }
    .audio-control label {
      color: #e0e0e0;
      cursor: pointer;
      user-select: none;
      font-weight: 500;
    }
    .audio-control input[type="checkbox"] {
      width: 18px;
      height: 18px;
      cursor: pointer;
      accent-color: #ffb347;
    }
    .audio-control button {
      margin-left: 8px;
      padding: 4px 8px;
      font-size: 0.75em;
      border: 1px solid #4a4a4a;
      background-color: #2a2a2a;
      color: #e0e0e0;
      border-radius: 3px;
      cursor: pointer;
      font-weight: 500;
      transition: all 0.2s;
    }
    .audio-control button:hover {
      background-color: #3a3a3a;
      border-color: #6a6a6a;
    }
    /* Toast notification styling */
    .toast-container {
      position: fixed;
      top: 60px;
      right: 20px;
      z-index: 3000;
      display: flex;
      flex-direction: column;
      gap: 8px;
      pointer-events: none;
    }
    .toast {
      background: rgba(26, 26, 26, 0.98);
      border: 1px solid #4a4a4a;
      border-left: 3px solid #4a9eff;
      border-radius: 4px;
      padding: 12px 16px;
      min-width: 300px;
      max-width: 400px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.6);
      display: flex;
      align-items: flex-start;
      gap: 12px;
      animation: slideInRight 0.3s ease-out;
      pointer-events: auto;
      cursor: pointer;
    }
    .toast.no-gps {
      border-left-color: #4a9eff;
    }
    .toast.new-drone {
      border-left-color: #ffb347;
    }
    .toast.known-drone {
      border-left-color: #6a6a6a;
    }
    .toast-icon {
      font-size: 1.2em;
      flex-shrink: 0;
    }
    .toast-content {
      flex: 1;
    }
    .toast-title {
      font-weight: 600;
      color: #e0e0e0;
      font-size: 0.9em;
      margin-bottom: 4px;
    }
    .toast-message {
      font-size: 0.8em;
      color: #9a9a9a;
      line-height: 1.4;
    }
    .toast-close {
      background: none;
      border: none;
      color: #6a6a6a;
      font-size: 1.2em;
      cursor: pointer;
      padding: 0;
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: color 0.2s;
    }
    .toast-close:hover {
      color: #e0e0e0;
    }
    @keyframes slideInRight {
      from {
        transform: translateX(100%);
        opacity: 0;
      }
      to {
        transform: translateX(0);
        opacity: 1;
      }
    }
    @keyframes slideOutRight {
      from {
        transform: translateX(0);
        opacity: 1;
      }
      to {
        transform: translateX(100%);
        opacity: 0;
      }
    }
    .toast.removing {
      animation: slideOutRight 0.3s ease-out forwards;
    }
</style>
    <style>
      /* Remove glow and shadows on text boxes, selects, and buttons */
      input, select, button {
        text-shadow: none !important;
        box-shadow: none !important;
      }
    </style>
</head>
<body>
  <!-- Audio Alert Toggle -->
  <div class="audio-control" style="top: 10px;">
    <input type="checkbox" id="audioAlertToggle" checked>
    <label for="audioAlertToggle">Audio Alerts</label>
    <button id="testAlertButton" title="Test Alert">Test</button>
  </div>
  <!-- Lightning Detection Toggle -->
  <div class="audio-control" style="top: 50px;">
    <input type="checkbox" id="lightningDetectionToggle" checked>
    <label for="lightningDetectionToggle">Lightning Detection</label>
  </div>
  <!-- Toast Container -->
  <div class="toast-container" id="toastContainer"></div>
<div id="map"></div>
<!-- Aircraft & Ships List Panel -->
<div id="aircraftShipsBox" style="position: absolute; top: 10px; left: 10px; background: rgba(0, 20, 0, 0.98); padding: 0; width: 320px; max-width: 25vw; border: 2px solid #36C3FF; border-top: 3px solid #36C3FF; color: #36C3FF; max-height: 95vh; overflow-y: auto; overflow-x: hidden; z-index: 1000; box-shadow: 0 0 20px rgba(54, 195, 255, 0.3), inset 0 0 10px rgba(54, 195, 255, 0.1); font-family: 'Courier New', 'Monaco', 'Consolas', monospace;">
  <div id="aircraftShipsHeader" style="display: flex; align-items: center; background: rgba(0, 20, 0, 0.95); border-bottom: 2px solid #36C3FF; justify-content: space-between; padding: 8px 12px;">
    <h3 style="flex: none; width: auto; margin: 0; color: #36C3FF; font-weight: 700; font-family: 'Courier New', 'Monaco', 'Consolas', monospace; font-size: 0.9em;">Aircraft & Ships</h3>
    <span id="aircraftShipsToggle" style="cursor: pointer; font-size: 20px; color: #36C3FF; font-weight: 700; padding: 0 8px;">[-]</span>
  </div>
  <div id="aircraftShipsContent" style="padding: 12px;">
    <div style="padding: 12px; border-bottom: 1px solid #36C3FF;">
      <h4 style="color:#36C3FF; font-weight:700; margin:0 0 8px 0; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(54,195,255,0.5);">AIRCRAFT</h4>
      <div id="aircraftPlaceholder" class="placeholder" style="min-height: 20px;"></div>
    </div>
    <div style="padding: 12px; border-bottom: 1px solid #36C3FF;">
      <h4 style="color:#36C3FF; font-weight:700; margin:0 0 8px 0; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(54,195,255,0.5);">SHIPS</h4>
      <div id="shipsPlaceholder" class="placeholder" style="min-height: 20px;"></div>
    </div>
  </div>
</div>
<div id="filterBox">
  <div id="filterHeader">
    <h3>Drones</h3>
    <span id="filterToggle" style="cursor: pointer; font-size: 20px;">[-]</span>
  </div>
  <div id="filterContent">
    <div style="padding: 12px; border-bottom: 1px solid #00ff41;">
      <h4 style="color:#00ff41; font-weight:700; margin:0 0 8px 0; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(0,255,65,0.5);">ACTIVE DRONES</h4>
      <div id="activePlaceholder" class="placeholder"></div>
    </div>
    <div style="padding: 12px; border-bottom: 1px solid #00ff41;">
      <h4 style="color:#00ff41; font-weight:700; margin:0 0 8px 0; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(0,255,65,0.5);">INACTIVE DRONES</h4>
      <div id="inactivePlaceholder" class="placeholder"></div>
    </div>
    <!-- Staleout Slider -->
    <div style="margin-top:12px; padding:12px; border-bottom:1px solid #00ff41; display:flex; flex-direction:column; align-items:stretch; width:100%; box-sizing:border-box;">
      <label style="color:#00ff41; font-weight:700; margin-bottom:8px; display:block; width:100%; text-align:center; font-size:0.85em; text-transform:uppercase; letter-spacing:1px; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(0,255,65,0.5);">STALEOUT TIME</label>
      <input type="range" id="staleoutSlider" min="1" max="5" step="1" value="1" 
             style="width:100%; margin-bottom:6px;">
      <input type="range" id="staleoutSlider" min="1" max="5" step="1" value="1" 
             style="width:100%; margin-bottom:8px; accent-color:#00ff41; cursor:pointer;">
      <div id="staleoutValue" style="color:#00ff41; width:100%; text-align:center; font-size:0.9em; font-weight:700; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(0,255,65,0.5);">1 MIN</div>
    </div>
    <!-- Downloads Section -->
    <div id="downloadSection" style="padding:12px; border-bottom:1px solid #00ff41;">
      <h4 style="color:#00ff41; font-weight:700; margin:0 0 10px 0; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; font-family:'Courier New',monospace; text-align:center; text-shadow:0 0 5px rgba(0,255,65,0.5);">DOWNLOAD LOGS</h4>
      <div id="downloadButtons">
        <button id="downloadCsv">CSV</button>
        <button id="downloadKml">KML</button>
        <button id="downloadAliases">Aliases</button>
      </div>
      <div id="downloadCumulativeButtons" style="display:flex; gap:4px; justify-content:center; margin-top:4px;">
        <button id="downloadCumulativeCsv">Cumulative CSV</button>
        <button id="downloadCumulativeKml">Cumulative KML</button>
      </div>
    </div>
    <!-- Basemap Section -->
    <div style="padding:12px; border-bottom:1px solid #00ff41;">
      <h4 style="margin: 0 0 10px 0; text-align: center; color:#00ff41; font-weight:700; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(0,255,65,0.5);">BASEMAP</h4>
      <select id="layerSelect" style="background-color:rgba(0,20,0,0.8); color:#00ff41; border:1px solid #00ff41; padding:8px; font-size:0.85em; width:100%; margin:0 auto; display:block; border-radius:0; font-family:'Courier New',monospace; font-weight:600; cursor:pointer;">
        <option value="osmStandard">OSM Standard</option>
        <option value="osmHumanitarian">OSM Humanitarian</option>
        <option value="cartoPositron">CartoDB Positron</option>
        <option value="cartoDarkMatter">CartoDB Dark Matter</option>
        <option value="esriWorldImagery" selected>Esri World Imagery</option>
        <option value="esriWorldTopo">Esri World TopoMap</option>
        <option value="esriDarkGray">Esri Dark Gray Canvas</option>
        <option value="openTopoMap">OpenTopoMap</option>
      </select>
    </div>
    <!-- View Toggle Section -->
    <div style="padding:12px; border-bottom:1px solid #00ff41;">
      <h4 style="margin: 0 0 10px 0; text-align: center; color:#00ff41; font-weight:700; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; font-family:'Courier New',monospace; text-shadow:0 0 5px rgba(0,255,65,0.5);">VIEW MODE</h4>
      <button id="toggle3DViewButton"
              style="display:block;
                     width:100%;
                     margin:0;
                     padding:10px;
                     background-color:#36C3FF;
                     color:#0F1215;
                     border:1px solid #36C3FF;
                     font-size:0.9em;
                     font-weight:600;
                     cursor:pointer;
                     font-family:'Courier New',monospace;
                     text-transform:uppercase;
                     letter-spacing:1px;
                     transition:background 0.2s;">
        3D View
      </button>
    </div>
    <div style="padding:12px; border-bottom:1px solid #00ff41;">
      <button id="settingsButton"
              style="display:block;
                     width:100%;
                     margin:0 0 8px 0;
                     padding:10px;
                     border:2px solid #00ff41;
                     background-color:rgba(0,20,0,0.6);
                     color:#00ff41;
                     font-size:0.9em;
                     border-radius:0;
                     cursor:pointer;
                     font-weight:700;
                     text-transform:uppercase;
                     letter-spacing:1px;
                     font-family:'Courier New',monospace;
                     transition:all 0.2s;
                     text-shadow:0 0 5px rgba(0,255,65,0.5);"
              onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)'; this.style.boxShadow='0 0 10px rgba(0,255,65,0.5)';"
              onmouseout="this.style.backgroundColor='rgba(0,20,0,0.6)'; this.style.boxShadow='none';"
              onclick="window.location.href='/select_ports'">
        SETTINGS
      </button>
      <button id="zonesButton"
              style="display:block;
                     width:100%;
                     margin:0;
                     padding:10px;
                     border:2px solid #00ff41;
                     background-color:rgba(0,20,0,0.6);
                     color:#00ff41;
                     font-size:0.9em;
                     border-radius:0;
                     cursor:pointer;
                     font-weight:700;
                     text-transform:uppercase;
                     letter-spacing:1px;
                     font-family:'Courier New',monospace;
                     transition:all 0.2s;
                     text-shadow:0 0 5px rgba(0,255,65,0.5);"
              onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)'; this.style.boxShadow='0 0 10px rgba(0,255,65,0.5)';"
              onmouseout="this.style.backgroundColor='rgba(0,20,0,0.6)'; this.style.boxShadow='none';"
              onclick="openZonesPanel()">
        ZONES
      </button>
    </div>
    <button id="toggleZonesButton"
            style="display:block;
                   width:100%;
                   margin:var(--space-2) 0;
                   padding:var(--space-3) var(--space-4);
                   border:1px solid var(--accent-cyan);
                   background-color:var(--accent-cyan);
                   color:var(--color-bg);
                   font-size:14px;
                   border-radius:var(--radius-md);
                   cursor:pointer;
                   font-weight:500;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all var(--transition-base);
                   font-family:var(--font-sans);"
            onmouseover="this.style.backgroundColor='#4DD0FF'; this.style.borderColor='#4DD0FF';"
            onmouseout="this.style.backgroundColor='var(--accent-cyan)'; this.style.borderColor='var(--accent-cyan)';"
            onclick="toggleZonesVisibility()">
      Hide Zones
    </button>
    <button id="toggleAisButton"
            style="display:block;
                   width:100%;
                   margin:var(--space-2) 0;
                   padding:var(--space-3) var(--space-4);
                   border:1px solid var(--accent-cyan);
                   background-color:var(--accent-cyan);
                   color:var(--color-bg);
                   font-size:14px;
                   border-radius:var(--radius-md);
                   cursor:pointer;
                   font-weight:500;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all var(--transition-base);
                   font-family:var(--font-sans);"
            onmouseover="this.style.backgroundColor='#4DD0FF'; this.style.borderColor='#4DD0FF';"
            onmouseout="this.style.backgroundColor='var(--accent-cyan)'; this.style.borderColor='var(--accent-cyan)';"
            onclick="toggleAisVessels()">
      Hide Vessels
    </button>
    <button id="toggleAprsButton"
            style="display:block;
                   width:100%;
                   margin:var(--space-2) 0;
                   padding:var(--space-3) var(--space-4);
                   border:1px solid var(--accent-cyan);
                   background-color:var(--accent-cyan);
                   color:var(--color-bg);
                   font-size:14px;
                   border-radius:var(--radius-md);
                   cursor:pointer;
                   font-weight:500;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all var(--transition-base);
                   font-family:var(--font-sans);"
            onmouseover="this.style.backgroundColor='#4DD0FF'; this.style.borderColor='#4DD0FF';"
            onmouseout="this.style.backgroundColor='var(--accent-cyan)'; this.style.borderColor='var(--accent-cyan)';"
            onclick="toggleAprsStations()">
      Hide APRS
    </button>
    <button id="toggleAdsbButton"
            style="display:block;
                   width:100%;
                   margin:var(--space-2) 0;
                   padding:var(--space-3) var(--space-4);
                   border:1px solid var(--accent-cyan);
                   background-color:var(--accent-cyan);
                   color:var(--color-bg);
                   font-size:14px;
                   border-radius:var(--radius-md);
                   cursor:pointer;
                   font-weight:500;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all var(--transition-base);
                   font-family:var(--font-sans);"
            onmouseover="this.style.backgroundColor='#4DD0FF'; this.style.borderColor='#4DD0FF';"
            onmouseout="this.style.backgroundColor='var(--accent-cyan)'; this.style.borderColor='var(--accent-cyan)';"
            onclick="toggleAdsbAircraft()">
      Hide Aircraft
    </button>
    <button id="toggleWeatherButton"
            style="display:block;
                   width:100%;
                   margin:var(--space-2) 0;
                   padding:var(--space-3) var(--space-4);
                   border:1px solid var(--accent-cyan);
                   background-color:var(--accent-cyan);
                   color:var(--color-bg);
                   font-size:14px;
                   border-radius:var(--radius-md);
                   cursor:pointer;
                   font-weight:500;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all var(--transition-base);
                   font-family:var(--font-sans);"
            onmouseover="this.style.backgroundColor='#4DD0FF'; this.style.borderColor='#4DD0FF';"
            onmouseout="this.style.backgroundColor='var(--accent-cyan)'; this.style.borderColor='var(--accent-cyan)';"
            onclick="toggleWeather()">
      Hide Weather
    </button>
    <button id="toggleWebcamsButton"
            style="display:block;
                   width:100%;
                   margin:var(--space-2) 0;
                   padding:var(--space-3) var(--space-4);
                   border:1px solid var(--accent-cyan);
                   background-color:var(--accent-cyan);
                   color:var(--color-bg);
                   font-size:14px;
                   border-radius:var(--radius-md);
                   cursor:pointer;
                   font-weight:500;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all var(--transition-base);
                   font-family:var(--font-sans);"
            onmouseover="this.style.backgroundColor='#4DD0FF'; this.style.borderColor='#4DD0FF';"
            onmouseout="this.style.backgroundColor='var(--accent-cyan)'; this.style.borderColor='var(--accent-cyan)';"
            onclick="toggleWebcams()">
      Hide Webcams
    </button>
    <button id="toggleMetOfficeButton"
            style="display:block;
                   width:100%;
                   margin:var(--space-2) 0;
                   padding:var(--space-3) var(--space-4);
                   border:1px solid var(--accent-cyan);
                   background-color:var(--accent-cyan);
                   color:var(--color-bg);
                   font-size:14px;
                   border-radius:var(--radius-md);
                   cursor:pointer;
                   font-weight:500;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all var(--transition-base);
                   font-family:var(--font-sans);"
            onmouseover="this.style.backgroundColor='#4DD0FF'; this.style.borderColor='#4DD0FF';"
            onmouseout="this.style.backgroundColor='var(--accent-cyan)'; this.style.borderColor='var(--accent-cyan)';"
            onclick="toggleMetOfficeWarnings()">
      Hide Warnings
    </button>
    <button id="aprsConfigButton"
            style="display:block;
                   width:100%;
                   margin:8px 0;
                   padding:8px;
                   border:1px solid #4a4a4a;
                   background-color:#2a2a2a;
                   color:#e0e0e0;
                   font-size:0.9em;
                   border-radius:3px;
                   cursor:pointer;
                   font-weight:600;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all 0.2s;"
            onmouseover="this.style.backgroundColor='#3a3a3a'; this.style.borderColor='#6a6a6a';"
            onmouseout="this.style.backgroundColor='#2a2a2a'; this.style.borderColor='#4a4a4a';"
            onclick="openAprsConfigModal()">
      Configure APRS
    </button>
    <div style="padding:12px; border-bottom:1px solid #00ff41;">
      <button id="weatherConfigButton"
              style="display:block;
                     width:100%;
                     margin:0 0 8px 0;
                     padding:10px;
                     border:2px solid #00ff41;
                     background-color:rgba(0,20,0,0.6);
                     color:#00ff41;
                     font-size:0.9em;
                     border-radius:0;
                     cursor:pointer;
                     font-weight:700;
                     text-transform:uppercase;
                     letter-spacing:1px;
                     font-family:'Courier New',monospace;
                     transition:all 0.2s;
                     text-shadow:0 0 5px rgba(0,255,65,0.5);"
              onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)'; this.style.boxShadow='0 0 10px rgba(0,255,65,0.5)';"
              onmouseout="this.style.backgroundColor='rgba(0,20,0,0.6)'; this.style.boxShadow='none';"
              onclick="openWeatherConfigModal()">
        WEATHER LOCATIONS
      </button>
      <button id="metOfficeSettingsButton"
              style="display:block;
                     width:100%;
                     margin:0;
                     padding:10px;
                     border:2px solid #00ff41;
                     background-color:rgba(0,20,0,0.6);
                     color:#00ff41;
                     font-size:0.9em;
                     border-radius:0;
                     cursor:pointer;
                     font-weight:700;
                     text-transform:uppercase;
                     letter-spacing:1px;
                     font-family:'Courier New',monospace;
                     transition:all 0.2s;
                     text-shadow:0 0 5px rgba(0,255,65,0.5);"
              onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)'; this.style.boxShadow='0 0 10px rgba(0,255,65,0.5)';"
              onmouseout="this.style.backgroundColor='rgba(0,20,0,0.6)'; this.style.boxShadow='none';"
              onclick="openMetOfficeSettingsModal()">
        WEATHER ALERTS
      </button>
    </div>
    <button id="incidentsButton"
            style="display:block;
                   width:100%;
                   margin:8px 0;
                   padding:8px;
                   border:1px solid #4a4a4a;
                   background-color:#2a2a2a;
                   color:#e0e0e0;
                   font-size:0.9em;
                   border-radius:3px;
                   cursor:pointer;
                   font-weight:600;
                   text-transform:uppercase;
                   letter-spacing:0.5px;
                   transition:all 0.2s;"
            onmouseover="this.style.backgroundColor='#3a3a3a'; this.style.borderColor='#6a6a6a';"
            onmouseout="this.style.backgroundColor='#2a2a2a'; this.style.borderColor='#4a4a4a';"
            onclick="openIncidentsPanel()">
      Incident Log
    </button>
    <!-- USB Status display with professional styling -->
    <div style="margin-top:12px; width:100%; border:1px solid #4a4a4a; background:#1f1f1f; padding:8px; border-radius:3px;">
      <div style="color:#ffb347; font-weight:600; text-align:center; font-size:0.85em; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px; border-bottom:1px solid #4a4a4a; padding-bottom:4px;">System Status</div>
      <div id="serialStatus" style="font-size:0.8em; text-align:center; line-height:1.6em; color:#e0e0e0;">
        <!-- USB port statuses will be injected here via WebSocket -->
      </div>
    </div>
  </div>
</div>

<!-- Zones Modal -->
<div id="zonesModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); z-index:10000; overflow:auto;">
  <div style="position:relative; background:#1a1a1a; border:1px solid #4a4a4a; margin:50px auto; padding:20px; max-width:800px; border-radius:4px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; border-bottom:1px solid #4a4a4a; padding-bottom:10px;">
      <h2 style="color:#ffb347; margin:0; text-transform:uppercase; letter-spacing:1px;">Zone Management</h2>
      <button onclick="closeZonesPanel()" style="background:#ff4444; border:none; color:#fff; padding:8px 16px; border-radius:3px; cursor:pointer; font-weight:600;">Close</button>
    </div>
    <div style="margin-bottom:20px;">
      <button onclick="startDrawingZone()" style="background:#4a9eff; border:none; color:#fff; padding:10px 20px; border-radius:3px; cursor:pointer; font-weight:600; margin-right:10px;">Draw New Zone</button>
      <button onclick="loadZones()" style="background:#2a2a2a; border:1px solid #4a4a4a; color:#e0e0e0; padding:10px 20px; border-radius:3px; cursor:pointer; font-weight:600;">Refresh</button>
    </div>
    <div id="zonesList" style="max-height:500px; overflow-y:auto;">
      <!-- Zones will be loaded here -->
    </div>
  </div>
</div>

<!-- Weather Config Modal -->
<div id="weatherConfigModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.9); z-index:10000; overflow:auto;">
  <div style="position:relative; background:rgba(0,20,0,0.98); border:2px solid #00ff41; margin:50px auto; padding:20px; max-width:600px; border-radius:0; box-shadow:0 0 30px rgba(0,255,65,0.5); font-family:'Courier New',monospace;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; border-bottom:2px solid #00ff41; padding-bottom:10px;">
      <h2 style="color:#00ff41; margin:0; text-transform:uppercase; letter-spacing:2px; font-weight:700; text-shadow:0 0 10px rgba(0,255,65,0.5);">WEATHER LOCATIONS</h2>
      <button onclick="closeWeatherConfigModal()" style="background:rgba(0,20,0,0.8); border:2px solid #00ff41; color:#00ff41; padding:8px 16px; border-radius:0; cursor:pointer; font-weight:700; font-family:'Courier New',monospace; text-transform:uppercase; letter-spacing:1px; text-shadow:0 0 5px rgba(0,255,65,0.5);" onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)';" onmouseout="this.style.backgroundColor='rgba(0,20,0,0.8)';">CLOSE</button>
    </div>
    <div style="margin-bottom:20px;">
      <h3 style="color:#00ff41; margin:0 0 10px 0; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; text-shadow:0 0 5px rgba(0,255,65,0.5);">ADD NEW LOCATION</h3>
      <div style="display:flex; gap:8px; margin-bottom:10px; flex-wrap:wrap;">
        <input type="text" id="weatherLocationName" placeholder="Location Name" style="flex:1; min-width:150px; background:rgba(0,10,0,0.8); color:#00ff41; border:1px solid #00ff41; padding:8px; border-radius:0; font-family:'Courier New',monospace; font-size:0.9em;">
        <input type="number" id="weatherLocationLat" placeholder="Latitude" step="0.0001" style="flex:1; min-width:120px; background:rgba(0,10,0,0.8); color:#00ff41; border:1px solid #00ff41; padding:8px; border-radius:0; font-family:'Courier New',monospace; font-size:0.9em;">
        <input type="number" id="weatherLocationLon" placeholder="Longitude" step="0.0001" style="flex:1; min-width:120px; background:rgba(0,10,0,0.8); color:#00ff41; border:1px solid #00ff41; padding:8px; border-radius:0; font-family:'Courier New',monospace; font-size:0.9em;">
        <button onclick="addWeatherLocation()" style="background:rgba(0,20,0,0.8); border:2px solid #00ff41; color:#00ff41; padding:8px 16px; border-radius:0; cursor:pointer; font-weight:700; font-family:'Courier New',monospace; text-transform:uppercase; letter-spacing:1px; text-shadow:0 0 5px rgba(0,255,65,0.5); white-space:nowrap;" onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)';" onmouseout="this.style.backgroundColor='rgba(0,20,0,0.8)';">ADD</button>
      </div>
      <div style="color:#00ff41; font-size:0.8em; margin-top:8px; padding:8px; background:rgba(0,10,0,0.5); border:1px solid #00ff41;">
        <strong>QUICK ADD:</strong> Click on map to add location at that point
      </div>
    </div>
    <div style="margin-bottom:20px;">
      <h3 style="color:#00ff41; margin:0 0 10px 0; text-transform:uppercase; letter-spacing:1px; font-size:0.9em; text-shadow:0 0 5px rgba(0,255,65,0.5);">CONFIGURED LOCATIONS</h3>
      <div id="weatherLocationsList" style="max-height:300px; overflow-y:auto; border:1px solid #00ff41; background:rgba(0,10,0,0.5); padding:10px;">
        <!-- Locations will be loaded here -->
      </div>
    </div>
    <div id="weatherConfigStatus" style="margin-top:10px; padding:8px; border-radius:0; display:none; font-size:0.85em; font-family:'Courier New',monospace;"></div>
  </div>
</div>

<!-- Incidents Modal -->
<div id="incidentsModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); z-index:10000; overflow:auto;">
  <div style="position:relative; background:var(--color-surface); border:1px solid var(--color-border); margin:50px auto; padding:var(--space-5); max-width:900px; border-radius:var(--radius-sm);">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:var(--space-5); border-bottom:1px solid var(--color-border); padding-bottom:var(--space-3);">
      <h2 style="color:var(--color-text); margin:0; text-transform:uppercase; letter-spacing:0.5px; font-family:var(--font-sans); font-size:20px; font-weight:600;">Incident Log</h2>
      <button onclick="closeIncidentsPanel()" style="background:var(--accent-red); border:none; color:var(--color-text); padding:var(--space-2) var(--space-4); border-radius:var(--radius-md); cursor:pointer; font-weight:500; font-family:var(--font-sans);">Close</button>
    </div>
    <div style="margin-bottom:var(--space-5); display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap;">
      <label style="color:var(--color-text); font-family:var(--font-sans);">Filter by Type:</label>
      <select id="incidentTypeFilter" style="background:var(--color-bg-alt); color:var(--color-text); border:1px solid var(--color-border); padding:var(--space-2); border-radius:var(--radius-sm); font-family:var(--font-sans);">
        <option value="">All Types</option>
        <option value="detection">Detection</option>
        <option value="zone_entry">Zone Entry</option>
        <option value="zone_exit">Zone Exit</option>
      </select>
      <label style="color:var(--color-text); font-family:var(--font-sans);">Limit:</label>
      <select id="incidentLimit" style="background:var(--color-bg-alt); color:var(--color-text); border:1px solid var(--color-border); padding:var(--space-2); border-radius:var(--radius-sm); font-family:var(--font-sans);">
        <option value="50">50</option>
        <option value="100" selected>100</option>
        <option value="200">200</option>
        <option value="500">500</option>
      </select>
      <button onclick="loadIncidents()" style="background:var(--accent-cyan); border:none; color:var(--color-bg); padding:var(--space-2) var(--space-4); border-radius:var(--radius-md); cursor:pointer; font-weight:500; font-family:var(--font-sans);">Refresh</button>
    </div>
    <div id="incidentsList" style="max-height:600px; overflow-y:auto;">
      <!-- Incidents will be loaded here -->
    </div>
  </div>
</div>

<!-- Met Office Weather Warnings Settings Modal -->
<div id="metOfficeSettingsModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.9); z-index:10000; overflow:auto;">
  <div style="position:relative; background:rgba(0,20,0,0.98); border:2px solid #00ff41; margin:50px auto; padding:20px; max-width:700px; border-radius:0; box-shadow:0 0 30px rgba(0,255,65,0.5); font-family:'Courier New',monospace;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; border-bottom:2px solid #00ff41; padding-bottom:10px;">
      <h2 style="color:#00ff41; margin:0; text-transform:uppercase; letter-spacing:2px; font-weight:700; text-shadow:0 0 10px rgba(0,255,65,0.5);">WEATHER WARNING ALERTS</h2>
      <button onclick="closeMetOfficeSettingsModal()" style="background:rgba(0,20,0,0.8); border:2px solid #00ff41; color:#00ff41; padding:8px 16px; border-radius:0; cursor:pointer; font-weight:700; font-family:'Courier New',monospace; text-transform:uppercase; letter-spacing:1px; text-shadow:0 0 5px rgba(0,255,65,0.5);" onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)';" onmouseout="this.style.backgroundColor='rgba(0,20,0,0.8)';">CLOSE</button>
    </div>
    
    <div style="margin-bottom:20px;">
      <label style="display:block; color:#e0e0e0; margin-bottom:8px; font-weight:600;">Alert Preferences</label>
      
      <div style="margin-bottom:16px; padding:12px; background:rgba(0,10,0,0.5); border:1px solid #00ff41; border-radius:0;">
        <label style="display:flex; align-items:center; color:#00ff41; cursor:pointer; margin-bottom:12px; font-family:'Courier New',monospace;">
          <input type="checkbox" id="easTonesEnabled" style="width:18px; height:18px; margin-right:8px; cursor:pointer; accent-color:#00ff41;">
          <span><strong style="text-transform:uppercase; letter-spacing:1px;">EAS TONES FOR CRITICAL (RED) WARNINGS</strong><br><small style="color:#00ff41; opacity:0.8;">Play Emergency Alert System tones when red warnings are issued</small></span>
        </label>
        
        <label style="display:flex; align-items:center; color:#00ff41; cursor:pointer; margin-bottom:12px; font-family:'Courier New',monospace;">
          <input type="checkbox" id="amberAlertsEnabled" style="width:18px; height:18px; margin-right:8px; cursor:pointer; accent-color:#00ff41;">
          <span><strong style="text-transform:uppercase; letter-spacing:1px;">ALERT FOR AMBER WARNINGS</strong><br><small style="color:#00ff41; opacity:0.8;">Show notifications for amber level warnings</small></span>
        </label>
        
        <label style="display:flex; align-items:center; color:#00ff41; cursor:pointer; margin-bottom:12px; font-family:'Courier New',monospace;">
          <input type="checkbox" id="yellowAlertsEnabled" style="width:18px; height:18px; margin-right:8px; cursor:pointer; accent-color:#00ff41;">
          <span><strong style="text-transform:uppercase; letter-spacing:1px;">ALERT FOR YELLOW WARNINGS</strong><br><small style="color:#00ff41; opacity:0.8;">Show notifications for yellow level warnings</small></span>
        </label>
        
        <label style="display:flex; align-items:center; color:#00ff41; cursor:pointer; font-family:'Courier New',monospace;">
          <input type="checkbox" id="repeatAlertsEnabled" style="width:18px; height:18px; margin-right:8px; cursor:pointer; accent-color:#00ff41;">
          <span><strong style="text-transform:uppercase; letter-spacing:1px;">REPEAT ALERTS</strong><br><small style="color:#00ff41; opacity:0.8;">Allow alerts to repeat if warning persists (default: alert once per warning)</small></span>
        </label>
      </div>
      
      <div style="margin-bottom:16px; padding:12px; background:rgba(0,10,0,0.5); border:1px solid #00ff41; border-radius:0;">
        <label style="display:block; color:#00ff41; margin-bottom:8px; font-weight:700; text-transform:uppercase; letter-spacing:1px; font-family:'Courier New',monospace;">EAS TONE VOLUME</label>
        <input type="range" id="easVolumeSlider" min="0" max="100" step="5" value="40" 
               style="width:100%; margin-bottom:8px; accent-color:#00ff41;">
        <div style="display:flex; justify-content:space-between; color:#00ff41; font-size:0.85em; font-family:'Courier New',monospace;">
          <span>0%</span>
          <span id="easVolumeValue" style="font-weight:700;">40%</span>
          <span>100%</span>
        </div>
        <small style="color:#00ff41; opacity:0.8; font-size:0.85em; display:block; margin-top:4px; font-family:'Courier New',monospace;">
          Adjust the volume of Emergency Alert System tones (853Hz + 960Hz)
        </small>
      </div>
      
      <div style="margin-bottom:16px; padding:12px; background:rgba(0,10,0,0.5); border:1px solid #00ff41; border-radius:0;">
        <label style="display:block; color:#00ff41; margin-bottom:8px; font-weight:700; text-transform:uppercase; letter-spacing:1px; font-family:'Courier New',monospace;">UPDATE FREQUENCY</label>
        <select id="updateFrequencySelect" style="width:100%; padding:8px; background:rgba(0,10,0,0.8); border:1px solid #00ff41; color:#00ff41; border-radius:0; font-size:0.9em; font-family:'Courier New',monospace; font-weight:600; cursor:pointer;">
          <option value="900">Every 15 minutes</option>
          <option value="1800" selected>Every 30 minutes (default)</option>
          <option value="3600">Every hour</option>
          <option value="7200">Every 2 hours</option>
        </select>
        <small style="color:#00ff41; opacity:0.8; font-size:0.85em; display:block; margin-top:4px; font-family:'Courier New',monospace;">
          How often to check for new weather warnings from Met Office
        </small>
      </div>
    </div>
    
    <div style="display:flex; gap:10px; margin-top:20px; border-top:2px solid #00ff41; padding-top:20px;">
      <button onclick="saveMetOfficeSettings()" style="flex:1; background:rgba(0,20,0,0.8); border:2px solid #00ff41; color:#00ff41; padding:12px; border-radius:0; cursor:pointer; font-weight:700; font-size:0.95em; font-family:'Courier New',monospace; text-transform:uppercase; letter-spacing:1px; text-shadow:0 0 5px rgba(0,255,65,0.5);" onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)'; this.style.boxShadow='0 0 10px rgba(0,255,65,0.5)';" onmouseout="this.style.backgroundColor='rgba(0,20,0,0.8)'; this.style.boxShadow='none';">SAVE SETTINGS</button>
      <button onclick="testEASTone()" style="background:rgba(0,20,0,0.8); border:2px solid #00ff41; color:#00ff41; padding:12px 20px; border-radius:0; cursor:pointer; font-weight:700; font-size:0.95em; font-family:'Courier New',monospace; text-transform:uppercase; letter-spacing:1px; text-shadow:0 0 5px rgba(0,255,65,0.5);" onmouseover="this.style.backgroundColor='rgba(0,255,65,0.2)'; this.style.boxShadow='0 0 10px rgba(0,255,65,0.5)';" onmouseout="this.style.backgroundColor='rgba(0,20,0,0.8)'; this.style.boxShadow='none';">TEST EAS TONE</button>
    </div>
    <div id="metOfficeSettingsStatus" style="margin-top:12px; padding:10px; border-radius:0; display:none; font-family:'Courier New',monospace; font-size:0.85em;"></div>
  </div>
</div>

<!-- APRS Configuration Modal -->
<div id="aprsConfigModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); z-index:10000; overflow:auto;">
  <div style="position:relative; background:#1a1a1a; border:1px solid #4a4a4a; margin:50px auto; padding:20px; max-width:700px; border-radius:4px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; border-bottom:1px solid #4a4a4a; padding-bottom:10px;">
      <h2 style="color:#ffb347; margin:0; text-transform:uppercase; letter-spacing:1px;">APRS Configuration</h2>
      <button onclick="closeAprsConfigModal()" style="background:#ff4444; border:none; color:#fff; padding:8px 16px; border-radius:3px; cursor:pointer; font-weight:600;">Close</button>
    </div>
    
    <div style="margin-bottom:20px;">
      <label style="display:block; color:#e0e0e0; margin-bottom:8px; font-weight:600;">APRS.fi API Key</label>
      <input type="password" id="aprsApiKey" placeholder="Enter your aprs.fi API key" 
             style="width:100%; padding:10px; background:#2a2a2a; border:1px solid #4a4a4a; color:#e0e0e0; border-radius:3px; font-size:0.9em; box-sizing:border-box;">
      <small style="color:#9a9a9a; font-size:0.85em; display:block; margin-top:4px;">
        Get your API key from <a href="https://aprs.fi/page/api" target="_blank" style="color:#4a9eff;">aprs.fi</a> (free account required)
      </small>
      <div id="aprsApiKeyStatus" style="margin-top:8px; padding:8px; border-radius:3px; display:none;"></div>
    </div>
    
    <div style="margin-bottom:20px;">
      <label style="display:block; color:#e0e0e0; margin-bottom:8px; font-weight:600;">Enable APRS Detection</label>
      <label style="display:flex; align-items:center; color:#e0e0e0; cursor:pointer;">
        <input type="checkbox" id="aprsEnabled" style="width:18px; height:18px; margin-right:8px; cursor:pointer; accent-color:#ff6600;">
        <span>Enable APRS station tracking</span>
      </label>
    </div>
    
    <div style="margin-bottom:20px; border-top:1px solid #4a4a4a; padding-top:20px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <label style="color:#e0e0e0; font-weight:600;">Callsigns to Track</label>
        <span id="aprsCallsignCount" style="color:#9a9a9a; font-size:0.85em;">0 callsigns</span>
      </div>
      <div style="display:flex; gap:8px; margin-bottom:12px;">
        <input type="text" id="newCallsign" placeholder="Enter callsign (e.g., N0CALL)" 
               style="flex:1; padding:8px; background:#2a2a2a; border:1px solid #4a4a4a; color:#e0e0e0; border-radius:3px; font-size:0.9em; text-transform:uppercase;"
               onkeypress="if(event.key==='Enter') addAprsCallsign()">
        <button onclick="addAprsCallsign()" style="background:#4a9eff; border:none; color:#fff; padding:8px 16px; border-radius:3px; cursor:pointer; font-weight:600; white-space:nowrap;">Add</button>
      </div>
      <div id="aprsCallsignList" style="max-height:300px; overflow-y:auto; border:1px solid #4a4a4a; border-radius:3px; background:#1f1f1f; padding:8px;">
        <div style="text-align:center; color:#9a9a9a; padding:20px;">No callsigns configured</div>
      </div>
      <small style="color:#9a9a9a; font-size:0.85em; display:block; margin-top:8px;">
        Add up to 20 callsigns to track. The APRS API supports querying multiple stations per request.
      </small>
    </div>
    
    <div style="display:flex; gap:10px; margin-top:20px; border-top:1px solid #4a4a4a; padding-top:20px;">
      <button onclick="saveAprsConfig()" style="flex:1; background:#4a9eff; border:none; color:#fff; padding:12px; border-radius:3px; cursor:pointer; font-weight:600; font-size:0.95em;">Save Configuration</button>
      <button onclick="testAprsConfig()" style="background:#2a2a2a; border:1px solid #4a4a4a; color:#e0e0e0; padding:12px 20px; border-radius:3px; cursor:pointer; font-weight:600; font-size:0.95em;">Test Connection</button>
    </div>
    <div id="aprsConfigStatus" style="margin-top:12px; padding:10px; border-radius:3px; display:none;"></div>
  </div>
</div>

<script>
  // Do not clear trackedPairs; persist across reloads
  // Track drones already alerted for no GPS
  const alertedNoGpsDrones = new Set();
  // Round tile positions to integer pixels to eliminate seams
  L.DomUtil.setPosition = (function() {
    var original = L.DomUtil.setPosition;
    return function(el, point) {
      var rounded = L.point(Math.round(point.x), Math.round(point.y));
      original.call(this, el, rounded);
    };
  })();

// --- Socket.IO real-time updates ---
const socket = io();

// On connect, optionally log or show status
socket.on('connected', function(data) {
  console.log(data.message);
});

// Listen for real-time detection events (single detection)
socket.on('detection', function(detection) {
  if (!window.tracked_pairs) window.tracked_pairs = {};
  window.tracked_pairs[detection.mac] = detection;
  localStorage.setItem("trackedPairs", JSON.stringify(window.tracked_pairs));
  updateComboList(window.tracked_pairs);
  updateAliases();
  // ... update markers, popups, etc. ...
});

// Listen for full detections state
socket.on('detections', function(allDetections) {
  window.tracked_pairs = allDetections;
  localStorage.setItem("trackedPairs", JSON.stringify(window.tracked_pairs));
  updateComboList(window.tracked_pairs);
  updateAliases();
  // ... update markers, popups, etc. ...
});

// Listen for real-time serial status events
socket.on('serial_status', function(statuses) {
  const statusDiv = document.getElementById('serialStatus');
  statusDiv.innerHTML = "";
  if (statuses) {
    for (const port in statuses) {
      const div = document.createElement("div");
      div.innerHTML = '<span class="usb-name">' + port + '</span>: ' +
        (statuses[port] ? '<span style="color: #4a9eff; font-weight:600;">● CONNECTED</span>' : '<span style="color: #ff4444; font-weight:600;">● DISCONNECTED</span>');
      statusDiv.appendChild(div);
    }
  }
});

// Listen for real-time aliases updates
socket.on('aliases', function(newAliases) {
  aliases = newAliases;
  updateComboList(window.tracked_pairs);
});

// Listen for real-time paths updates
socket.on('paths', function(paths) {
  // Update dronePaths and pilotPaths, redraw polylines, etc.
  // You may want to call restorePaths() or similar logic here
  // ...
});

// Listen for real-time cumulative log updates
socket.on('cumulative_log', function(log) {
  // Optionally update UI with new log data
  // ...
});

// Listen for real-time FAA cache updates
socket.on('faa_cache', function(faaCache) {
  // Optionally update UI with new FAA data
  // ...
});

// Listen for lightning alerts
socket.on('lightning_alert', function(alert) {
  if (audioAlertsEnabled) {
    // Play audible warning for lightning
    playLightningAlert();
    // Show toast notification
    showToast('⚡ Lightning Detected', 
      `Strike at ${alert.lat.toFixed(4)}, ${alert.lon.toFixed(4)} - ${alert.current.toFixed(1)}kA`, 
      'lightning');
  }
});

// ----------------------
// Weather Data Tracking
// ----------------------
let weatherMarkers = {};
let weatherVisible = true;
let webcamMarkers = {};
let webcamsVisible = true;

// ----------------------
// Maritime AIS Vessel Tracking
// ----------------------
let aisVesselMarkers = {};
let aisVesselsVisible = true;

// ----------------------
// ADSB Aircraft Tracking
// ----------------------
let adsbAircraftMarkers = {};
let adsbAircraftVisible = true;

// Listen for AIS vessel updates (bulk)
socket.on('ais_vessels', function(data) {
  console.log('Received ais_vessels event:', data);
  if (data && data.vessels) {
    console.log(`AIS vessels data received: ${data.vessels.length} vessels`);
    updateAisVessels(data.vessels);
  } else {
    console.log('No vessels data in event:', data);
    updateAisVessels([]);
  }
});

// Listen for individual AIS vessel updates (real-time)
socket.on('ais_vessel_update', function(vessel) {
  if (aisVesselsVisible && vessel && vessel.mmsi) {
    const mmsi = vessel.mmsi;
    const lat = vessel.lat;
    const lon = vessel.lon;
    
    if (!lat || !lon || lat === 0 || lon === 0) {
      return;
    }
    
    // Update or create marker
    if (aisVesselMarkers[mmsi]) {
      aisVesselMarkers[mmsi].setLatLng([lat, lon]);
      aisVesselMarkers[mmsi].setPopupContent(generateVesselPopup(vessel));
    } else {
      const icon = L.icon({
        iconUrl: 'data:image/svg+xml;base64,' + btoa(`
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
            <path fill="#4a9eff" d="M12 2L2 7v10c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V7l-10-5z"/>
            <path fill="#fff" d="M12 8v8M8 10h8"/>
          </svg>
        `),
        iconSize: [24, 24],
        iconAnchor: [12, 12],
        popupAnchor: [0, -12]
      });
      
      const marker = L.marker([lat, lon], { icon: icon })
        .bindPopup(generateVesselPopup(vessel))
        .addTo(map);
      
      aisVesselMarkers[mmsi] = marker;
    }
  }
});

function updateAisVessels(vessels) {
  if (!vessels || !Array.isArray(vessels)) {
    console.warn('Invalid AIS vessels data:', vessels);
    return;
  }
  
  if (!aisVesselsVisible) {
    // Clear all markers if layer is hidden
    Object.values(aisVesselMarkers).forEach(marker => {
      if (marker && map && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    });
    aisVesselMarkers = {};
    return;
  }
  
  if (!map) {
    console.warn('Map not initialized yet for AIS vessels');
    return;
  }
  
  console.log('Updating AIS vessels:', vessels.length, 'vessels');
  
  // Create a set of current MMSIs
  const currentMmsis = new Set(vessels.map(v => v.mmsi).filter(m => m));
  
  // Remove markers for vessels that are no longer in the data
  Object.keys(aisVesselMarkers).forEach(mmsi => {
    if (!currentMmsis.has(mmsi)) {
      const marker = aisVesselMarkers[mmsi];
      if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
      delete aisVesselMarkers[mmsi];
    }
  });
  
  // Add or update markers for current vessels
  vessels.forEach(vessel => {
    const mmsi = vessel.mmsi;
    const lat = vessel.lat;
    const lon = vessel.lon;
    
    if (!mmsi || !lat || !lon || lat === 0 || lon === 0 || isNaN(lat) || isNaN(lon)) {
      console.warn('Skipping invalid AIS vessel:', vessel);
      return; // Skip invalid coordinates
    }
    
    // Create or update marker
    if (aisVesselMarkers[mmsi]) {
      // Update existing marker position
      aisVesselMarkers[mmsi].setLatLng([lat, lon]);
      // Update popup content
      aisVesselMarkers[mmsi].setPopupContent(generateVesselPopup(vessel));
    } else {
      // Create new marker
      const icon = L.icon({
        iconUrl: 'data:image/svg+xml;base64,' + btoa(`
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
            <path fill="#4a9eff" d="M12 2L2 7v10c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V7l-10-5z"/>
            <path fill="#fff" d="M12 8v8M8 10h8"/>
          </svg>
        `),
        iconSize: [24, 24],
        iconAnchor: [12, 12],
        popupAnchor: [0, -12]
      });
      
      const marker = L.marker([lat, lon], { icon: icon })
        .bindPopup(generateVesselPopup(vessel))
        .addTo(map);
      
      aisVesselMarkers[mmsi] = marker;
      console.log('Added AIS vessel marker for', vessel.name || mmsi, 'at', lat, lon);
    }
  });
  
  // Update ships list
  updateShipsList(vessels);
}

// Update ships list in UI
function updateShipsList(vessels) {
  const placeholder = document.getElementById('shipsPlaceholder');
  if (!placeholder) {
    console.warn('shipsPlaceholder element not found');
    return;
  }
  
  placeholder.innerHTML = '';
  
  if (!vessels || vessels.length === 0) {
    placeholder.innerHTML = '<div style="color: #9DA3AD; font-size: 12px; padding: 8px; text-align: center;">No ships</div>';
    return;
  }
  
  console.log(`Updating ships list with ${vessels.length} vessels`);
  
  vessels.forEach(vessel => {
    const mmsi = vessel.mmsi;
    const name = vessel.name || `Vessel ${mmsi}`;
    const speed = vessel.speed !== undefined ? vessel.speed.toFixed(1) + ' kts' : 'Unknown';
    const vesselType = vessel.vessel_type || 'Unknown';
    
    const item = document.createElement('div');
    item.style.cssText = 'padding: 8px; margin: 4px 0; border: 1px solid #36C3FF; border-radius: 4px; cursor: pointer; background: rgba(54, 195, 255, 0.1);';
    item.innerHTML = `
      <div style="font-weight: 600; color: #36C3FF; margin-bottom: 4px;">${name}</div>
      <div style="font-size: 11px; color: #9DA3AD;">
        Type: ${vesselType} | Speed: ${speed}
      </div>
    `;
    
    item.addEventListener('click', () => {
      if (aisVesselMarkers[mmsi] && map) {
        map.setView([vessel.lat, vessel.lon], Math.max(map.getZoom(), 12));
        aisVesselMarkers[mmsi].openPopup();
      }
    });
    
    placeholder.appendChild(item);
  });
}

function generateVesselPopup(vessel) {
  let content = `<div style="min-width:200px;"><strong>🚢 ${vessel.name || 'Unknown Vessel'}</strong><br>`;
  content += `<small>MMSI: ${vessel.mmsi || 'N/A'}</small><br>`;
  
  if (vessel.vessel_type) {
    content += `Type: ${vessel.vessel_type}<br>`;
  }
  if (vessel.speed !== undefined) {
    content += `Speed: ${vessel.speed.toFixed(1)} knots<br>`;
  }
  if (vessel.course !== undefined) {
    content += `Course: ${vessel.course.toFixed(1)}°<br>`;
  }
  if (vessel.heading !== undefined) {
    content += `Heading: ${vessel.heading}°<br>`;
  }
  if (vessel.lat && vessel.lon) {
    content += `<br><small>Position: ${vessel.lat.toFixed(4)}, ${vessel.lon.toFixed(4)}</small><br>`;
    content += `<a target="_blank" href="https://www.google.com/maps/search/?api=1&query=${vessel.lat},${vessel.lon}">View on Google Maps</a>`;
  }
  
  content += '</div>';
  return content;
}

// ----------------------
// Met Office Weather Warnings Tracking
// ----------------------
let metofficeWarningMarkers = {};
let metofficeWarningPolygons = {};  // Store polygon layers for map display
let metofficeWarningsVisible = true;
let alertedWarningIds = new Set(); // Track which warnings we've already alerted for

// Initialize Met Office alert settings from localStorage or defaults
let metofficeAlertSettings = {
  eas_tones_enabled: localStorage.getItem('metOfficeEasTonesEnabled') !== 'false',
  amber_alerts_enabled: localStorage.getItem('metOfficeAmberAlertsEnabled') === 'true',
  yellow_alerts_enabled: localStorage.getItem('metOfficeYellowAlertsEnabled') === 'true',
  repeat_alerts_enabled: localStorage.getItem('metOfficeRepeatAlertsEnabled') === 'true',
  eas_volume: parseInt(localStorage.getItem('metOfficeEasVolume') || '40'),
  update_frequency: parseInt(localStorage.getItem('metOfficeUpdateFrequency') || '1800')
};

// Listen for Met Office warnings updates
socket.on('metoffice_warnings', function(data) {
  updateMetOfficeWarnings(data.warnings || []);
});

function updateMetOfficeWarnings(warnings) {
  if (!warnings || !Array.isArray(warnings)) {
    console.warn('Invalid Met Office warnings data:', warnings);
    return;
  }
  
  if (!map) {
    console.warn('Map not initialized yet for Met Office warnings');
    return;
  }
  
  console.log('Updating Met Office warnings:', warnings.length, 'warnings');
  
  // Create a set of current warning IDs
  const currentWarningIds = new Set(warnings.map(w => w.id).filter(id => id));
  
  // Remove polygons and markers for warnings that are no longer active
  Object.keys(metofficeWarningPolygons).forEach(warningId => {
    if (!currentWarningIds.has(warningId)) {
      // Remove polygon layers
      const polygons = metofficeWarningPolygons[warningId];
      if (polygons && Array.isArray(polygons)) {
        polygons.forEach(polygon => {
          if (polygon && map.hasLayer(polygon)) {
            map.removeLayer(polygon);
          }
        });
      }
      delete metofficeWarningPolygons[warningId];
    }
  });
  
  Object.keys(metofficeWarningMarkers).forEach(warningId => {
    if (!currentWarningIds.has(warningId)) {
      const marker = metofficeWarningMarkers[warningId];
      if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
      delete metofficeWarningMarkers[warningId];
    }
  });
  
  // Clear everything if warnings are hidden
  if (!metofficeWarningsVisible) {
    return;
  }
  
  // Add or update polygons and markers for current warnings
  warnings.forEach(warning => {
    const warningId = warning.id;
    if (!warningId) {
      console.warn('Skipping warning without ID:', warning);
      return;
    }
    
    // Check if this warning needs an alert based on settings
    const isCritical = warning.level === 'red';
    const isAmber = warning.level === 'amber';
    const isYellow = warning.level === 'yellow';
    const isNewWarning = !metofficeWarningPolygons[warningId] && !metofficeWarningMarkers[warningId];
    const shouldAlert = (isCritical && metofficeAlertSettings.eas_tones_enabled) ||
                        (isAmber && metofficeAlertSettings.amber_alerts_enabled) ||
                        (isYellow && metofficeAlertSettings.yellow_alerts_enabled);
    const hasAlerted = alertedWarningIds.has(warningId);
    const canAlert = metofficeAlertSettings.repeat_alerts_enabled || !hasAlerted;
    
    if (shouldAlert && isNewWarning && canAlert) {
      // Play EAS tone for critical warnings if enabled
      if (isCritical && metofficeAlertSettings.eas_tones_enabled) {
        playEASTone();
      }
      
      alertedWarningIds.add(warningId);
      
      // Show toast notification
      const emoji = isCritical ? '🔴' : isAmber ? '🟠' : '🟡';
      const levelText = isCritical ? 'CRITICAL' : isAmber ? 'AMBER' : 'YELLOW';
      showToast(`${emoji} ${levelText} WEATHER WARNING`, 
        `${warning.title || levelText + ' Warning'} - ${warning.weather_type || 'Severe Weather'}`, 
        isCritical ? 'critical' : 'warning');
    }
    
    // Define colors based on warning level
    const levelColors = {
      'red': '#E24A4A',      // Critical red from design system
      'amber': '#F5C542',    // Amber from design system  
      'yellow': '#FFD700'    // Yellow
    };
    const color = levelColors[warning.level] || '#FFD700';
    
    // Draw polygons and polylines if available
    const polygons = warning.polygons || [];
    if (polygons && polygons.length > 0) {
      // Remove existing polygons for this warning
      if (metofficeWarningPolygons[warningId]) {
        metofficeWarningPolygons[warningId].forEach(polygon => {
          if (polygon && map.hasLayer(polygon)) {
            map.removeLayer(polygon);
          }
        });
      }
      
      // Helper function to calculate center from polygon coordinates
      function calculatePolygonCenter(coords) {
        if (!coords || coords.length === 0) return null;
        let sumLat = 0, sumLon = 0, count = 0;
        coords.forEach(coord => {
          if (coord && coord.length >= 2) {
            sumLat += coord[0];
            sumLon += coord[1];
            count++;
          }
        });
        return count > 0 ? [sumLat / count, sumLon / count] : null;
      }
      
      // Create polygon layers and polylines for each polygon in the warning
      const polygonLayers = [];
      let centerCalculated = null;
      
      polygons.forEach((polygonCoords, index) => {
        if (polygonCoords && polygonCoords.length >= 3) {
          try {
            // Calculate center from first polygon for marker/popup positioning
            if (!centerCalculated) {
              centerCalculated = calculatePolygonCenter(polygonCoords);
            }
            
            // Draw filled polygon
            const polygon = L.polygon(polygonCoords, {
              color: color,
              fillColor: color,
              fillOpacity: 0.2,
              weight: 2,
              opacity: 0.6
            });
            
            // Draw prominent polyline boundary for visibility
            const polyline = L.polyline(polygonCoords, {
              color: color,
              weight: 4,
              opacity: 0.9,
              fill: false
            });
            
            // Bind popup to first polygon's polyline
            if (index === 0) {
              polyline.bindPopup(generateWarningPopup(warning));
            }
            
            if (metofficeWarningsVisible) {
              polygon.addTo(map);
              polyline.addTo(map);
              polyline.bringToFront();
            }
            
            polygonLayers.push(polygon);
            polygonLayers.push(polyline);
          } catch (e) {
            console.error(`Error drawing polygon ${index} for warning ${warningId}:`, e, polygonCoords);
          }
        }
      });
      
      metofficeWarningPolygons[warningId] = polygonLayers;
      console.log(`Added ${polygonLayers.length} polygon/polyline layers for Met Office warning: ${warning.title}`);
    } else {
      // Fallback: Try to calculate center from affected areas or use a reasonable default
      let fallbackCenter = [55.5, -4.0]; // Central Scotland as default
      
      // Try to extract location hint from title or affected_areas
      if (warning.affected_areas && warning.affected_areas.length > 0) {
        const areaText = warning.affected_areas[0].toLowerCase();
        // Rough location hints for UK regions
        if (areaText.includes('strathclyde') || areaText.includes('glasgow') || areaText.includes('scotland')) {
          fallbackCenter = [55.5, -4.5]; // Central Scotland
        } else if (areaText.includes('england') || areaText.includes('london')) {
          fallbackCenter = [52.0, -1.0]; // Central England
        } else if (areaText.includes('wales')) {
          fallbackCenter = [52.5, -3.5]; // Central Wales
        } else if (areaText.includes('northern ireland')) {
          fallbackCenter = [54.5, -6.0]; // Northern Ireland
        }
      }
      
      if (metofficeWarningMarkers[warningId]) {
        // Update existing marker location and popup content
        metofficeWarningMarkers[warningId].setLatLng(fallbackCenter);
        metofficeWarningMarkers[warningId].setPopupContent(generateWarningPopup(warning));
      } else {
        const icon = L.divIcon({
          className: 'metoffice-warning-icon',
          html: `<div style="background-color:${color}; width:24px; height:24px; border-radius:50%; border:2px solid #fff; box-shadow:0 0 10px ${color};"></div>`,
          iconSize: [24, 24],
          iconAnchor: [12, 12],
          popupAnchor: [0, -12]
        });
        
        const marker = L.marker(fallbackCenter, { icon: icon })
          .bindPopup(generateWarningPopup(warning));
        
        if (metofficeWarningsVisible) {
          marker.addTo(map);
        }
        
        metofficeWarningMarkers[warningId] = marker;
        console.log('Added Met Office warning marker (no polygons):', warning.title, 'at', fallbackCenter);
      }
    }
  });
  
  // Clean up alerted IDs for warnings that no longer exist
  alertedWarningIds.forEach(warningId => {
    if (!currentWarningIds.has(warningId)) {
      alertedWarningIds.delete(warningId);
    }
  });
}

function generateWarningPopup(warning) {
  const levelColors = {
    'red': '#E24A4A',
    'amber': '#F5C542',
    'yellow': '#FFD700'
  };
  const color = levelColors[warning.level] || '#FFD700';
  const levelNames = {
    'red': 'RED',
    'amber': 'AMBER',
    'yellow': 'YELLOW'
  };
  const levelName = levelNames[warning.level] || 'UNKNOWN';
  const weatherType = (warning.weather_type || 'unknown').toUpperCase();
  
  // Format dates if available
  let dateInfo = '';
  if (warning.start_time || warning.end_time) {
    const formatDate = (timestamp) => {
      if (!timestamp) return '';
      const d = new Date(timestamp * 1000);
      return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) + 
             ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    };
    const start = formatDate(warning.start_time);
    const end = formatDate(warning.end_time);
    if (start && end) {
      dateInfo = `<div style="margin:8px 0; padding:6px; background:#1F2329; border-left:3px solid ${color}; font-size:0.9em;">
        <strong>Valid:</strong> ${start} - ${end}
      </div>`;
    } else if (start) {
      dateInfo = `<div style="margin:8px 0; padding:6px; background:#1F2329; border-left:3px solid ${color}; font-size:0.9em;">
        <strong>Valid from:</strong> ${start}
      </div>`;
    }
  }
  
  let content = `<div style="min-width:300px; max-width:400px; font-family:Inter,sans-serif; color:#D6DAE0;">
    <div style="background-color:${color}; color:#0F1215; padding:10px 12px; margin:-10px -10px 12px -10px; font-weight:700; text-align:center; text-transform:uppercase; letter-spacing:1px; font-size:13px;">
      ${levelName} WARNING - ${weatherType}
    </div>
    
    <div style="margin-bottom:12px;">
      <strong style="color:#D6DAE0; font-size:15px;">${warning.title || 'Weather Warning'}</strong>
    </div>`;
  
  if (dateInfo) {
    content += dateInfo;
  }
  
  if (warning.description) {
    // Clean up description HTML and extract meaningful text
    let desc = warning.description.replace(/<[^>]*>/g, '').trim();
    // Remove repeated title text if it appears in description
    if (warning.title) {
      desc = desc.replace(new RegExp(warning.title, 'gi'), '').trim();
    }
    if (desc && desc.length > 0) {
      desc = desc.substring(0, 300);
      content += `<div style="max-height:150px; overflow-y:auto; margin:10px 0; padding:8px; background:#1F2329; border-radius:4px; font-size:14px; line-height:1.5; color:#D6DAE0;">
        ${desc}${desc.length === 300 ? '...' : ''}
      </div>`;
    }
  }
  
  if (warning.affected_areas && warning.affected_areas.length > 0) {
    // Filter out duplicate/irrelevant area entries
    const uniqueAreas = [];
    const seenAreas = new Set();
    warning.affected_areas.forEach(area => {
      const cleanArea = area.trim();
      // Skip if it's just a repeat of the title or too short
      if (cleanArea.length > 3 && 
          !cleanArea.toLowerCase().includes(warning.title?.toLowerCase() || '') &&
          !seenAreas.has(cleanArea.toLowerCase())) {
        uniqueAreas.push(cleanArea);
        seenAreas.add(cleanArea.toLowerCase());
      }
    });
    
    if (uniqueAreas.length > 0) {
      content += `<div style="margin-top:12px;">
        <strong style="color:#D6DAE0; font-size:13px; text-transform:uppercase; letter-spacing:0.5px;">Affected Areas:</strong>
        <div style="max-height:120px; overflow-y:auto; margin-top:6px; padding:8px; background:#15191E; border-radius:4px; font-size:13px; line-height:1.6;">
      `;
      uniqueAreas.slice(0, 8).forEach(area => {
        content += `<div style="margin:4px 0; color:#9DA3AD;">• ${area}</div>`;
      });
      if (uniqueAreas.length > 8) {
        content += `<div style="margin-top:6px; color:#9DA3AD; font-style:italic;">... and ${uniqueAreas.length - 8} more area${uniqueAreas.length - 8 > 1 ? 's' : ''}</div>`;
      }
      content += `</div></div>`;
    }
  }
  
  if (warning.published) {
    try {
      const pubDate = new Date(warning.published);
      if (!isNaN(pubDate.getTime())) {
        const formattedDate = pubDate.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        content += `<div style="margin-top:12px; padding-top:8px; border-top:1px solid #2A2F36;">
          <small style="color:#9DA3AD; font-size:11px;">Published: ${formattedDate}</small>
        </div>`;
      }
    } catch (e) {
      // Skip date formatting if it fails
    }
  }
  
  if (warning.link) {
    content += `<div style="margin-top:10px; text-align:center;">
      <a href="${warning.link}" target="_blank" style="color:#36C3FF; text-decoration:none; font-size:13px; font-weight:500; padding:6px 12px; border:1px solid #36C3FF; border-radius:4px; display:inline-block;">
        View on Met Office website →
      </a>
    </div>`;
  }
  
  content += '</div>';
  return content;
}

function toggleMetOfficeWarnings() {
  metofficeWarningsVisible = !metofficeWarningsVisible;
  const btn = document.getElementById('toggleMetOfficeButton');
  
  if (metofficeWarningsVisible) {
    // Show warnings (polygons and markers)
    Object.values(metofficeWarningPolygons).forEach(polygonArray => {
      if (polygonArray && Array.isArray(polygonArray)) {
        polygonArray.forEach(polygon => {
          if (polygon && !map.hasLayer(polygon)) {
            polygon.addTo(map);
          }
        });
      }
    });
    Object.values(metofficeWarningMarkers).forEach(marker => {
      if (marker && !map.hasLayer(marker)) {
        marker.addTo(map);
      }
    });
    if (btn) {
      btn.textContent = 'Hide Warnings';
      btn.style.backgroundColor = 'var(--accent-cyan)';
      btn.style.color = 'var(--color-bg)';
    }
    // Fetch latest warnings
    fetch('/api/metoffice_warnings_update', { method: 'POST' })
      .catch(err => console.error('Error updating Met Office warnings:', err));
  } else {
    // Hide warnings
    Object.values(metofficeWarningPolygons).forEach(polygonArray => {
      if (polygonArray && Array.isArray(polygonArray)) {
        polygonArray.forEach(polygon => {
          if (polygon && map.hasLayer(polygon)) {
            map.removeLayer(polygon);
          }
        });
      }
    });
    Object.values(metofficeWarningMarkers).forEach(marker => {
      if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    });
    if (btn) {
      btn.textContent = 'Show Warnings';
      btn.style.backgroundColor = 'var(--color-text-dim)';
      btn.style.color = 'var(--color-text)';
    }
  }
  
  // Update enabled state on server
  fetch('/api/metoffice_warnings_detection', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: metofficeWarningsVisible })
  }).catch(err => console.error('Error updating Met Office warnings state:', err));
}

function toggleAisVessels() {
  aisVesselsVisible = !aisVesselsVisible;
  const btn = document.getElementById('toggleAisButton');
  if (btn) {
    btn.textContent = aisVesselsVisible ? 'Hide Vessels' : 'Show Vessels';
    btn.style.backgroundColor = aisVesselsVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
    btn.style.color = aisVesselsVisible ? 'var(--color-bg)' : 'var(--color-text)';
  }
  
  // Update visibility
  Object.values(aisVesselMarkers).forEach(marker => {
    if (aisVesselsVisible) {
      if (!map.hasLayer(marker)) {
        marker.addTo(map);
      }
    } else {
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    }
  });
  
  // Fetch current vessels if enabling
  if (aisVesselsVisible) {
    fetch('/api/ais_vessels')
      .then(res => res.json())
      .then(data => {
        if (data.vessels) {
          updateAisVessels(data.vessels);
        }
      })
      .catch(err => console.error('Error fetching AIS vessels:', err));
  }
}

// Listen for ADSB aircraft updates (bulk)
socket.on('adsb_aircraft', function(data) {
  if (data && data.aircraft) {
    console.log(`ADSB update: ${data.aircraft.length} aircraft received`);
    updateAdsbAircraft(data.aircraft);
  } else {
    console.log('ADSB update: No aircraft data');
    updateAdsbAircraft([]);
  }
});

function updateAdsbAircraft(aircraftList) {
  if (!adsbAircraftVisible) {
    // Remove all markers if layer is hidden
    Object.values(adsbAircraftMarkers).forEach(marker => {
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    });
    adsbAircraftMarkers = {};
    return;
  }
  
  if (!aircraftList || aircraftList.length === 0) {
    // Clear all markers if no aircraft
    Object.keys(adsbAircraftMarkers).forEach(hex => {
      if (map.hasLayer(adsbAircraftMarkers[hex])) {
        map.removeLayer(adsbAircraftMarkers[hex]);
      }
      delete adsbAircraftMarkers[hex];
    });
    updateAircraftList([]);
    return;
  }
  
  // Create a set of current aircraft hex codes
  const currentHexCodes = new Set(aircraftList.map(a => a.hex).filter(Boolean));
  
  // Remove markers for aircraft that are no longer present
  Object.keys(adsbAircraftMarkers).forEach(hex => {
    if (!currentHexCodes.has(hex)) {
      const marker = adsbAircraftMarkers[hex];
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
      delete adsbAircraftMarkers[hex];
    }
  });
  
  // Add or update markers for current aircraft
  aircraftList.forEach(aircraft => {
    const hex = aircraft.hex;
    const lat = aircraft.lat;
    const lon = aircraft.lon;
    
    if (!hex || !lat || !lon || lat === 0 || lon === 0) {
      return;
    }
    
    // Update existing marker or create new one
    if (adsbAircraftMarkers[hex]) {
      // Get current position
      const currentPos = adsbAircraftMarkers[hex].getLatLng();
      const newPos = [lat, lon];
      
      // Only update if position actually changed (avoid unnecessary updates)
      if (Math.abs(currentPos.lat - lat) > 0.0001 || Math.abs(currentPos.lng - lon) > 0.0001) {
        adsbAircraftMarkers[hex].setLatLng(newPos);
      }
      
      // Update icon rotation if track angle changed
      const trackAngle = aircraft.track || 0;
      const aircraftIcon = L.divIcon({
        className: 'adsb-aircraft-marker',
        html: `<div style="width: 24px; height: 24px; font-size: 20px; text-align: center; line-height: 24px; transform: rotate(${trackAngle}deg); filter: drop-shadow(0 0 2px rgba(245, 197, 66, 0.8));">✈️</div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
      });
      adsbAircraftMarkers[hex].setIcon(aircraftIcon);
      
      // Update popup content
      const callsign = aircraft.callsign || aircraft.registration || hex;
      const altitude = aircraft.altitude_ft || aircraft.altitude_baro || 'Unknown';
      const speed = aircraft.speed_kts ? aircraft.speed_kts.toFixed(0) + ' kts' : 'Unknown';
      const aircraftType = aircraft.aircraft_type || 'Unknown';
      
      adsbAircraftMarkers[hex].setPopupContent(`
        <div style="font-family: 'Courier New', monospace; font-size: 12px;">
          <strong style="color: #F5C542;">Aircraft: ${callsign}</strong><br>
          Type: ${aircraftType}<br>
          Altitude: ${altitude} ft<br>
          Speed: ${speed}<br>
          Track: ${(aircraft.track || 0).toFixed(0)}°<br>
          Hex: ${hex}
        </div>
      `);
    } else {
      // Create new marker
      const trackAngle = aircraft.track || 0;
      const aircraftIcon = L.divIcon({
        className: 'adsb-aircraft-marker',
        html: `<div style="width: 24px; height: 24px; font-size: 20px; text-align: center; line-height: 24px; transform: rotate(${trackAngle}deg); filter: drop-shadow(0 0 2px rgba(245, 197, 66, 0.8));">✈️</div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
      });
      
      // Create marker
      const marker = L.marker([lat, lon], { icon: aircraftIcon });
      
      // Create popup
      const callsign = aircraft.callsign || aircraft.registration || hex;
      const altitude = aircraft.altitude_ft || aircraft.altitude_baro || 'Unknown';
      const speed = aircraft.speed_kts ? aircraft.speed_kts.toFixed(0) + ' kts' : 'Unknown';
      const aircraftType = aircraft.aircraft_type || 'Unknown';
      
      marker.bindPopup(`
        <div style="font-family: 'Courier New', monospace; font-size: 12px;">
          <strong style="color: #F5C542;">Aircraft: ${callsign}</strong><br>
          Type: ${aircraftType}<br>
          Altitude: ${altitude} ft<br>
          Speed: ${speed}<br>
          Track: ${(aircraft.track || 0).toFixed(0)}°<br>
          Hex: ${hex}
        </div>
      `);
      
      // Add to map
      marker.addTo(map);
      adsbAircraftMarkers[hex] = marker;
    }
  });
  
  // Update aircraft list
  updateAircraftList(aircraftList);
}

// Update aircraft list in UI
function updateAircraftList(aircraftList) {
  const placeholder = document.getElementById('aircraftPlaceholder');
  if (!placeholder) {
    console.warn('aircraftPlaceholder element not found');
    return;
  }
  
  placeholder.innerHTML = '';
  
  if (!aircraftList || aircraftList.length === 0) {
    placeholder.innerHTML = '<div style="color: #9DA3AD; font-size: 12px; padding: 8px; text-align: center;">No aircraft</div>';
    return;
  }
  
  console.log(`Updating aircraft list with ${aircraftList.length} aircraft`);
  
  aircraftList.forEach(aircraft => {
    const hex = aircraft.hex;
    const callsign = aircraft.callsign || aircraft.registration || hex;
    const altitude = aircraft.altitude_ft || aircraft.altitude_baro || 'Unknown';
    const speed = aircraft.speed_kts ? aircraft.speed_kts.toFixed(0) + ' kts' : 'Unknown';
    
    const item = document.createElement('div');
    item.style.cssText = 'padding: 8px; margin: 4px 0; border: 1px solid #36C3FF; border-radius: 4px; cursor: pointer; background: rgba(54, 195, 255, 0.1);';
    item.innerHTML = `
      <div style="font-weight: 600; color: #36C3FF; margin-bottom: 4px;">${callsign}</div>
      <div style="font-size: 11px; color: #9DA3AD;">
        Alt: ${altitude} ft | Speed: ${speed}
      </div>
    `;
    
    item.addEventListener('click', () => {
      if (adsbAircraftMarkers[hex] && map) {
        map.setView([aircraft.lat, aircraft.lon], Math.max(map.getZoom(), 12));
        adsbAircraftMarkers[hex].openPopup();
      }
    });
    
    placeholder.appendChild(item);
  });
}

function toggleAdsbAircraft() {
  adsbAircraftVisible = !adsbAircraftVisible;
  const btn = document.getElementById('toggleAdsbButton');
  if (btn) {
    btn.textContent = adsbAircraftVisible ? 'Hide Aircraft' : 'Show Aircraft';
    btn.style.backgroundColor = adsbAircraftVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
    btn.style.color = adsbAircraftVisible ? 'var(--color-bg)' : 'var(--color-text)';
  }
  
  // Update visibility
  Object.values(adsbAircraftMarkers).forEach(marker => {
    if (adsbAircraftVisible) {
      if (!map.hasLayer(marker)) {
        marker.addTo(map);
      }
    } else {
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    }
  });
  
  // Fetch current aircraft if enabling
  if (adsbAircraftVisible) {
    fetch('/api/adsb_aircraft')
      .then(res => res.json())
      .then(data => {
        if (data.aircraft) {
          updateAdsbAircraft(data.aircraft);
        }
      })
      .catch(err => console.error('Error fetching ADSB aircraft:', err));
  }
}

// Load AIS vessels on page load (with delay to ensure map is initialized)
window.addEventListener('load', function() {
  setTimeout(() => {
    // Check if AIS detection is enabled
    fetch('/api/ais_detection')
      .then(res => res.json())
      .then(data => {
        if (data.enabled) {
          // Fetch initial vessel data
          fetch('/api/ais_vessels')
            .then(res => res.json())
            .then(data => {
              if (data.vessels && data.vessels.length > 0) {
                console.log('Loading AIS vessels:', data.vessels.length, 'vessels');
                updateAisVessels(data.vessels);
              } else {
                console.log('No AIS vessels found');
              }
            })
            .catch(err => console.error('Error fetching AIS vessels:', err));
        }
      })
      .catch(err => console.error('Error checking AIS status:', err));
  }, 1000);
  
  // Load APRS stations on page load
  fetch('/api/aprs_detection')
    .then(res => res.json())
    .then(data => {
      if (data.enabled) {
        fetch('/api/aprs_stations')
          .then(res => res.json())
          .then(data => {
            if (data.stations) {
              updateAprsStations(data.stations);
            }
          })
          .catch(err => console.error('Error fetching APRS stations:', err));
      }
    })
    .catch(err => console.error('Error checking APRS status:', err));
  
  // Load ADSB aircraft on page load
  setTimeout(() => {
    fetch('/api/adsb_detection')
      .then(res => res.json())
      .then(data => {
        if (data.enabled) {
          fetch('/api/adsb_aircraft')
            .then(res => res.json())
            .then(data => {
              if (data.aircraft && data.aircraft.length > 0) {
                console.log('Loading ADSB aircraft:', data.aircraft.length, 'aircraft');
                updateAdsbAircraft(data.aircraft);
              } else {
                console.log('No ADSB aircraft found');
              }
            })
            .catch(err => console.error('Error fetching ADSB aircraft:', err));
        }
      })
      .catch(err => console.error('Error checking ADSB status:', err));
  }, 1500);
  
  // Load all recent data from database for fast page load
  setTimeout(() => {
    fetch('/api/recent_data')
      .then(res => res.json())
      .then(data => {
        if (data.status === 'ok') {
          console.log('Loading recent data from database:', data.counts);
          
          // Load detections
          if (data.detections && Object.keys(data.detections).length > 0) {
            // Update tracked_pairs with database data
            Object.assign(tracked_pairs, data.detections);
            emit_detections();
            console.log('Loaded', Object.keys(data.detections).length, 'detections from database');
          }
          
          // Load AIS vessels
          if (data.ais_vessels && data.ais_vessels.length > 0) {
            updateAisVessels(data.ais_vessels);
            console.log('Loaded', data.ais_vessels.length, 'AIS vessels from database');
          }
          
          // Load weather
          if (data.weather && Object.keys(data.weather).length > 0) {
            updateWeatherMarkers(data.weather);
            console.log('Loaded', Object.keys(data.weather).length, 'weather locations from database');
          }
          
          // Load webcams (when implemented)
          if (data.webcams && data.webcams.length > 0) {
            console.log('Loaded', data.webcams.length, 'webcams from database');
            // updateWebcams(data.webcams); // Will implement when webcam display is added
          }
          
          // Load APRS stations
          if (data.aprs_stations && data.aprs_stations.length > 0) {
            updateAprsStations(data.aprs_stations);
            console.log('Loaded', data.aprs_stations.length, 'APRS stations from database');
          }
        }
      })
      .catch(err => console.error('Error loading recent data:', err));
  }, 1000);
});

// ----------------------
// Weather Data Display
// ----------------------

// Listen for weather data updates
socket.on('weather_data', function(data) {
  if (weatherVisible && data.weather) {
    updateWeatherMarkers(data.weather);
  }
});

socket.on('webcams_data', function(data) {
  console.log('Received webcams_data event:', data);
  if (data && data.webcams) {
    const webcamCount = Object.keys(data.webcams).length;
    console.log(`Webcams data received: ${webcamCount} webcams`);
    if (webcamsVisible) {
      updateWebcamMarkers(data.webcams);
    } else {
      console.log('Webcams layer is hidden');
    }
  } else {
    console.log('No webcams data in event:', data);
  }
});

function generateWeatherPopup(weatherData) {
  const loc = weatherData.location || {};
  const name = loc.name || `${loc.lat},${loc.lon}`;
  const units = weatherData.units || {};
  
  // Get current weather (first timestamp)
  const ts = weatherData.ts || [];
  if (ts.length === 0) return `<b>${name}</b><br>No weather data`;
  
  const currentIdx = 0;
  const currentTime = new Date(ts[currentIdx]);
  
  // Extract weather parameters
  const temp = weatherData['temp-surface'] ? weatherData['temp-surface'][currentIdx] : null;
  const windU = weatherData['wind_u-surface'] ? weatherData['wind_u-surface'][currentIdx] : null;
  const windV = weatherData['wind_v-surface'] ? weatherData['wind_v-surface'][currentIdx] : null;
  const windGust = weatherData['gust-surface'] ? weatherData['gust-surface'][currentIdx] : null;
  const pressure = weatherData['pressure-surface'] ? weatherData['pressure-surface'][currentIdx] : null;
  const precip = weatherData['past3hprecip-surface'] ? weatherData['past3hprecip-surface'][currentIdx] : null;
  const rh = weatherData['rh-surface'] ? weatherData['rh-surface'][currentIdx] : null;
  
  // Calculate wind speed and direction
  let windSpeed = null;
  let windDir = null;
  if (windU !== null && windV !== null) {
    windSpeed = Math.sqrt(windU * windU + windV * windV);
    windDir = (Math.atan2(windU, windV) * 180 / Math.PI + 360) % 360;
  }
  
  let html = `<b>${name}</b><br>`;
  html += `<small>${currentTime.toLocaleString()}</small><br><hr>`;
  
  if (temp !== null) {
    const tempUnit = units['temp-surface'] || '°C';
    html += `<b>Temperature:</b> ${temp.toFixed(1)} ${tempUnit}<br>`;
  }
  
  if (windSpeed !== null) {
    const windUnit = units['wind_u-surface'] || 'm/s';
    const dirStr = windDir !== null ? ` (${Math.round(windDir)}°)` : '';
    html += `<b>Wind:</b> ${windSpeed.toFixed(1)} ${windUnit}${dirStr}<br>`;
  }
  
  if (windGust !== null) {
    const gustUnit = units['gust-surface'] || 'm/s';
    html += `<b>Gusts:</b> ${windGust.toFixed(1)} ${gustUnit}<br>`;
  }
  
  if (pressure !== null) {
    const pressUnit = units['pressure-surface'] || 'Pa';
    html += `<b>Pressure:</b> ${pressure.toFixed(0)} ${pressUnit}<br>`;
  }
  
  if (rh !== null) {
    html += `<b>Humidity:</b> ${rh.toFixed(0)}%<br>`;
  }
  
  if (precip !== null) {
    const precipUnit = units['past3hprecip-surface'] || 'mm';
    html += `<b>Precip (3h):</b> ${precip.toFixed(1)} ${precipUnit}<br>`;
  }
  
  return html;
}

function updateWeatherMarkers(weatherData) {
  if (!weatherData || typeof weatherData !== 'object') {
    console.warn('Invalid weather data:', weatherData);
    return;
  }
  
  if (!weatherVisible) {
    // Remove all markers if weather is hidden
    Object.values(weatherMarkers).forEach(marker => {
      if (map && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    });
    weatherMarkers = {};
    return;
  }
  
  if (!map) {
    console.warn('Map not initialized yet');
    return;
  }
  
  // Get current location keys
  const currentKeys = Object.keys(weatherData);
  console.log('Updating weather markers for', currentKeys.length, 'locations');
  
  // Remove markers for locations that no longer have data
  Object.keys(weatherMarkers).forEach(key => {
    if (!currentKeys.includes(key)) {
      const marker = weatherMarkers[key];
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
      delete weatherMarkers[key];
    }
  });
  
  // Add or update markers for each location
  Object.keys(weatherData).forEach(key => {
    const data = weatherData[key];
    if (!data) return;
    
    const loc = data.location || {};
    const lat = loc.lat;
    const lon = loc.lon;
    
    if (!lat || !lon || isNaN(lat) || isNaN(lon)) {
      console.warn('Invalid coordinates for weather location:', key, lat, lon);
      return;
    }
    
    // Create or update marker
    if (weatherMarkers[key]) {
      weatherMarkers[key].setLatLng([lat, lon]);
      weatherMarkers[key].setPopupContent(generateWeatherPopup(data));
    } else {
      // Create weather icon (cloud icon)
      const weatherIcon = L.divIcon({
        className: 'weather-marker',
        html: '<div style="background-color: #90ee90; border: 2px solid #1a1a1a; border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-size: 12px;">☁</div>',
        iconSize: [20, 20],
        iconAnchor: [10, 10]
      });
      
      const marker = L.marker([lat, lon], { icon: weatherIcon })
        .bindPopup(generateWeatherPopup(data));
      
      marker.addTo(map);
      weatherMarkers[key] = marker;
      console.log('Added weather marker for', loc.name || key, 'at', lat, lon);
    }
  });
}

function toggleWeather() {
  weatherVisible = !weatherVisible;
  const btn = document.getElementById('toggleWeatherButton');
  if (btn) {
    btn.textContent = weatherVisible ? 'Hide Weather' : 'Show Weather';
    btn.style.backgroundColor = weatherVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
    btn.style.color = weatherVisible ? 'var(--color-bg)' : 'var(--color-text)';
  }
  
  // Update visibility
  Object.values(weatherMarkers).forEach(marker => {
    if (weatherVisible) {
      if (!map.hasLayer(marker)) {
        marker.addTo(map);
      }
    } else {
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    }
  });
  
  // Fetch current weather if enabling
  if (weatherVisible) {
    fetch('/api/weather')
      .then(res => res.json())
      .then(data => {
        if (data.weather) {
          updateWeatherMarkers(data.weather);
        }
      })
      .catch(err => console.error('Error fetching weather:', err));
  }
}

// ----------------------
// Webcams Tracking
// ----------------------
function generateWebcamPopup(webcamData) {
  const title = webcamData.title || 'Webcam';
  const lat = webcamData.lat;
  const lon = webcamData.lon;
  const status = webcamData.status || 'unknown';
  const image = webcamData.image || {};
  const player = webcamData.player || {};
  
  let html = `<b>${title}</b><br>`;
  html += `<small>Status: ${status}</small><br><hr>`;
  
  // Add preview image if available
  if (image.current && image.current.preview) {
    html += `<img src="${image.current.preview}" style="max-width: 300px; max-height: 200px; border-radius: 4px; margin: 5px 0;"><br>`;
  }
  
  // Add links to view webcam
  if (image.current && image.current.preview) {
    html += `<a href="${image.current.preview}" target="_blank" style="color: #87CEEB;">View Image</a><br>`;
  }
  if (player.live && player.live.embed) {
    html += `<a href="${player.live.embed}" target="_blank" style="color: #87CEEB;">View Live Stream</a><br>`;
  }
  
  // Add location info
  if (lat && lon) {
    html += `<a href="https://www.google.com/maps/search/?api=1&query=${lat},${lon}" target="_blank" style="color: #87CEEB;">View on Google Maps</a>`;
  }
  
  return html;
}

function updateWebcamMarkers(webcamsData) {
  if (!webcamsData || typeof webcamsData !== 'object') {
    console.warn('Invalid webcams data:', webcamsData);
    return;
  }
  
  if (!webcamsVisible) {
    // Remove all markers if webcams are hidden
    Object.values(webcamMarkers).forEach(marker => {
      if (marker && map && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    });
    webcamMarkers = {};
    return;
  }
  
  if (!map) {
    console.warn('Map not initialized yet for webcams');
    return;
  }
  
  // Get current webcam IDs
  const currentIds = Object.keys(webcamsData);
  console.log('Updating webcam markers for', currentIds.length, 'webcams');
  
  // Remove markers for webcams that no longer exist
  Object.keys(webcamMarkers).forEach(id => {
    if (!currentIds.includes(id)) {
      const marker = webcamMarkers[id];
      if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
      delete webcamMarkers[id];
    }
  });
  
  // Add or update markers for each webcam
  Object.keys(webcamsData).forEach(id => {
    const webcam = webcamsData[id];
    if (!webcam) return;
    
    const lat = webcam.lat;
    const lon = webcam.lon;
    
    if (!lat || !lon || isNaN(lat) || isNaN(lon)) {
      console.warn('Invalid coordinates for webcam:', id, lat, lon);
      return;
    }
    
    // Create or update marker
    if (webcamMarkers[id]) {
      webcamMarkers[id].setLatLng([lat, lon]);
      webcamMarkers[id].setPopupContent(generateWebcamPopup(webcam));
    } else {
      // Create webcam icon (camera emoji)
      const webcamIcon = L.divIcon({
        className: 'webcam-marker',
        html: '<div style="background-color: #ff6b6b; border: 2px solid #1a1a1a; border-radius: 50%; width: 24px; height: 24px; display: flex; align-items: center; justify-content: center; font-size: 14px;">📹</div>',
        iconSize: [24, 24],
        iconAnchor: [12, 12]
      });
      
      const marker = L.marker([lat, lon], { icon: webcamIcon })
        .bindPopup(generateWebcamPopup(webcam));
      
      marker.addTo(map);
      webcamMarkers[id] = marker;
      console.log('Added webcam marker for', webcam.title || id, 'at', lat, lon);
    }
  });
}

function toggleWebcams() {
  webcamsVisible = !webcamsVisible;
  const btn = document.getElementById('toggleWebcamsButton');
  if (btn) {
    btn.textContent = webcamsVisible ? 'Hide Webcams' : 'Show Webcams';
    btn.style.backgroundColor = webcamsVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
    btn.style.color = webcamsVisible ? 'var(--color-bg)' : 'var(--color-text)';
  }
  
  // Update visibility
  Object.values(webcamMarkers).forEach(marker => {
    if (webcamsVisible) {
      if (!map.hasLayer(marker)) {
        marker.addTo(map);
      }
    } else {
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    }
  });
  
  // Fetch current webcams if enabling
  if (webcamsVisible) {
    fetch('/api/webcams')
      .then(res => res.json())
      .then(data => {
        if (data.webcams) {
          updateWebcamMarkers(data.webcams);
        }
      })
      .catch(err => console.error('Error fetching webcams:', err));
  }
}

// ----------------------
// APRS Station Tracking
// ----------------------
let aprsStationMarkers = {};
let aprsStationsVisible = true;

// Listen for APRS station updates (bulk)
socket.on('aprs_stations', function(data) {
  updateAprsStations(data.stations || []);
});

function updateAprsStations(stations) {
  if (!aprsStationsVisible) {
    // Clear all markers if layer is hidden
    Object.values(aprsStationMarkers).forEach(marker => {
      if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    });
    aprsStationMarkers = {};
    return;
  }
  
  // Create a set of current callsigns
  const currentCallsigns = new Set(stations.map(s => s.callsign));
  
  // Remove markers for stations that are no longer in the data
  Object.keys(aprsStationMarkers).forEach(callsign => {
    if (!currentCallsigns.has(callsign)) {
      const marker = aprsStationMarkers[callsign];
      if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
      delete aprsStationMarkers[callsign];
    }
  });
  
  // Add or update markers for current stations
  stations.forEach(station => {
    const callsign = station.callsign;
    const lat = station.lat;
    const lon = station.lng;
    
    if (!lat || !lon || lat === 0 || lon === 0) {
      return; // Skip invalid coordinates
    }
    
    // Create or update marker
    if (aprsStationMarkers[callsign]) {
      // Update existing marker position
      aprsStationMarkers[callsign].setLatLng([lat, lon]);
      // Update popup content
      aprsStationMarkers[callsign].setPopupContent(generateAprsPopup(station));
    } else {
      // Create new marker
      const icon = L.icon({
        iconUrl: 'data:image/svg+xml;base64,' + btoa(`
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" fill="#ff6600" stroke="#fff" stroke-width="2"/>
            <text x="12" y="16" font-family="Arial" font-size="12" font-weight="bold" fill="#fff" text-anchor="middle">AP</text>
          </svg>
        `),
        iconSize: [20, 20],
        iconAnchor: [10, 10],
        popupAnchor: [0, -10]
      });
      
      const marker = L.marker([lat, lon], { icon: icon })
        .bindPopup(generateAprsPopup(station))
        .addTo(map);
      
      aprsStationMarkers[callsign] = marker;
    }
  });
}

function generateAprsPopup(station) {
  let content = `<div style="min-width:200px;"><strong>📻 ${station.name || station.callsign}</strong><br>`;
  content += `<small>Callsign: ${station.callsign}</small><br>`;
  
  if (station.speed !== undefined && station.speed > 0) {
    content += `Speed: ${station.speed.toFixed(1)} km/h<br>`;
  }
  if (station.course !== undefined) {
    content += `Course: ${station.course.toFixed(1)}°<br>`;
  }
  if (station.altitude !== undefined && station.altitude > 0) {
    content += `Altitude: ${station.altitude.toFixed(0)} m<br>`;
  }
  if (station.comment) {
    content += `Comment: ${station.comment.substring(0, 50)}${station.comment.length > 50 ? '...' : ''}<br>`;
  }
  if (station.status) {
    content += `Status: ${station.status.substring(0, 50)}${station.status.length > 50 ? '...' : ''}<br>`;
  }
  
  // Format last update time
  if (station.lasttime) {
    const lastUpdate = new Date(station.lasttime * 1000);
    const timeAgo = Math.floor((Date.now() - lastUpdate.getTime()) / 1000);
    let timeStr = '';
    if (timeAgo < 60) timeStr = `${timeAgo}s ago`;
    else if (timeAgo < 3600) timeStr = `${Math.floor(timeAgo / 60)}m ago`;
    else timeStr = `${Math.floor(timeAgo / 3600)}h ago`;
    content += `<br><small>Last update: ${timeStr}</small><br>`;
  }
  
  if (station.lat && station.lng) {
    content += `<br><small>Position: ${station.lat.toFixed(4)}, ${station.lng.toFixed(4)}</small><br>`;
    content += `<a target="_blank" href="https://www.google.com/maps/search/?api=1&query=${station.lat},${station.lng}">View on Google Maps</a><br>`;
    content += `<a target="_blank" href="https://aprs.fi/#!call=${station.callsign}">View on aprs.fi</a>`;
  }
  
  content += '</div>';
  return content;
}

function toggleAprsStations() {
  aprsStationsVisible = !aprsStationsVisible;
  const btn = document.getElementById('toggleAprsButton');
  if (btn) {
    btn.textContent = aprsStationsVisible ? 'Hide APRS' : 'Show APRS';
    btn.style.backgroundColor = aprsStationsVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
    btn.style.color = aprsStationsVisible ? 'var(--color-bg)' : 'var(--color-text)';
  }
  
  // Update visibility
  Object.values(aprsStationMarkers).forEach(marker => {
    if (aprsStationsVisible) {
      if (!map.hasLayer(marker)) {
        marker.addTo(map);
      }
    } else {
      if (map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    }
  });
  
  // Fetch current stations if enabling
  if (aprsStationsVisible) {
    fetch('/api/aprs_stations')
      .then(res => res.json())
      .then(data => {
        if (data.stations) {
          updateAprsStations(data.stations);
        }
      })
      .catch(err => console.error('Error fetching APRS stations:', err));
  }
}

// APRS Configuration Functions
let aprsCallsigns = [];

function openAprsConfigModal() {
  document.getElementById('aprsConfigModal').style.display = 'block';
  loadAprsConfig();
}

function closeAprsConfigModal() {
  document.getElementById('aprsConfigModal').style.display = 'none';
}

// Met Office Weather Alerts Modal Functions
function openMetOfficeSettingsModal() {
  document.getElementById('metOfficeSettingsModal').style.display = 'block';
  loadMetOfficeSettings();
}

function closeMetOfficeSettingsModal() {
  document.getElementById('metOfficeSettingsModal').style.display = 'none';
}

function loadMetOfficeSettings() {
  // Load settings from localStorage or use defaults
  const easTonesEnabled = localStorage.getItem('metOfficeEasTonesEnabled') !== 'false';
  const amberAlertsEnabled = localStorage.getItem('metOfficeAmberAlertsEnabled') === 'true';
  const yellowAlertsEnabled = localStorage.getItem('metOfficeYellowAlertsEnabled') === 'true';
  const repeatAlertsEnabled = localStorage.getItem('metOfficeRepeatAlertsEnabled') === 'true';
  const easVolume = parseInt(localStorage.getItem('metOfficeEasVolume') || '40');
  const updateFrequency = parseInt(localStorage.getItem('metOfficeUpdateFrequency') || '1800');
  
  document.getElementById('easTonesEnabled').checked = easTonesEnabled;
  document.getElementById('amberAlertsEnabled').checked = amberAlertsEnabled;
  document.getElementById('yellowAlertsEnabled').checked = yellowAlertsEnabled;
  document.getElementById('repeatAlertsEnabled').checked = repeatAlertsEnabled;
  document.getElementById('easVolumeSlider').value = easVolume;
  document.getElementById('easVolumeValue').textContent = easVolume + '%';
  document.getElementById('updateFrequencySelect').value = updateFrequency;
  
  // Update volume display when slider changes
  document.getElementById('easVolumeSlider').addEventListener('input', function() {
    document.getElementById('easVolumeValue').textContent = this.value + '%';
  });
}

function saveMetOfficeSettings() {
  const easTonesEnabled = document.getElementById('easTonesEnabled').checked;
  const amberAlertsEnabled = document.getElementById('amberAlertsEnabled').checked;
  const yellowAlertsEnabled = document.getElementById('yellowAlertsEnabled').checked;
  const repeatAlertsEnabled = document.getElementById('repeatAlertsEnabled').checked;
  const easVolume = parseInt(document.getElementById('easVolumeSlider').value);
  const updateFrequency = parseInt(document.getElementById('updateFrequencySelect').value);
  
  // Save to localStorage
  localStorage.setItem('metOfficeEasTonesEnabled', easTonesEnabled);
  localStorage.setItem('metOfficeAmberAlertsEnabled', amberAlertsEnabled);
  localStorage.setItem('metOfficeYellowAlertsEnabled', yellowAlertsEnabled);
  localStorage.setItem('metOfficeRepeatAlertsEnabled', repeatAlertsEnabled);
  localStorage.setItem('metOfficeEasVolume', easVolume);
  localStorage.setItem('metOfficeUpdateFrequency', updateFrequency);
  
  showMetOfficeStatus('Settings saved successfully', 'success');
}

function testEASTone() {
  // Create EAS tone (853Hz + 960Hz)
  const audioContext = new (window.AudioContext || window.webkitAudioContext)();
  const volume = parseInt(document.getElementById('easVolumeSlider').value) / 100;
  
  const oscillator1 = audioContext.createOscillator();
  const oscillator2 = audioContext.createOscillator();
  const gainNode = audioContext.createGain();
  
  oscillator1.type = 'sine';
  oscillator1.frequency.value = 853;
  
  oscillator2.type = 'sine';
  oscillator2.frequency.value = 960;
  
  gainNode.gain.value = volume * 0.3; // Scale down to avoid clipping
  
  oscillator1.connect(gainNode);
  oscillator2.connect(gainNode);
  gainNode.connect(audioContext.destination);
  
  oscillator1.start();
  oscillator2.start();
  
  // Play for 1 second
  setTimeout(() => {
    oscillator1.stop();
    oscillator2.stop();
  }, 1000);
  
  showMetOfficeStatus('EAS tone test played', 'success');
}

function showMetOfficeStatus(message, type) {
  const statusDiv = document.getElementById('metOfficeSettingsStatus');
  statusDiv.style.display = 'block';
  statusDiv.textContent = message.toUpperCase();
  statusDiv.style.color = type === 'error' ? '#ff4444' : '#00ff41';
  statusDiv.style.border = `1px solid ${type === 'error' ? '#ff4444' : '#00ff41'}`;
  statusDiv.style.background = type === 'error' ? 'rgba(255,68,68,0.1)' : 'rgba(0,255,65,0.1)';
  statusDiv.style.textShadow = `0 0 5px ${type === 'error' ? 'rgba(255,68,68,0.5)' : 'rgba(0,255,65,0.5)'}`;
  statusDiv.style.fontWeight = '700';
  
  setTimeout(() => {
    statusDiv.style.display = 'none';
  }, 3000);
}

// Weather Configuration Modal Functions
function openWeatherConfigModal() {
  document.getElementById('weatherConfigModal').style.display = 'block';
  loadWeatherLocations();
}

function closeWeatherConfigModal() {
  document.getElementById('weatherConfigModal').style.display = 'none';
}

function loadWeatherLocations() {
  fetch('/api/weather_config')
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        const locations = data.locations || [];
        const listDiv = document.getElementById('weatherLocationsList');
        listDiv.innerHTML = '';
        
        if (locations.length === 0) {
          listDiv.innerHTML = '<div style="color:#00ff41; text-align:center; padding:20px; font-size:0.9em;">NO LOCATIONS CONFIGURED</div>';
        } else {
          locations.forEach((loc, index) => {
            const locDiv = document.createElement('div');
            locDiv.style.cssText = 'display:flex; justify-content:space-between; align-items:center; padding:8px; margin-bottom:6px; border:1px solid #00ff41; background:rgba(0,10,0,0.5); font-family:"Courier New",monospace;';
            locDiv.innerHTML = `
              <div style="flex:1; color:#00ff41; font-size:0.85em;">
                <strong>${loc.name || 'Unnamed'}</strong><br>
                <small>${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)}</small>
              </div>
              <button onclick="removeWeatherLocation(${index})" style="background:rgba(0,20,0,0.8); border:1px solid #ff4444; color:#ff4444; padding:6px 12px; border-radius:0; cursor:pointer; font-weight:700; font-family:"Courier New",monospace; text-transform:uppercase; font-size:0.75em; text-shadow:0 0 5px rgba(255,68,68,0.5);" onmouseover="this.style.backgroundColor='rgba(255,68,68,0.2)';" onmouseout="this.style.backgroundColor='rgba(0,20,0,0.8)';">REMOVE</button>
            `;
            listDiv.appendChild(locDiv);
          });
        }
      }
    })
    .catch(err => {
      console.error('Error loading weather locations:', err);
      showWeatherConfigStatus('Error loading locations', 'error');
    });
}

function addWeatherLocation() {
  const name = document.getElementById('weatherLocationName').value.trim();
  const lat = parseFloat(document.getElementById('weatherLocationLat').value);
  const lon = parseFloat(document.getElementById('weatherLocationLon').value);
  
  if (!name) {
    showWeatherConfigStatus('Location name is required', 'error');
    return;
  }
  
  if (isNaN(lat) || isNaN(lon)) {
    showWeatherConfigStatus('Valid latitude and longitude are required', 'error');
    return;
  }
  
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    showWeatherConfigStatus('Latitude must be -90 to 90, Longitude must be -180 to 180', 'error');
    return;
  }
  
  // Get current config
  fetch('/api/weather_config')
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        const locations = data.locations || [];
        const apiKey = data.config?.windy_api_key || '';
        
        // Add new location
        locations.push({
          lat: lat,
          lon: lon,
          name: name,
          source: 'manual'
        });
        
        // Update config
        fetch('/api/weather_config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            windy_api_key: apiKey,
            locations: locations
          })
        })
        .then(res => res.json())
        .then(result => {
          if (result.status === 'ok') {
            // Clear form
            document.getElementById('weatherLocationName').value = '';
            document.getElementById('weatherLocationLat').value = '';
            document.getElementById('weatherLocationLon').value = '';
            
            // Reload locations
            loadWeatherLocations();
            showWeatherConfigStatus('Location added successfully', 'success');
            
            // Trigger weather update
            fetch('/api/weather_update', {method: 'POST'});
          } else {
            showWeatherConfigStatus('Error: ' + (result.message || 'Failed to add location'), 'error');
          }
        })
        .catch(err => {
          console.error('Error adding location:', err);
          showWeatherConfigStatus('Error adding location', 'error');
        });
      }
    })
    .catch(err => {
      console.error('Error fetching config:', err);
      showWeatherConfigStatus('Error fetching configuration', 'error');
    });
}

function removeWeatherLocation(index) {
  // Get current config
  fetch('/api/weather_config')
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        const locations = data.locations || [];
        // Get API key from config or use empty string (will use env var if available)
        const apiKey = data.config?.windy_api_key || '';
        
        // Remove location
        locations.splice(index, 1);
        
        // Update config - send API key only if not from environment and we have one
        const payload = { locations: locations };
        if (!data.has_env_key && apiKey && !apiKey.includes('...')) {
          payload.windy_api_key = apiKey;
        }
        
        fetch('/api/weather_config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(result => {
          if (result.status === 'ok') {
            // Reload locations
            loadWeatherLocations();
            showWeatherConfigStatus('LOCATION REMOVED', 'success');
          } else {
            showWeatherConfigStatus('ERROR: ' + (result.message || 'Failed to remove location'), 'error');
          }
        })
        .catch(err => {
          console.error('Error removing location:', err);
          showWeatherConfigStatus('Error removing location', 'error');
        });
      }
    })
    .catch(err => {
      console.error('Error fetching config:', err);
      showWeatherConfigStatus('Error fetching configuration', 'error');
    });
}

function showWeatherConfigStatus(message, type) {
  const statusDiv = document.getElementById('weatherConfigStatus');
  statusDiv.style.display = 'block';
  statusDiv.textContent = message;
  statusDiv.style.color = type === 'error' ? '#ff4444' : '#00ff41';
  statusDiv.style.border = `1px solid ${type === 'error' ? '#ff4444' : '#00ff41'}`;
  statusDiv.style.background = type === 'error' ? 'rgba(255,68,68,0.1)' : 'rgba(0,255,65,0.1)';
  statusDiv.style.padding = '8px';
  statusDiv.style.textShadow = `0 0 5px ${type === 'error' ? 'rgba(255,68,68,0.5)' : 'rgba(0,255,65,0.5)'}`;
  
  setTimeout(() => {
    statusDiv.style.display = 'none';
  }, 3000);
}

function loadAprsConfig() {
  // Load APRS configuration
  fetch('/api/aprs_config')
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        // Load API key (will be masked, so we might need to keep existing if masked)
        const apiKeyInput = document.getElementById('aprsApiKey');
        if (data.config && data.config.aprs_api_key) {
          // If masked (contains ...), don't overwrite if user has entered something
          if (data.config.aprs_api_key.includes('...')) {
            // Keep current value or show masked indicator
            if (!apiKeyInput.value) {
              apiKeyInput.placeholder = 'API key configured (hidden for security)';
            }
          } else {
            apiKeyInput.value = data.config.aprs_api_key;
          }
        }
        
        // Load callsigns
        aprsCallsigns = (data.config && data.config.callsigns) ? [...data.config.callsigns] : [];
        updateAprsCallsignList();
      }
    })
    .catch(err => {
      console.error('Error loading APRS config:', err);
      showAprsStatus('Error loading configuration', 'error');
    });
  
  // Load APRS detection enabled state
  fetch('/api/aprs_detection')
    .then(res => res.json())
    .then(data => {
      document.getElementById('aprsEnabled').checked = data.enabled || false;
    })
    .catch(err => console.error('Error loading APRS detection state:', err));
}

function updateAprsCallsignList() {
  const listDiv = document.getElementById('aprsCallsignList');
  const countSpan = document.getElementById('aprsCallsignCount');
  
  if (!listDiv || !countSpan) return;
  
  countSpan.textContent = `${aprsCallsigns.length} callsign${aprsCallsigns.length !== 1 ? 's' : ''}`;
  
  if (aprsCallsigns.length === 0) {
    listDiv.innerHTML = '<div style="text-align:center; color:#9a9a9a; padding:20px;">No callsigns configured</div>';
    return;
  }
  
  listDiv.innerHTML = '';
  aprsCallsigns.forEach((callsign, index) => {
    const itemDiv = document.createElement('div');
    itemDiv.style.cssText = 'display:flex; justify-content:space-between; align-items:center; padding:8px; margin-bottom:4px; background:#2a2a2a; border:1px solid #4a4a4a; border-radius:3px;';
    itemDiv.innerHTML = `
      <span style="color:#e0e0e0; font-weight:600; font-family:monospace;">${callsign}</span>
      <button onclick="removeAprsCallsign(${index})" style="background:#ff4444; border:none; color:#fff; padding:4px 12px; border-radius:3px; cursor:pointer; font-size:0.85em; font-weight:600;">Remove</button>
    `;
    listDiv.appendChild(itemDiv);
  });
}

function addAprsCallsign() {
  const input = document.getElementById('newCallsign');
  if (!input) return;
  
  const callsign = input.value.trim().toUpperCase();
  
  if (!callsign) {
    showAprsStatus('Please enter a callsign', 'error');
    return;
  }
  
  if (aprsCallsigns.length >= 20) {
    showAprsStatus('Maximum 20 callsigns allowed', 'error');
    return;
  }
  
  if (aprsCallsigns.includes(callsign)) {
    showAprsStatus('Callsign already added', 'error');
    return;
  }
  
  // Basic validation - callsigns are typically alphanumeric, 3-7 characters
  if (!/^[A-Z0-9]{3,7}(-[0-9]+)?$/.test(callsign)) {
    if (!confirm('Callsign format looks unusual. Add anyway?')) {
      return;
    }
  }
  
  aprsCallsigns.push(callsign);
  input.value = '';
  updateAprsCallsignList();
  showAprsStatus(`Added ${callsign}`, 'success');
}

function removeAprsCallsign(index) {
  const callsign = aprsCallsigns[index];
  aprsCallsigns.splice(index, 1);
  updateAprsCallsignList();
  showAprsStatus(`Removed ${callsign}`, 'success');
}

function saveAprsConfig() {
  const apiKeyInput = document.getElementById('aprsApiKey');
  const enabledCheckbox = document.getElementById('aprsEnabled');
  
  if (!apiKeyInput || !enabledCheckbox) return;
  
  const apiKey = apiKeyInput.value.trim();
  const enabled = enabledCheckbox.checked;
  
  if (!apiKey) {
    showAprsStatus('API key is required', 'error');
    return;
  }
  
  showAprsStatus('Saving configuration...', 'info');
  
  // Save configuration
  fetch('/api/aprs_config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      aprs_api_key: apiKey,
      callsigns: aprsCallsigns
    })
  })
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        // Save enabled state
        return fetch('/api/aprs_detection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: enabled })
        });
      } else {
        throw new Error(data.message || 'Failed to save configuration');
      }
    })
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        showAprsStatus('Configuration saved successfully!', 'success');
        // Trigger update
        fetch('/api/aprs_update', { method: 'POST' })
          .catch(err => console.error('Error triggering APRS update:', err));
      } else {
        throw new Error('Failed to save detection state');
      }
    })
    .catch(err => {
      console.error('Error saving APRS config:', err);
      showAprsStatus('Error saving configuration: ' + err.message, 'error');
    });
}

function testAprsConfig() {
  const apiKeyInput = document.getElementById('aprsApiKey');
  if (!apiKeyInput) return;
  
  const apiKey = apiKeyInput.value.trim();
  
  if (!apiKey) {
    showAprsStatus('Please enter an API key first', 'error');
    return;
  }
  
  showAprsStatus('Testing connection...', 'info');
  
  // Test API key format by saving config
  fetch('/api/aprs_config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      aprs_api_key: apiKey,
      callsigns: []
    })
  })
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        showAprsStatus('API key format is valid. Save configuration to test with callsigns.', 'success');
      } else {
        throw new Error(data.message || 'API key validation failed');
      }
    })
    .catch(err => {
      console.error('Error testing APRS config:', err);
      showAprsStatus('Test failed: ' + err.message, 'error');
    });
}

function showAprsStatus(message, type) {
  const statusDiv = document.getElementById('aprsConfigStatus');
  if (!statusDiv) return;
  
  statusDiv.style.display = 'block';
  statusDiv.textContent = message;
  
  // Remove existing status classes
  statusDiv.className = '';
  
  // Add appropriate styling based on type
  if (type === 'success') {
    statusDiv.style.backgroundColor = '#2d5a2d';
    statusDiv.style.color = '#90ee90';
    statusDiv.style.border = '1px solid #4a9eff';
  } else if (type === 'error') {
    statusDiv.style.backgroundColor = '#5a2d2d';
    statusDiv.style.color = '#ffaaaa';
    statusDiv.style.border = '1px solid #ff4444';
  } else {
    statusDiv.style.backgroundColor = '#2d3a5a';
    statusDiv.style.color = '#aaaaff';
    statusDiv.style.border = '1px solid #4a4a4a';
  }
  
  // Auto-hide success/info messages after 5 seconds
  if (type === 'success' || type === 'info') {
    setTimeout(() => {
      statusDiv.style.display = 'none';
    }, 5000);
  }
}

// Lightning alert audio function
function playLightningAlert() {
  if (!audioAlertsEnabled) return;
  
  try {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    // Create a sharp, attention-grabbing tone for lightning
    const oscillator = audioContext.createOscillator();
    const gainNode = audioContext.createGain();
    
    oscillator.connect(gainNode);
    gainNode.connect(audioContext.destination);
    
    // High-pitched, urgent tone
    oscillator.frequency.value = 800;
    oscillator.type = 'sine';
    
    gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.3);
    
    oscillator.start(audioContext.currentTime);
    oscillator.stop(audioContext.currentTime + 0.3);
    
    // Play a second beep after a short delay for urgency
    setTimeout(() => {
      const oscillator2 = audioContext.createOscillator();
      const gainNode2 = audioContext.createGain();
      
      oscillator2.connect(gainNode2);
      gainNode2.connect(audioContext.destination);
      
      oscillator2.frequency.value = 1000;
      oscillator2.type = 'sine';
      
      gainNode2.gain.setValueAtTime(0.3, audioContext.currentTime);
      gainNode2.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.2);
      
      oscillator2.start(audioContext.currentTime);
      oscillator2.stop(audioContext.currentTime + 0.2);
    }, 200);
  } catch (error) {
    console.error('Error playing lightning alert:', error);
  }
}

// EAS (Emergency Alert System) tone function for critical weather warnings
function playEASTone() {
  if (!audioAlertsEnabled) return;
  
  // Check if EAS tones are enabled in settings
  if (!metofficeAlertSettings || !metofficeAlertSettings.eas_tones_enabled) {
    return;
  }
  
  try {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    
    // EAS tone specification:
    // First tone: 853 Hz for 0.8 seconds
    // Silence: 0.25 seconds
    // Second tone: 960 Hz for 0.8 seconds
    
    const toneDuration = 0.8; // seconds
    const silenceDuration = 0.25; // seconds
    // Use volume from settings (0-100, convert to 0-1 range)
    const volume = (metofficeAlertSettings.eas_volume || 40) / 100 * 0.4;
    
    // First tone: 853 Hz
    const oscillator1 = audioContext.createOscillator();
    const gainNode1 = audioContext.createGain();
    
    oscillator1.connect(gainNode1);
    gainNode1.connect(audioContext.destination);
    
    oscillator1.frequency.value = 853;
    oscillator1.type = 'sine';
    
    gainNode1.gain.setValueAtTime(0, audioContext.currentTime);
    gainNode1.gain.linearRampToValueAtTime(volume, audioContext.currentTime + 0.01);
    gainNode1.gain.setValueAtTime(volume, audioContext.currentTime + toneDuration - 0.01);
    gainNode1.gain.linearRampToValueAtTime(0, audioContext.currentTime + toneDuration);
    
    oscillator1.start(audioContext.currentTime);
    oscillator1.stop(audioContext.currentTime + toneDuration);
    
    // Second tone: 960 Hz (after silence)
    const startTime2 = audioContext.currentTime + toneDuration + silenceDuration;
    
    setTimeout(() => {
      const oscillator2 = audioContext.createOscillator();
      const gainNode2 = audioContext.createGain();
      
      oscillator2.connect(gainNode2);
      gainNode2.connect(audioContext.destination);
      
      oscillator2.frequency.value = 960;
      oscillator2.type = 'sine';
      
      const currentTime = audioContext.currentTime;
      gainNode2.gain.setValueAtTime(0, currentTime);
      gainNode2.gain.linearRampToValueAtTime(volume, currentTime + 0.01);
      gainNode2.gain.setValueAtTime(volume, currentTime + toneDuration - 0.01);
      gainNode2.gain.linearRampToValueAtTime(0, currentTime + toneDuration);
      
      oscillator2.start(currentTime);
      oscillator2.stop(currentTime + toneDuration);
    }, (toneDuration + silenceDuration) * 1000);
    
  } catch (error) {
    console.error('Error playing EAS tone:', error);
  }
}

// Zone Management Functions
let zoneLayers = {};
let drawingZone = false;
let currentZonePolygon = null;
let zoneDrawPoints = [];
let zonesVisible = true;  // Default to showing zones

function openZonesPanel() {
  document.getElementById('zonesModal').style.display = 'block';
  loadZones();
}

function closeZonesPanel() {
  document.getElementById('zonesModal').style.display = 'none';
  if (drawingZone) {
    cancelZoneDrawing();
  }
}

function loadZones() {
  fetch('/api/zones')
    .then(res => res.json())
    .then(data => {
      // Update zones list in panel if panel exists
      const zonesList = document.getElementById('zonesList');
      if (zonesList) {
        zonesList.innerHTML = '';
        
        if (data.zones.length === 0) {
          zonesList.innerHTML = '<div style="text-align:center; color:#9a9a9a; padding:20px;">No zones defined</div>';
        } else {
          data.zones.forEach(zone => {
            const zoneDiv = document.createElement('div');
            zoneDiv.style.cssText = 'border:1px solid #4a4a4a; background:#2a2a2a; padding:12px; margin-bottom:10px; border-radius:3px;';
            zoneDiv.innerHTML = `
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                  <strong style="color:#e0e0e0;">${zone.name || 'Unnamed Zone'}</strong>
                  <span style="color:#9a9a9a; font-size:0.85em; margin-left:10px;">${zone.type || 'warning'}</span>
                  ${zone.enabled ? '<span style="color:#4a9eff; font-size:0.8em; margin-left:10px;">ENABLED</span>' : '<span style="color:#9a9a9a; font-size:0.8em; margin-left:10px;">DISABLED</span>'}
                </div>
                <div>
                  <button onclick="toggleZone('${zone.id}')" style="margin-right:5px; padding:4px 8px; background:#2a2a2a; border:1px solid #4a4a4a; color:#e0e0e0; border-radius:3px; cursor:pointer; font-size:0.8em;">${zone.enabled ? 'Disable' : 'Enable'}</button>
                  <button onclick="deleteZone('${zone.id}')" style="padding:4px 8px; background:#ff4444; border:1px solid #ff4444; color:#fff; border-radius:3px; cursor:pointer; font-size:0.8em;">Delete</button>
                </div>
              </div>
            `;
            zonesList.appendChild(zoneDiv);
          });
        }
      }
      
      // Always draw zones on map (even if panel isn't open)
      drawZonesOnMap(data.zones);
      
      // Update toggle button state
      const toggleBtn = document.getElementById('toggleZonesButton');
      if (toggleBtn) {
        toggleBtn.textContent = zonesVisible ? 'Hide Zones' : 'Show Zones';
        toggleBtn.style.backgroundColor = zonesVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
        toggleBtn.style.color = zonesVisible ? 'var(--color-bg)' : 'var(--color-text)';
      }
    })
    .catch(err => {
      console.error('Error loading zones:', err);
    });
}

function drawZonesOnMap(zones) {
  // Check if map is available
  if (typeof map === 'undefined' || !map) {
    console.warn('Map not ready, retrying zone load in 1 second...');
    setTimeout(() => loadZones(), 1000);
    return;
  }
  
  // Clear existing zones
  Object.values(zoneLayers).forEach(layer => {
    try {
      map.removeLayer(layer);
    } catch(e) {
      // Layer might not exist, ignore
    }
  });
  zoneLayers = {};
  
  // Don't draw if zones are hidden
  if (!zonesVisible) {
    console.log('Zones are hidden, not drawing');
    return;
  }
  
  if (!zones || zones.length === 0) {
    console.log('No zones to draw');
    return;
  }
  
  console.log(`Drawing ${zones.length} zones on map`);
  
  zones.forEach(zone => {
    if (!zone.enabled) return;
    
    // Skip expired NOTAM zones
    if (zone.source === 'notam' && zone.end_date) {
      try {
        const endDate = new Date(zone.end_date);
        if (endDate < new Date()) {
          console.log(`Skipping expired NOTAM zone: ${zone.name || zone.id}`);
          return;
        }
      } catch(e) {
        // If date parsing fails, include it anyway
      }
    }
    
    const coords = zone.coordinates || [];
    if (coords.length < 3) {
      console.warn(`Zone ${zone.name || zone.id} has insufficient coordinates: ${coords.length}`);
      return;
    }
    
    const color = zone.type === 'critical' ? '#ff4444' : zone.type === 'warning' ? '#ffb347' : '#4a9eff';
    
    try {
      const polygon = L.polygon(coords, {
        color: color,
        fillColor: color,
        fillOpacity: 0.3,
        weight: 3,
        opacity: 0.8
      });
      
      // Only add to map if zones are visible
      if (zonesVisible) {
        polygon.addTo(map);
        // Bring zones to front so they're visible
        polygon.bringToFront();
      }
      
      // Create popup content with full details for NOTAMs
      let popupContent = `<strong>${zone.name || 'Unnamed Zone'}</strong><br>Type: ${zone.type || 'warning'}`;
      
      if (zone.lower_altitude_ft !== undefined || zone.upper_altitude_ft !== undefined) {
        const lower = zone.lower_altitude_ft || 0;
        const upper = zone.upper_altitude_ft || 'unlimited';
        popupContent += `<br>Altitude: ${lower}ft - ${upper}ft`;
      }
      
      // Add full NOTAM details if it's a NOTAM zone
      if (zone.source === 'notam') {
        if (zone.description) {
          popupContent += `<br><br><strong>NOTAM Details:</strong><br><div style="max-width:300px; word-wrap:break-word; font-size:0.9em;">${zone.description}</div>`;
        }
        if (zone.notam_id) {
          popupContent += `<br><small>NOTAM ID: ${zone.notam_id}</small>`;
        }
        if (zone.end_date) {
          try {
            const endDate = new Date(zone.end_date);
            popupContent += `<br><small>Valid until: ${endDate.toLocaleString()}</small>`;
          } catch(e) {
            popupContent += `<br><small>Valid until: ${zone.end_date}</small>`;
          }
        }
      }
      
      // Add airspace class and frequency for OpenAir zones
      if (zone.source === 'openair') {
        if (zone.airspace_class) {
          popupContent += `<br><small>Class: ${zone.airspace_class}</small>`;
        }
        if (zone.frequency) {
          popupContent += `<br><small>Frequency: ${zone.frequency} MHz</small>`;
        }
      }
      
      polygon.bindPopup(popupContent);
      zoneLayers[zone.id] = polygon;
    } catch(e) {
      console.error(`Error drawing zone ${zone.name || zone.id}:`, e);
    }
  });
  
  console.log(`Successfully drawn ${Object.keys(zoneLayers).length} zones`);
}

function toggleZonesVisibility() {
  zonesVisible = !zonesVisible;
  const btn = document.getElementById('toggleZonesButton');
  
  if (zonesVisible) {
    // Show zones
    Object.values(zoneLayers).forEach(layer => {
      if (!map.hasLayer(layer)) {
        layer.addTo(map);
      }
    });
    if (btn) {
      btn.textContent = 'Hide Zones';
      btn.style.backgroundColor = '#4a9eff';
    }
  } else {
    // Hide zones
    Object.values(zoneLayers).forEach(layer => {
      if (map.hasLayer(layer)) {
        map.removeLayer(layer);
      }
    });
    if (btn) {
      btn.textContent = 'Show Zones';
      btn.style.backgroundColor = '#666';
    }
  }
}

function startDrawingZone() {
  drawingZone = true;
  zoneDrawPoints = [];
  alert('Click on the map to draw zone polygon. Right-click or press ESC to finish.');
  
  const clickHandler = function(e) {
    zoneDrawPoints.push([e.latlng.lat, e.latlng.lng]);
    
    if (currentZonePolygon) {
      map.removeLayer(currentZonePolygon);
    }
    
    if (zoneDrawPoints.length >= 3) {
      currentZonePolygon = L.polygon(zoneDrawPoints, {
        color: '#4a9eff',
        fillColor: '#4a9eff',
        fillOpacity: 0.3,
        weight: 2
      }).addTo(map);
    }
  };
  
  const contextHandler = function(e) {
    if (zoneDrawPoints.length >= 3) {
      finishZoneDrawing();
    }
    map.off('click', clickHandler);
    map.off('contextmenu', contextHandler);
  };
  
  map.on('click', clickHandler);
  map.on('contextmenu', contextHandler);
  
  document.addEventListener('keydown', function escHandler(e) {
    if (e.key === 'Escape') {
      if (zoneDrawPoints.length >= 3) {
        finishZoneDrawing();
      } else {
        cancelZoneDrawing();
      }
      document.removeEventListener('keydown', escHandler);
    }
  });
}

function finishZoneDrawing() {
  if (zoneDrawPoints.length < 3) {
    alert('Zone needs at least 3 points');
    return;
  }
  
  const name = prompt('Enter zone name:');
  if (!name) {
    cancelZoneDrawing();
    return;
  }
  
  const type = prompt('Enter zone type (warning/critical/info):', 'warning');
  
  const zone = {
    name: name,
    type: type || 'warning',
    coordinates: zoneDrawPoints,
    enabled: true
  };
  
  fetch('/api/zones', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(zone)
  })
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        loadZones();
        cancelZoneDrawing();
      }
    });
}

function cancelZoneDrawing() {
  drawingZone = false;
  zoneDrawPoints = [];
  if (currentZonePolygon) {
    map.removeLayer(currentZonePolygon);
    currentZonePolygon = null;
  }
}

function toggleZone(zoneId) {
  fetch('/api/zones')
    .then(res => res.json())
    .then(data => {
      const zone = data.zones.find(z => z.id === zoneId);
      if (zone) {
        zone.enabled = !zone.enabled;
        fetch(`/api/zones/${zoneId}`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(zone)
        })
          .then(() => loadZones());
      }
    });
}

function deleteZone(zoneId) {
  if (confirm('Delete this zone?')) {
    fetch(`/api/zones/${zoneId}`, {method: 'DELETE'})
      .then(() => loadZones());
  }
}

// Incident Log Functions
function openIncidentsPanel() {
  document.getElementById('incidentsModal').style.display = 'block';
  loadIncidents();
}

function closeIncidentsPanel() {
  document.getElementById('incidentsModal').style.display = 'none';
}

function loadIncidents() {
  const type = document.getElementById('incidentTypeFilter').value;
  const limit = document.getElementById('incidentLimit').value;
  
  let url = `/api/incidents?limit=${limit}`;
  if (type) url += `&type=${type}`;
  
  fetch(url)
    .then(res => res.json())
    .then(data => {
      const incidentsList = document.getElementById('incidentsList');
      incidentsList.innerHTML = '';
      
      if (data.incidents.length === 0) {
        incidentsList.innerHTML = '<div style="text-align:center; color:#9a9a9a; padding:20px;">No incidents found</div>';
        return;
      }
      
      data.incidents.forEach(incident => {
        const incDiv = document.createElement('div');
        incDiv.style.cssText = 'border:1px solid #4a4a4a; background:#2a2a2a; padding:12px; margin-bottom:8px; border-radius:3px; border-left:3px solid ' + 
          (incident.type === 'zone_entry' ? '#ff4444' : incident.type === 'zone_exit' ? '#ffb347' : '#4a9eff') + ';';
        
        const time = new Date(incident.timestamp).toLocaleString();
        const typeLabel = incident.type === 'zone_entry' ? 'ZONE ENTRY' : incident.type === 'zone_exit' ? 'ZONE EXIT' : 'DETECTION';
        
        incDiv.innerHTML = `
          <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
            <strong style="color:#e0e0e0;">${typeLabel}</strong>
            <span style="color:#9a9a9a; font-size:0.85em;">${time}</span>
          </div>
          <div style="color:#e0e0e0; font-size:0.9em;">
            MAC: ${incident.mac} ${incident.alias ? `(${incident.alias})` : ''}<br>
            ${incident.zone_name ? `Zone: ${incident.zone_name}<br>` : ''}
            ${incident.drone_lat && incident.drone_lat !== 0 ? `Location: ${incident.drone_lat.toFixed(6)}, ${incident.drone_long.toFixed(6)}<br>` : ''}
            ${incident.basic_id ? `RID: ${incident.basic_id}<br>` : ''}
            ${incident.rssi ? `RSSI: ${incident.rssi} dBm` : ''}
          </div>
        `;
        incidentsList.appendChild(incDiv);
      });
    });
}

// Load zones on socket connect (only if map is ready)
socket.on('connect', function() {
  if (typeof map !== 'undefined' && map) {
    loadZones();
  } else {
    setTimeout(() => {
      if (typeof map !== 'undefined' && map) {
        loadZones();
      }
    }, 1000);
  }
});

// Listen for zones updates
socket.on('zones_updated', function(data) {
  loadZones();
});

// Listen for zone events
socket.on('zone_event', function(data) {
  const eventType = data.type === 'entry' ? 'ZONE ENTRY' : 'ZONE EXIT';
  const zoneName = data.zone.name || 'Unknown Zone';
  showToast(`${eventType}: ${zoneName}`, `Drone ${data.mac} ${data.type === 'entry' ? 'entered' : 'exited'} ${zoneName}`, data.type === 'entry' ? 'no-gps' : 'known-drone');
});

// Listen for new incidents
socket.on('new_incident', function(incident) {
  if (document.getElementById('incidentsModal') && document.getElementById('incidentsModal').style.display === 'block') {
    loadIncidents();
  }
});

// Remove all polling for detections, serial status, aliases, paths, cumulative log, FAA cache, etc.
// All UI updates are now handled by Socket.IO events above.
// ... existing code ...

// --- Node Mode Main Switch & Polling Interval Sync ---
document.addEventListener('DOMContentLoaded', () => {
  // Restore filter collapsed state
  const filterBox = document.getElementById('filterBox');
  const filterToggle = document.getElementById('filterToggle');
  const wasCollapsed = localStorage.getItem('filterCollapsed') === 'true';
  if (wasCollapsed) {
    filterBox.classList.add('collapsed');
    filterToggle.textContent = '[+]';
  }
  
  // Restore aircraft/ships panel collapsed state
  const aircraftShipsBox = document.getElementById('aircraftShipsBox');
  const aircraftShipsToggle = document.getElementById('aircraftShipsToggle');
  if (aircraftShipsBox && aircraftShipsToggle) {
    const wasAircraftShipsCollapsed = localStorage.getItem('aircraftShipsCollapsed') === 'true';
    if (wasAircraftShipsCollapsed) {
      aircraftShipsBox.classList.add('collapsed');
      aircraftShipsToggle.textContent = '[+]';
    }
    
    // Add toggle event listener
    aircraftShipsToggle.addEventListener('click', function() {
      aircraftShipsBox.classList.toggle('collapsed');
      aircraftShipsToggle.textContent = aircraftShipsBox.classList.contains('collapsed') ? '[+]' : '[-]';
      localStorage.setItem('aircraftShipsCollapsed', aircraftShipsBox.classList.contains('collapsed'));
    });
  }
  // restore follow-lock on reload
  const storedLock = localStorage.getItem('followLock');
  if (storedLock) {
    try {
      followLock = JSON.parse(storedLock);
      if (followLock.type === 'observer') {
        updateObserverPopupButtons();
      } else if (followLock.type === 'drone' || followLock.type === 'pilot') {
        updateMarkerButtons(followLock.type, followLock.id);
      }
    } catch (e) { console.error('Failed to restore followLock', e); }
  }
  // Ensure Node Mode default is off if unset
  if (localStorage.getItem('nodeMode') === null) {
    localStorage.setItem('nodeMode', 'false');
  }
  const mainSwitch = document.getElementById('nodeModeMainSwitch');
  if (mainSwitch) {
    // Sync toggle with stored setting
    mainSwitch.checked = (localStorage.getItem('nodeMode') === 'true');
    mainSwitch.onchange = () => {
      const enabled = mainSwitch.checked;
      localStorage.setItem('nodeMode', enabled);
      clearInterval(updateDataInterval);
      updateDataInterval = setInterval(updateData, enabled ? 1000 : 100);
      // Sync popup toggle if open
      const popupSwitch = document.getElementById('nodeModePopupSwitch');
      if (popupSwitch) popupSwitch.checked = enabled;
    };
  }
  // Start polling based on current setting
  updateData();
  updateDataInterval = setInterval(updateData, mainSwitch && mainSwitch.checked ? 1000 : 100);
  
  // Load zones on page load (after map is ready)
  // Wait for map to be initialized
  function loadZonesWhenReady() {
    if (typeof map !== 'undefined' && map) {
      console.log('Map is ready, loading zones...');
      loadZones();
    } else {
      console.log('Map not ready yet, retrying...');
      setTimeout(loadZonesWhenReady, 500);
    }
  }
  setTimeout(loadZonesWhenReady, 1000);
  // Adaptive polling: slow down during map interactions
  map.on('zoomstart dragstart', () => {
    clearInterval(updateDataInterval);
    updateDataInterval = setInterval(updateData, 500);
  });
  map.on('zoomend dragend', () => {
    clearInterval(updateDataInterval);
    const interval = mainSwitch && mainSwitch.checked ? 1000 : 100;
    updateDataInterval = setInterval(updateData, interval);
  });

  // Staleout slider initialization
  const staleoutSlider = document.getElementById('staleoutSlider');
  const staleoutValue = document.getElementById('staleoutValue');
  if (staleoutSlider && typeof STALE_THRESHOLD !== 'undefined') {
    staleoutSlider.value = STALE_THRESHOLD / 60;
    staleoutValue.textContent = (STALE_THRESHOLD / 60) + ' min';
    staleoutSlider.oninput = () => {
      const minutes = parseInt(staleoutSlider.value, 10);
      STALE_THRESHOLD = minutes * 60;
      staleoutValue.textContent = minutes + ' min';
      localStorage.setItem('staleoutMinutes', minutes.toString());
    };
  }
  // Filter box toggle persistence
  if (filterToggle && filterBox) {
    filterToggle.addEventListener('click', function() {
      filterBox.classList.toggle('collapsed');
      filterToggle.textContent = filterBox.classList.contains('collapsed') ? '[+]' : '[-]';
      // Persist filter collapsed state
      localStorage.setItem('filterCollapsed', filterBox.classList.contains('collapsed'));
    });
  }
});
// Fallback collapse handler to ensure filter toggle works
document.getElementById("filterToggle").addEventListener("click", function() {
  const box = document.getElementById("filterBox");
  const isCollapsed = box.classList.toggle("collapsed");
  this.textContent = isCollapsed ? "[+]" : "[-]";
  localStorage.setItem('filterCollapsed', isCollapsed);
});
// Configure tile loading for smooth zoom transitions
L.Map.prototype.options.fadeAnimation = true;
L.Map.prototype.options.zoomAnimation = true;
L.TileLayer.prototype.options.updateWhenZooming = true;
L.TileLayer.prototype.options.updateWhenIdle = true;
// Use default tileSize for crisp rendering
L.TileLayer.prototype.options.detectRetina = false;
// Keep a moderate tile buffer for smoother panning
L.TileLayer.prototype.options.keepBuffer = 50;
// Disable aggressive preloading to avoid stutters
L.TileLayer.prototype.options.preload = false;
// On window load, restore persisted detection data (trackedPairs) and re-add markers.
window.onload = function() {
  let stored = localStorage.getItem("trackedPairs");
  if (stored) {
    try {
      let storedPairs = JSON.parse(stored);
      window.tracked_pairs = storedPairs;
      for (const mac in storedPairs) {
        let det = storedPairs[mac];
        let color = get_color_for_mac(mac);
        // Restore drone marker if valid coordinates exist.
        if (det.drone_lat && det.drone_long && det.drone_lat != 0 && det.drone_long != 0) {
          if (!droneMarkers[mac]) {
            droneMarkers[mac] = L.marker([det.drone_lat, det.drone_long], {icon: createIcon('🛸', color), pane: 'droneIconPane'})
                                  .bindPopup(generatePopupContent(det, 'drone'))
                                  .addTo(map);
          }
        }
        // Restore pilot marker if valid coordinates exist.
        if (det.pilot_lat && det.pilot_long && det.pilot_lat != 0 && det.pilot_long != 0) {
          if (!pilotMarkers[mac]) {
            pilotMarkers[mac] = L.marker([det.pilot_lat, det.pilot_long], {icon: createIcon('👤', color), pane: 'pilotIconPane'})
                                  .bindPopup(generatePopupContent(det, 'pilot'))
                                  .addTo(map);
          }
        }
      }
      // Prevent webhook/alert firing for restored drones on page reload
      Object.keys(window.tracked_pairs).forEach(mac => alertedNoGpsDrones.add(mac));
    } catch(e) {
      console.error("Error parsing trackedPairs from localStorage", e);
    }
  }
}

if (localStorage.getItem('colorOverrides')) {
  try { window.colorOverrides = JSON.parse(localStorage.getItem('colorOverrides')); }
  catch(e){ window.colorOverrides = {}; }
} else { window.colorOverrides = {}; }

// Restore historical drones from localStorage
if (localStorage.getItem('historicalDrones')) {
  try { window.historicalDrones = JSON.parse(localStorage.getItem('historicalDrones')); }
  catch(e) { window.historicalDrones = {}; }
} else {
  window.historicalDrones = {};
}

// Restore map center and zoom from localStorage
let persistedCenter = localStorage.getItem('mapCenter');
let persistedZoom = localStorage.getItem('mapZoom');
if (persistedCenter) {
  try { persistedCenter = JSON.parse(persistedCenter); } catch(e) { persistedCenter = null; }
} else {
  persistedCenter = null;
}
persistedZoom = persistedZoom ? parseInt(persistedZoom, 10) : null;

// Application-level globals
var aliases = {};
var colorOverrides = window.colorOverrides;

// Load stale-out minutes from localStorage (default 1) and compute threshold in seconds
if (localStorage.getItem('staleoutMinutes') === null) {
  localStorage.setItem('staleoutMinutes', '1');
}
let STALE_THRESHOLD = parseInt(localStorage.getItem('staleoutMinutes'), 10) * 60;

var comboListItems = {};

async function updateAliases() {
  try {
    const response = await fetch(window.location.origin + '/api/aliases');
    aliases = await response.json();
    updateComboList(window.tracked_pairs);
      // Persist detection state across page reloads
      localStorage.setItem("trackedPairs", JSON.stringify(window.tracked_pairs));
  } catch (error) { console.error("Error fetching aliases:", error); }
}

function safeSetView(latlng, zoom=18) {
  const currentZoom = map.getZoom();
  // make sure we have a Leaflet LatLng
  const target = L.latLng(latlng);
  // if it's already on-screen, do just a small "quarter" zoom
  if (map.getBounds().contains(target)) {
    const smallZoom = currentZoom + (zoom - currentZoom) * 0.25;
    map.flyTo(target, smallZoom, { duration: 0.4 });
    return;
  }
  // otherwise do the full zoom-out + zoom-in
  const midZoom = Math.max(Math.min(currentZoom, zoom) - 3, 8);
  map.flyTo(target, midZoom, { duration: 0.3 });
  setTimeout(() => {
    map.flyTo(target, zoom, { duration: 0.5 });
  }, 300);
}

// Global variable to track the current popup timeout
let currentPopupTimeout = null;

// Audio alert settings (persisted in localStorage)
let audioAlertsEnabled = localStorage.getItem('audioAlertsEnabled') !== 'false'; // Default: enabled

// Toast notification function
function showToast(title, message, type = 'new-drone') {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  
  // Determine icon based on type
  let icon = '🛸';
  if (type === 'no-gps') icon = '⚠️';
  else if (type === 'known-drone') icon = '📡';
  
  toast.innerHTML = `
    <div class="toast-icon">${icon}</div>
    <div class="toast-content">
      <div class="toast-title">${title}</div>
      <div class="toast-message">${message}</div>
    </div>
    <button class="toast-close" onclick="this.parentElement.remove()">×</button>
  `;
  
  container.appendChild(toast);
  
  // Auto-remove after 5 seconds
  setTimeout(() => {
    toast.classList.add('removing');
    setTimeout(() => {
      if (toast.parentElement) {
        toast.remove();
      }
    }, 300);
  }, 5000);
  
  // Click to dismiss
  toast.addEventListener('click', function(e) {
    if (e.target.classList.contains('toast-close')) return;
    toast.classList.add('removing');
    setTimeout(() => {
      if (toast.parentElement) {
        toast.remove();
      }
    }, 300);
  });
}

// Lightning detection state
let lightningDetectionEnabled = true;

// Initialize audio toggle and lightning detection toggle on page load
document.addEventListener('DOMContentLoaded', function() {
  const audioToggle = document.getElementById('audioAlertToggle');
  if (audioToggle) {
    audioToggle.checked = audioAlertsEnabled;
    audioToggle.addEventListener('change', function() {
      audioAlertsEnabled = this.checked;
      localStorage.setItem('audioAlertsEnabled', audioAlertsEnabled);
    });
  }
  
  // Load Met Office warnings enabled state
  fetch('/api/metoffice_warnings_detection')
    .then(res => res.json())
    .then(data => {
      metofficeWarningsVisible = data.enabled || false;
      const btn = document.getElementById('toggleMetOfficeButton');
      if (btn) {
        btn.textContent = metofficeWarningsVisible ? 'Hide Warnings' : 'Show Warnings';
        btn.style.backgroundColor = metofficeWarningsVisible ? 'var(--accent-cyan)' : 'var(--color-text-dim)';
        btn.style.color = metofficeWarningsVisible ? 'var(--color-bg)' : 'var(--color-text)';
      }
      // Load initial warnings if enabled
      if (metofficeWarningsVisible) {
        fetch('/api/metoffice_warnings')
          .then(res => res.json())
          .then(data => {
            if (data.warnings) {
              updateMetOfficeWarnings(data.warnings);
            }
          })
          .catch(err => console.error('Error loading Met Office warnings:', err));
      }
    })
    .catch(err => console.error('Error loading Met Office warnings state:', err));
  
  // Lightning detection toggle
  const lightningToggle = document.getElementById('lightningDetectionToggle');
  if (lightningToggle) {
    // Load current state from server
    fetch(window.location.origin + '/api/lightning_detection')
      .then(response => response.json())
      .then(data => {
        lightningDetectionEnabled = data.enabled;
        lightningToggle.checked = lightningDetectionEnabled;
      })
      .catch(error => console.error('Error loading lightning detection state:', error));
    
    lightningToggle.addEventListener('change', function() {
      lightningDetectionEnabled = this.checked;
      fetch(window.location.origin + '/api/lightning_detection', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: lightningDetectionEnabled})
      })
      .then(response => response.json())
      .then(data => {
        console.log('Lightning detection', data.enabled ? 'enabled' : 'disabled');
      })
      .catch(error => console.error('Error toggling lightning detection:', error));
    });
  }
  
  // Test button handler
  const testButton = document.getElementById('testAlertButton');
  if (testButton) {
    testButton.addEventListener('click', function() {
      // Test with a mock detection
      const testDetections = [
        {
          title: 'New Drone Detected',
          message: 'RID: TEST123 | MAC: aa:bb:cc:dd:ee:ff',
          type: 'new-drone',
          hasGps: true,
          isNew: true
        },
        {
          title: 'No-GPS Drone Detected',
          message: 'RID: N/A | MAC: 11:22:33:44:55:66',
          type: 'no-gps',
          hasGps: false,
          isNew: true
        },
        {
          title: 'Known Drone Detected',
          message: 'RID: KNOWN001 | MAC: ff:ee:dd:cc:bb:aa',
          type: 'known-drone',
          hasGps: true,
          isNew: false
        }
      ];
      
      // Cycle through test detections
      const testIndex = Math.floor(Math.random() * testDetections.length);
      const test = testDetections[testIndex];
      
      showToast(test.title, test.message, test.type);
      // Get current alert style for test
      const currentStyle = localStorage.getItem('audioAlertStyle') || 'soft';
      audioAlertStyle = currentStyle;
      playDetectionAlert(test.isNew, test.hasGps);
    });
  }
  
  // Request notification permission for fallback
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
});

// Audio alert function for drone detections
function playDetectionAlert(isNew, hasGps) {
  // Check if audio alerts are enabled
  if (!audioAlertsEnabled) return;
  
  // Get current alert style
  const style = localStorage.getItem('audioAlertStyle') || 'soft';
  
  try {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    
    if (style === 'eas') {
      // EAS Alert System - three-tone pattern (more urgent for no-GPS or new)
      if (!hasGps || isNew) {
        playEASTone(audioContext, 0);
        setTimeout(() => playEASTone(audioContext, 0.5), 1000);
        setTimeout(() => playEASTone(audioContext, 1.0), 2000);
      } else {
        // Single tone for known drones
        playEASTone(audioContext, 0);
      }
    } else if (style === 'siren') {
      // Siren - oscillating frequency
      const duration = (!hasGps || isNew) ? 2.5 : 1.5;
      playSiren(audioContext, duration);
    } else if (style === 'pulse') {
      // Pulse - repeating beeps
      const count = (!hasGps || isNew) ? 4 : 2;
      playPulseAlert(audioContext, count);
    } else {
      // Soft tones (default)
      let frequency = 800;
      let duration = 0.3;
      
      if (!hasGps) {
        frequency = 1000;
        duration = 0.2;
      } else if (isNew) {
        frequency = 800;
        duration = 0.3;
      } else {
        frequency = 600;
        duration = 0.25;
      }
      
      playSoftTone(audioContext, frequency, duration);
      
      // For no-GPS or new drones, play a second beep
      if (!hasGps || isNew) {
        setTimeout(() => {
          playSoftTone(audioContext, frequency, duration);
        }, duration * 1000 + 100);
      }
    }
  } catch (e) {
    console.warn('Audio alert failed:', e);
    // Fallback: use browser notification API if available
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('Drone Detected', {
        body: 'A drone has been detected',
        icon: '/favicon.ico',
        tag: 'drone-detection'
      });
    }
  }
}

// Helper functions for different alert styles
function playEASTone(audioContext, startTime) {
  const oscillator = audioContext.createOscillator();
  const gainNode = audioContext.createGain();
  oscillator.connect(gainNode);
  gainNode.connect(audioContext.destination);
  
  oscillator.frequency.value = 853; // EAS standard frequency
  oscillator.type = 'sine';
  
  const duration = 0.25;
  gainNode.gain.setValueAtTime(0, audioContext.currentTime + startTime);
  gainNode.gain.linearRampToValueAtTime(0.5, audioContext.currentTime + startTime + 0.01);
  gainNode.gain.linearRampToValueAtTime(0, audioContext.currentTime + startTime + duration);
  
  oscillator.start(audioContext.currentTime + startTime);
  oscillator.stop(audioContext.currentTime + startTime + duration);
}

function playSiren(audioContext, duration) {
  const oscillator = audioContext.createOscillator();
  const gainNode = audioContext.createGain();
  oscillator.connect(gainNode);
  gainNode.connect(audioContext.destination);
  
  oscillator.type = 'sine';
  oscillator.frequency.setValueAtTime(800, audioContext.currentTime);
  oscillator.frequency.exponentialRampToValueAtTime(1200, audioContext.currentTime + duration / 2);
  oscillator.frequency.exponentialRampToValueAtTime(800, audioContext.currentTime + duration);
  
  gainNode.gain.setValueAtTime(0, audioContext.currentTime);
  gainNode.gain.linearRampToValueAtTime(0.4, audioContext.currentTime + 0.1);
  gainNode.gain.linearRampToValueAtTime(0, audioContext.currentTime + duration);
  
  oscillator.start(audioContext.currentTime);
  oscillator.stop(audioContext.currentTime + duration);
}

function playPulseAlert(audioContext, count) {
  for (let i = 0; i < count; i++) {
    setTimeout(() => {
      playSoftTone(audioContext, 1000, 0.15);
    }, i * 300);
  }
}

function playSoftTone(audioContext, frequency, duration) {
  const oscillator = audioContext.createOscillator();
  const gainNode = audioContext.createGain();
  oscillator.connect(gainNode);
  gainNode.connect(audioContext.destination);
  
  oscillator.frequency.value = frequency;
  oscillator.type = 'sine';
  
  gainNode.gain.setValueAtTime(0, audioContext.currentTime);
  gainNode.gain.linearRampToValueAtTime(0.3, audioContext.currentTime + 0.01);
  gainNode.gain.linearRampToValueAtTime(0, audioContext.currentTime + duration);
  
  oscillator.start(audioContext.currentTime);
  oscillator.stop(audioContext.currentTime + duration);
}

// Transient terminal-style popup for drone events
function showTerminalPopup(det, isNew) {
  // Clear any existing timeout first
  if (currentPopupTimeout) {
    clearTimeout(currentPopupTimeout);
    currentPopupTimeout = null;
  }

  // Remove any existing popup
  const old = document.getElementById('dronePopup');
  if (old) old.remove();
  
  // Play audible alert
  const hasGps = det.drone_lat && det.drone_long && det.drone_lat !== 0 && det.drone_long !== 0;
  playDetectionAlert(isNew, hasGps);
  
  // Get alias and RID (used for both toast and popup)
  const alias = aliases[det.mac] || '';
  const rid = det.basic_id || 'N/A';
  
  // Show toast notification
  let toastTitle, toastMessage, toastType;
  
  if (!hasGps) {
    toastTitle = 'No-GPS Drone Detected';
    toastMessage = `RID: ${rid} | MAC: ${det.mac}`;
    toastType = 'no-gps';
  } else if (alias) {
    toastTitle = `Known Drone: ${alias}`;
    toastMessage = `RID: ${rid} | MAC: ${det.mac}`;
    toastType = 'known-drone';
  } else if (isNew) {
    toastTitle = 'New Drone Detected';
    toastMessage = `RID: ${rid} | MAC: ${det.mac}`;
    toastType = 'new-drone';
  } else {
    toastTitle = 'Drone Detected';
    toastMessage = `RID: ${rid} | MAC: ${det.mac}`;
    toastType = 'known-drone';
  }
  
  showToast(toastTitle, toastMessage, toastType);

  // Build a new popup container
  const popup = document.createElement('div');
  popup.id = 'dronePopup';
  const isMobile = window.innerWidth <= 600;
  Object.assign(popup.style, {
    position: 'fixed',
    top: isMobile ? '50px' : '10px',
    left: '50%',
    transform: 'translateX(-50%)',
    background: 'rgba(0,0,0,0.8)',
    color: '#e0e0e0',
    fontFamily: 'sans-serif',
    whiteSpace: 'normal',
    padding: isMobile ? '2px 4px' : '4px 8px',
    border: '1px solid #4a4a4a',
    borderRadius: '4px',
    zIndex: 2000,
    opacity: 0.9,
    fontSize: isMobile ? '0.6em' : '',
    maxWidth: isMobile ? '80vw' : 'none',
    display: 'inline-block',
    textAlign: 'center',
  });

  // Build concise popup text (reuse alias and rid from above)
  let header;
  if (!det.drone_lat || !det.drone_long || det.drone_lat === 0 || det.drone_long === 0) {
    header = 'Drone with no GPS lock detected';
  } else if (alias) {
    header = `Known drone detected – ${alias}`;
  } else {
    header = isNew ? 'New drone detected' : 'Previously seen non-aliased drone detected';
  }
  const content = alias
    ? `${header} - RID:${rid} MAC:${det.mac}`
    : `${header} - RID:${rid} MAC:${det.mac}`;
  // Build popup HTML and button using new logic
  // Build popup text
  const isMobileBtn = window.innerWidth <= 600;
  const headerDiv = `<div>${content}</div>`;
  let buttonDiv = '';
  if (det.drone_lat && det.drone_long && det.drone_lat !== 0 && det.drone_long !== 0) {
    const btnStyle = [
      'display:block',
      'width:100%',
      'margin-top:4px',
      'padding:' + (isMobileBtn ? '2px 0' : '4px 6px'),
      'border:1px solid #4a4a4a',
      'border-radius:4px',
      'background:transparent',
      'color:#e0e0e0',
      'font-size:' + (isMobileBtn ? '0.8em' : '0.9em'),
      'cursor:pointer'
    ].join('; ');
    buttonDiv = `<div><button id="zoomBtn" style="${btnStyle}">Zoom to Drone</button></div>`;
  }
  popup.innerHTML = headerDiv + buttonDiv;

  if (buttonDiv) {
    const zoomBtn = popup.querySelector('#zoomBtn');
    zoomBtn.addEventListener('click', () => {
      zoomBtn.style.backgroundColor = '#4a9eff';
      setTimeout(() => { zoomBtn.style.backgroundColor = 'transparent'; }, 200);
      safeSetView([det.drone_lat, det.drone_long]);
    });
  }
  // --- Webhook logic (scoped, non-intrusive) ---
  // Webhooks are now handled automatically by the backend
  // Backend triggers webhooks using the same detection logic as these popups
  // --- End webhook logic ---

  document.body.appendChild(popup);

  // Set a new 5-second timeout and store the reference
  currentPopupTimeout = setTimeout(() => {
    const popupToRemove = document.getElementById('dronePopup');
    if (popupToRemove) {
      popupToRemove.remove();
    }
    currentPopupTimeout = null;
  }, 5000);
}

var followLock = { type: null, id: null, enabled: false };

function generateObserverPopup() {
  var observerLocked = (followLock.enabled && followLock.type === 'observer');
  var storedObserverEmoji = localStorage.getItem('observerEmoji') || "😎";
  return `
  <div>
    <strong>Observer Location</strong><br>
    <label for="observerEmoji">Select Observer Icon:</label>
    <select id="observerEmoji" onchange="updateObserverEmoji()">
       <option value="😎" ${storedObserverEmoji === "😎" ? "selected" : ""}>😎</option>
       <option value="👽" ${storedObserverEmoji === "👽" ? "selected" : ""}>👽</option>
       <option value="🤖" ${storedObserverEmoji === "🤖" ? "selected" : ""}>🤖</option>
       <option value="🏎️" ${storedObserverEmoji === "🏎️" ? "selected" : ""}>🏎️</option>
       <option value="🕵️‍♂️" ${storedObserverEmoji === "🕵️‍♂️" ? "selected" : ""}>🕵️‍♂️</option>
       <option value="🥷" ${storedObserverEmoji === "🥷" ? "selected" : ""}>🥷</option>
       <option value="👁️" ${storedObserverEmoji === "👁️" ? "selected" : ""}>👁️</option>
    </select><br>
    <div style="display:flex; gap:4px; justify-content:center; margin-top:4px;">
        <button id="lock-observer" onclick="lockObserver()" style="background-color: ${observerLocked ? 'green' : ''};">
          ${observerLocked ? 'Locked on Observer' : 'Lock on Observer'}
        </button>
        <button id="unlock-observer" onclick="unlockObserver()" style="background-color: ${observerLocked ? '' : 'green'};">
          ${observerLocked ? 'Unlock Observer' : 'Unlocked Observer'}
        </button>
    </div>
  </div>
  `;
}

// Updated function: now saves the selected observer icon to localStorage and updates the observer marker.
function updateObserverEmoji() {
  var select = document.getElementById("observerEmoji");
  var selectedEmoji = select.value;
  localStorage.setItem('observerEmoji', selectedEmoji);
  if (observerMarker) {
    observerMarker.setIcon(createIcon(selectedEmoji, 'blue'));
  }
}

function lockObserver() { followLock = { type: 'observer', id: 'observer', enabled: true }; updateObserverPopupButtons();
  localStorage.setItem('followLock', JSON.stringify(followLock));
}
function unlockObserver() { followLock = { type: null, id: null, enabled: false }; updateObserverPopupButtons();
  localStorage.setItem('followLock', JSON.stringify(followLock));
}
function updateObserverPopupButtons() {
  var observerLocked = (followLock.enabled && followLock.type === 'observer');
  var lockBtn = document.getElementById("lock-observer");
  var unlockBtn = document.getElementById("unlock-observer");
  if(lockBtn) { lockBtn.style.backgroundColor = observerLocked ? "green" : ""; lockBtn.textContent = observerLocked ? "Locked on Observer" : "Lock on Observer"; }
  if(unlockBtn) { unlockBtn.style.backgroundColor = observerLocked ? "" : "green"; unlockBtn.textContent = observerLocked ? "Unlock Observer" : "Unlocked Observer"; }
}

function generatePopupContent(detection, markerType) {
  let content = '';
  let aliasText = aliases[detection.mac] ? aliases[detection.mac] : "No Alias";
  content += '<strong>ID:</strong> <span id="aliasDisplay_' + detection.mac + '" style="color:#FF00FF;">' + aliasText + '</span> (MAC: ' + detection.mac + ')<br>';
  
  if (detection.basic_id || detection.faa_data) {
    if (detection.basic_id) {
      content += '<div style="border:2px solid #FF00FF; padding:5px; margin:5px 0;">FAA RemoteID: ' + detection.basic_id + '</div>';
    }
    if (detection.basic_id) {
      content += '<button onclick="queryFaaAPI(\\\'' + detection.mac + '\\\', \\\'' + detection.basic_id + '\\\')" id="queryFaaButton_' + detection.mac + '">Query FAA API</button>';
    }
    content += '<div id="faaResult_' + detection.mac + '" style="margin-top:5px;">';
    if (detection.faa_data) {
      let faaData = detection.faa_data;
      let item = null;
      if (faaData.data && faaData.data.items && faaData.data.items.length > 0) {
        item = faaData.data.items[0];
      }
      if (item) {
        const fields = ["makeName", "modelName", "series", "trackingNumber", "complianceCategories", "updatedAt"];
        content += '<div style="border:2px solid #FF69B4; padding:5px; margin:5px 0;">';
        fields.forEach(function(field) {
          let value = item[field] !== undefined ? item[field] : "";
          content += `<div><span style="color:#FF00FF;">${field}:</span> <span style="color:#00FF00;">${value}</span></div>`;
        });
        content += '</div>';
      } else {
        content += '<div style="border:2px solid #FF69B4; padding:5px; margin:5px 0;">No FAA data available</div>';
      }
    }
    content += '</div><br>';
  }
  
  for (const key in detection) {
    if (['mac', 'basic_id', 'last_update', 'userLocked', 'lockTime', 'faa_data'].indexOf(key) === -1) {
      content += key + ': ' + detection[key] + '<br>';
    }
  }
  
  if (detection.drone_lat && detection.drone_long && detection.drone_lat != 0 && detection.drone_long != 0) {
    content += '<a target="_blank" href="https://www.google.com/maps/search/?api=1&query=' 
             + detection.drone_lat + ',' + detection.drone_long + '">View Drone on Google Maps</a><br>';
  }
  if (detection.pilot_lat && detection.pilot_long && detection.pilot_lat != 0 && detection.pilot_long != 0) {
    content += '<a target="_blank" href="https://www.google.com/maps/search/?api=1&query=' 
             + detection.pilot_lat + ',' + detection.pilot_long + '">View Pilot on Google Maps</a><br>';
  }
  
  content += `<hr style="border: 1px solid lime;">
              <label for="aliasInput">Alias:</label>
              <input type="text" id="aliasInput" onclick="event.stopPropagation();" ontouchstart="event.stopPropagation();" 
                     style="background-color: #222; color: #87CEEB; border: 1px solid #FF00FF;" 
                     value="${aliases[detection.mac] ? aliases[detection.mac] : ''}"><br>
              <div style="display:flex; align-items:center; justify-content:space-between; width:100%; margin-top:4px;">
                <button
                  onclick="saveAlias('${detection.mac}'); this.style.backgroundColor='purple'; setTimeout(()=>this.style.backgroundColor='#333',300);"
                  style="flex:1; margin:0 2px; padding:4px 0;"
                >Save Alias</button>
                <button
                  onclick="clearAlias('${detection.mac}'); this.style.backgroundColor='purple'; setTimeout(()=>this.style.backgroundColor='#333',300);"
                  style="flex:1; margin:0 2px; padding:4px 0;"
                >Clear Alias</button>
              </div>`;
  
  content += `<div style="border-top:2px solid lime; margin:10px 0;"></div>`;
  
    var isDroneLocked = (followLock.enabled && followLock.type === 'drone' && followLock.id === detection.mac);
    var droneLockButton = `<button id="lock-drone-${detection.mac}" onclick="lockMarker('drone', '${detection.mac}')" style="flex:${isDroneLocked ? 1.2 : 0.8}; margin:0 2px; padding:4px 0; background-color: ${isDroneLocked ? 'green' : ''};">
      ${isDroneLocked ? 'Locked on Drone' : 'Lock on Drone'}
    </button>`;
    var droneUnlockButton = `<button id="unlock-drone-${detection.mac}" onclick="unlockMarker('drone', '${detection.mac}')" style="flex:${isDroneLocked ? 0.8 : 1.2}; margin:0 2px; padding:4px 0; background-color: ${isDroneLocked ? '' : 'green'};">
      ${isDroneLocked ? 'Unlock Drone' : 'Unlocked Drone'}
    </button>`;
    var isPilotLocked = (followLock.enabled && followLock.type === 'pilot' && followLock.id === detection.mac);
    var pilotLockButton = `<button id="lock-pilot-${detection.mac}" onclick="lockMarker('pilot', '${detection.mac}')" style="flex:${isPilotLocked ? 1.2 : 0.8}; margin:0 2px; padding:4px 0; background-color: ${isPilotLocked ? 'green' : ''};">
      ${isPilotLocked ? 'Locked on Pilot' : 'Lock on Pilot'}
    </button>`;
    var pilotUnlockButton = `<button id="unlock-pilot-${detection.mac}" onclick="unlockMarker('pilot', '${detection.mac}')" style="flex:${isPilotLocked ? 0.8 : 1.2}; margin:0 2px; padding:4px 0; background-color: ${isPilotLocked ? '' : 'green'};">
      ${isPilotLocked ? 'Unlock Pilot' : 'Unlocked Pilot'}
    </button>`;
    content += `
      <div style="display:flex; align-items:center; justify-content:space-between; width:100%; margin-top:4px;">
        ${droneLockButton}
        ${droneUnlockButton}
      </div>
      <div style="display:flex; align-items:center; justify-content:space-between; width:100%; margin-top:4px;">
        ${pilotLockButton}
        ${pilotUnlockButton}
      </div>`;
  
  let defaultHue = colorOverrides[detection.mac] !== undefined ? colorOverrides[detection.mac] : (function(){
      let hash = 0;
      for (let i = 0; i < detection.mac.length; i++){
          hash = detection.mac.charCodeAt(i) + ((hash << 5) - hash);
      }
      return Math.abs(hash) % 360;
  })();
  content += `<div style="margin-top:10px;">
    <label for="colorSlider_${detection.mac}" style="display:block; color:lime;">Color:</label>
    <input type="range" id="colorSlider_${detection.mac}" min="0" max="360" value="${defaultHue}" style="width:100%;" onchange="updateColor('${detection.mac}', this.value)">
  </div>`;

      // Node Mode toggle in popup

  return content;
}

// New function to query the FAA API.
async function queryFaaAPI(mac, remote_id) {
    const button = document.getElementById("queryFaaButton_" + mac);
    if (button) {
        button.disabled = true;
        const originalText = button.textContent;
        button.textContent = "Querying...";
        button.style.backgroundColor = "gray";
    }
    try {
        const response = await fetch(window.location.origin + '/api/query_faa', {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({mac: mac, remote_id: remote_id})
        });
        const result = await response.json();
        if (result.status === "ok") {
            // Immediately update the in-memory tracked_pairs with the returned FAA data
            if (window.tracked_pairs && window.tracked_pairs[mac]) {
              window.tracked_pairs[mac].faa_data = result.faa_data;
            }
            const faaDiv = document.getElementById("faaResult_" + mac);
            if (faaDiv) {
                let faaData = result.faa_data;
                let item = null;
                if (faaData.data && faaData.data.items && faaData.data.items.length > 0) {
                  item = faaData.data.items[0];
                }
                if (item) {
                  const fields = ["makeName", "modelName", "series", "trackingNumber", "complianceCategories", "updatedAt"];
                  let html = '<div style="border:2px solid #FF69B4; padding:5px; margin:5px 0;">';
                  fields.forEach(function(field) {
                    let value = item[field] !== undefined ? item[field] : "";
                    html += `<div><span style="color:#FF00FF;">${field}:</span> <span style="color:#00FF00;">${value}</span></div>`;
                  });
                  html += '</div>';
                  faaDiv.innerHTML = html;
                } else {
                  faaDiv.innerHTML = '<div style="border:2px solid #FF69B4; padding:5px; margin:5px 0;">No FAA data available</div>';
                }
            }
            // Immediately refresh popups with new FAA data
            const key = result.mac || mac;
            if (typeof tracked_pairs !== "undefined" && tracked_pairs[key]) {
              if (droneMarkers[key]) {
                droneMarkers[key].setPopupContent(generatePopupContent(tracked_pairs[key], 'drone'));
                if (droneMarkers[key].isPopupOpen()) {
                  droneMarkers[key].openPopup();
                }
              }
              if (pilotMarkers[key]) {
                pilotMarkers[key].setPopupContent(generatePopupContent(tracked_pairs[key], 'pilot'));
                if (pilotMarkers[key].isPopupOpen()) {
                  pilotMarkers[key].openPopup();
                }
              }
            }
        } else {
            alert("FAA API error: " + result.message);
        }
    } catch(error) {
        console.error("Error querying FAA API:", error);
    } finally {
        const button = document.getElementById("queryFaaButton_" + mac);
        if (button) {
            button.disabled = false;
            button.style.backgroundColor = "#333";
            button.textContent = "Query FAA API";
        }
    }
}

function lockMarker(markerType, id) {
  // Remember previous lock so we can clear its buttons
  const prevId = followLock.id;
  // Set new lock
  followLock = { type: markerType, id: id, enabled: true };
  // Update buttons for this id in both drone and pilot sections
  updateMarkerButtons('drone', id);
  updateMarkerButtons('pilot', id);
  localStorage.setItem('followLock', JSON.stringify(followLock));
  // If another id was locked before, clear its button states
  if (prevId && prevId !== id) {
    updateMarkerButtons('drone', prevId);
    updateMarkerButtons('pilot', prevId);
  }
}

function unlockMarker(markerType, id) {
  if (followLock.enabled && followLock.type === markerType && followLock.id === id) {
    // Clear the lock
    followLock = { type: null, id: null, enabled: false };
    // Update buttons for this id in both drone and pilot sections
    updateMarkerButtons('drone', id);
    updateMarkerButtons('pilot', id);
    localStorage.setItem('followLock', JSON.stringify(followLock));
  }
}

function updateMarkerButtons(markerType, id) {
  var isLocked = (followLock.enabled && followLock.type === markerType && followLock.id === id);
  var lockBtn = document.getElementById("lock-" + markerType + "-" + id);
  var unlockBtn = document.getElementById("unlock-" + markerType + "-" + id);
  if(lockBtn) { lockBtn.style.backgroundColor = isLocked ? "green" : ""; lockBtn.textContent = isLocked ? "Locked on " + markerType.charAt(0).toUpperCase() + markerType.slice(1) : "Lock on " + markerType.charAt(0).toUpperCase() + markerType.slice(1); }
  if(unlockBtn) { unlockBtn.style.backgroundColor = isLocked ? "" : "green"; unlockBtn.textContent = isLocked ? "Unlock " + markerType.charAt(0).toUpperCase() + markerType.slice(1) : "Unlocked " + markerType.charAt(0).toUpperCase() + markerType.slice(1); }
}

function openAliasPopup(mac) {
  let detection = window.tracked_pairs[mac] || {};
  let content = generatePopupContent(Object.assign({mac: mac}, detection), 'alias');
  if (droneMarkers[mac]) {
    droneMarkers[mac].setPopupContent(content).openPopup();
  } else if (pilotMarkers[mac]) {
    pilotMarkers[mac].setPopupContent(content).openPopup();
  } else {
    L.popup({className: 'leaflet-popup-content-wrapper'})
      .setLatLng(map.getCenter())
      .setContent(content)
      .openOn(map);
  }
}

// Updated saveAlias: now it updates the open popup without closing it.
async function saveAlias(mac) {
  let alias = document.getElementById("aliasInput").value;
  try {
    const response = await fetch(window.location.origin + '/api/set_alias', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mac: mac, alias: alias}) });
    const data = await response.json();
    if (data.status === "ok") {
      // Immediately update local alias map so popup content uses new alias
      aliases[mac] = alias;
      updateAliases();
      let detection = window.tracked_pairs[mac] || {mac: mac};
      let content = generatePopupContent(detection, 'alias');
      let currentPopup = map.getPopup();
      if (currentPopup) {
         currentPopup.setContent(content);
      } else {
         L.popup().setContent(content).openOn(map);
      }
      // Immediately update the drone list aliases
      updateComboList(window.tracked_pairs);
      // Flash the updated alias in the popup
      const aliasSpan = document.getElementById('aliasDisplay_' + mac);
      if (aliasSpan) {
        aliasSpan.textContent = alias;
        // Force reflow to apply immediate flash
        aliasSpan.getBoundingClientRect();
        const prevBg = aliasSpan.style.backgroundColor;
        aliasSpan.style.backgroundColor = 'purple';
        setTimeout(() => { aliasSpan.style.backgroundColor = prevBg; }, 300);
      }
      // Ensure the alias list updates immediately
      updateComboList(window.tracked_pairs);
    }
  } catch (error) { console.error("Error saving alias:", error); }
}

async function clearAlias(mac) {
  try {
    const response = await fetch(window.location.origin + '/api/clear_alias/' + mac, {method: 'POST'});
    const data = await response.json();
    if (data.status === "ok") {
      updateAliases();
      let detection = window.tracked_pairs[mac] || {mac: mac};
      let content = generatePopupContent(detection, 'alias');
      L.popup().setContent(content).openOn(map);
      // Immediately update the drone list aliases
      updateComboList(window.tracked_pairs);
    }
  } catch (error) { console.error("Error clearing alias:", error); }
}

const osmStandard = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap contributors',
  maxNativeZoom: 19,
  maxZoom: 22,
});
const osmHumanitarian = L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
  attribution: '© Humanitarian OpenStreetMap Team',
  maxNativeZoom: 19,
  maxZoom: 22,
});
const cartoPositron = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '© OpenStreetMap contributors, © CARTO',
  maxNativeZoom: 19,
  maxZoom: 22,
});
const cartoDarkMatter = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '© OpenStreetMap contributors, © CARTO',
  maxNativeZoom: 19,
  maxZoom: 22,
});
const esriWorldImagery = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  attribution: 'Tiles © Esri',
  maxNativeZoom: 19,
  maxZoom: 22,
});
const esriWorldTopo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
  attribution: 'Tiles © Esri',
  maxNativeZoom: 19,
  maxZoom: 22,
});
const esriDarkGray = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
  attribution: 'Tiles © Esri',
  maxNativeZoom: 16,
  maxZoom: 16,
});
const openTopoMap = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenTopoMap contributors',
  maxNativeZoom: 17,
  maxZoom: 17,
});

  // Load persisted basemap selection or default to satellite imagery
  var persistedBasemap = localStorage.getItem('basemap') || 'esriWorldImagery';
  document.getElementById('layerSelect').value = persistedBasemap;
  var initialLayer;
  switch(persistedBasemap) {
    case 'osmStandard': initialLayer = osmStandard; break;
    case 'osmHumanitarian': initialLayer = osmHumanitarian; break;
    case 'cartoPositron': initialLayer = cartoPositron; break;
    case 'cartoDarkMatter': initialLayer = cartoDarkMatter; break;
    case 'esriWorldImagery': initialLayer = esriWorldImagery; break;
    case 'esriWorldTopo': initialLayer = esriWorldTopo; break;
    case 'esriDarkGray': initialLayer = esriDarkGray; break;
    case 'openTopoMap': initialLayer = openTopoMap; break;
    default: initialLayer = esriWorldImagery;
  }

const map = L.map('map', {
  center: persistedCenter || [0, 0],
  zoom: persistedZoom || 2,
  layers: [initialLayer],
  attributionControl: false,
  maxZoom: initialLayer.options.maxZoom
});
var canvasRenderer = L.canvas();
// create custom Leaflet panes for z-ordering
map.createPane('pilotCirclePane');
map.getPane('pilotCirclePane').style.zIndex = 600;
map.createPane('pilotIconPane');
map.getPane('pilotIconPane').style.zIndex = 601;
map.createPane('droneCirclePane');
map.getPane('droneCirclePane').style.zIndex = 650;
map.createPane('droneIconPane');
map.getPane('droneIconPane').style.zIndex = 651;

map.on('moveend zoomend', function() {
  let center = map.getCenter();
  let zoom = map.getZoom();
  localStorage.setItem('mapCenter', JSON.stringify(center));
  localStorage.setItem('mapZoom', zoom);
});

// Update marker icon sizes whenever the map zoom changes
map.on('zoomend', function() {
  // Scale circle and ring radii based on current zoom
  const zoomLevel = map.getZoom();
  const size = Math.max(12, Math.min(zoomLevel * 1.5, 24));
  const circleRadius = size * 0.45;
  Object.keys(droneMarkers).forEach(mac => {
    const color = get_color_for_mac(mac);
    droneMarkers[mac].setIcon(createIcon('🛸', color));
  });
  Object.keys(pilotMarkers).forEach(mac => {
    const color = get_color_for_mac(mac);
    pilotMarkers[mac].setIcon(createIcon('👤', color));
  });
  // Update circle marker sizes
  Object.values(droneCircles).forEach(circle => circle.setRadius(circleRadius));
  Object.values(pilotCircles).forEach(circle => circle.setRadius(circleRadius));
  // Update broadcast ring sizes
  Object.values(droneBroadcastRings).forEach(ring => ring.setRadius(size * 0.34));
  // Update observer icon size based on zoom level
  if (observerMarker) {
    const storedObserverEmoji = localStorage.getItem('observerEmoji') || "😎";
    observerMarker.setIcon(createIcon(storedObserverEmoji, 'blue'));
  }
});

document.getElementById("layerSelect").addEventListener("change", function() {
  let value = this.value;
  let newLayer;
  if (value === "osmStandard") newLayer = osmStandard;
  else if (value === "osmHumanitarian") newLayer = osmHumanitarian;
  else if (value === "cartoPositron") newLayer = cartoPositron;
  else if (value === "cartoDarkMatter") newLayer = cartoDarkMatter;
  else if (value === "esriWorldImagery") newLayer = esriWorldImagery;
  else if (value === "esriWorldTopo") newLayer = esriWorldTopo;
  else if (value === "esriDarkGray") newLayer = esriDarkGray;
  else if (value === "openTopoMap") newLayer = openTopoMap;
  map.eachLayer(function(layer) {
    if (layer.options && layer.options.attribution) { map.removeLayer(layer); }
  });
  newLayer.addTo(map);
  newLayer.redraw();
  // Clamp zoom to the layer's allowed maxZoom to avoid missing tiles
  const maxAllowed = newLayer.options.maxZoom;
  if (map.getZoom() > maxAllowed) {
    map.setZoom(maxAllowed);
  }
  // update map's allowed max zoom for this layer
  map.options.maxZoom = maxAllowed;
  localStorage.setItem('basemap', value);
  this.style.backgroundColor = "rgba(0,0,0,0.8)";
  this.style.color = "#FF00FF";
  setTimeout(() => { this.style.backgroundColor = "rgba(0,0,0,0.8)"; this.style.color = "#FF00FF"; }, 500);
});

let persistentMACs = [];
const droneMarkers = {};
const pilotMarkers = {};
const droneCircles = {};
const pilotCircles = {};
const dronePolylines = {};
const pilotPolylines = {};
const dronePathCoords = {};
const pilotPathCoords = {};
const droneBroadcastRings = {};
let historicalDrones = window.historicalDrones;
let firstDetectionZoomed = false;

let observerMarker = null;

if (navigator.geolocation) {
  navigator.geolocation.watchPosition(function(position) {
    const lat = position.coords.latitude;
    const lng = position.coords.longitude;
    // Use stored observer emoji or default to "😎"
    const storedObserverEmoji = localStorage.getItem('observerEmoji') || "😎";
    const observerIcon = createIcon(storedObserverEmoji, 'blue');
    if (!observerMarker) {
      observerMarker = L.marker([lat, lng], {icon: observerIcon})
                        .bindPopup(generateObserverPopup())
                        .addTo(map)
                        .on('popupopen', function() { updateObserverPopupButtons(); })
                        .on('click', function() { safeSetView(observerMarker.getLatLng(), 18); });
    } else { observerMarker.setLatLng([lat, lng]); }
  }, function(error) { console.error("Error watching location:", error); }, { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 });
} else { console.error("Geolocation is not supported by this browser."); }

function zoomToDrone(mac, detection) {
  // Only zoom if we have valid, non-zero coordinates
  if (
    detection &&
    detection.drone_lat !== undefined &&
    detection.drone_long !== undefined &&
    detection.drone_lat !== 0 &&
    detection.drone_long !== 0
  ) {
    safeSetView([detection.drone_lat, detection.drone_long], 18);
  }
}

function showHistoricalDrone(mac, detection) {
  // Only map drones with valid, non-zero coordinates
  if (
    detection.drone_lat === undefined ||
    detection.drone_long === undefined ||
    detection.drone_lat === 0 ||
    detection.drone_long === 0
  ) {
    return;
  }
  const color = get_color_for_mac(mac);
  if (!droneMarkers[mac]) {
    droneMarkers[mac] = L.marker([detection.drone_lat, detection.drone_long], {
      icon: createIcon('🛸', color),
      pane: 'droneIconPane'
    })
                           .bindPopup(generatePopupContent(detection, 'drone'))
                           .addTo(map)
                           .on('click', function(){ map.setView(this.getLatLng(), map.getZoom()); });
  } else {
    droneMarkers[mac].setLatLng([detection.drone_lat, detection.drone_long]);
    droneMarkers[mac].setPopupContent(generatePopupContent(detection, 'drone'));
  }
  if (!droneCircles[mac]) {
    const zoomLevel = map.getZoom();
    const size = Math.max(12, Math.min(zoomLevel * 1.5, 24));
    droneCircles[mac] = L.circleMarker([detection.drone_lat, detection.drone_long],
                                       {
                                         renderer: canvasRenderer,
                                         pane: 'droneCirclePane',
                                         radius: size * 0.45,
                                         color: color,
                                         fillColor: color,
                                         fillOpacity: 0.7
                                       })
                           .addTo(map);
  } else { droneCircles[mac].setLatLng([detection.drone_lat, detection.drone_long]); }
  if (!dronePathCoords[mac]) { dronePathCoords[mac] = []; }
  const lastDrone = dronePathCoords[mac][dronePathCoords[mac].length - 1];
  if (!lastDrone || lastDrone[0] != detection.drone_lat || lastDrone[1] != detection.drone_long) { dronePathCoords[mac].push([detection.drone_lat, detection.drone_long]); }
  if (dronePolylines[mac]) { map.removeLayer(dronePolylines[mac]); }
  dronePolylines[mac] = L.polyline(dronePathCoords[mac], {
    renderer: canvasRenderer,
    color: color
  }).addTo(map);
  if (detection.pilot_lat && detection.pilot_long && detection.pilot_lat != 0 && detection.pilot_long != 0) {
    if (!pilotMarkers[mac]) {
      pilotMarkers[mac] = L.marker([detection.pilot_lat, detection.pilot_long], {
        icon: createIcon('👤', color),
        pane: 'pilotIconPane'
      })
                             .bindPopup(generatePopupContent(detection, 'pilot'))
                             .addTo(map)
                             .on('click', function(){ map.setView(this.getLatLng(), map.getZoom()); });
    } else {
      pilotMarkers[mac].setLatLng([detection.pilot_lat, detection.pilot_long]);
      pilotMarkers[mac].setPopupContent(generatePopupContent(detection, 'pilot'));
    }
    if (!pilotCircles[mac]) {
      const zoomLevel = map.getZoom();
      const size = Math.max(12, Math.min(zoomLevel * 1.5, 24));
      pilotCircles[mac] = L.circleMarker([detection.pilot_lat, detection.pilot_long],
                                          {
                                            renderer: canvasRenderer,
                                            pane: 'pilotCirclePane',
                                            radius: size * 0.34,
                                            color: color,
                                            fillColor: color,
                                            fillOpacity: 0.7
                                          })
                            .addTo(map);
    } else { pilotCircles[mac].setLatLng([detection.pilot_lat, detection.pilot_long]); }
    // Historical pilot path (dotted)
    if (!pilotPathCoords[mac]) { pilotPathCoords[mac] = []; }
    const lastPilotHis = pilotPathCoords[mac][pilotPathCoords[mac].length - 1];
    if (!lastPilotHis || lastPilotHis[0] !== detection.pilot_lat || lastPilotHis[1] !== detection.pilot_long) {
      pilotPathCoords[mac].push([detection.pilot_lat, detection.pilot_long]);
    }
    if (pilotPolylines[mac]) { map.removeLayer(pilotPolylines[mac]); }
    pilotPolylines[mac] = L.polyline(pilotPathCoords[mac], {
      renderer: canvasRenderer,
      color: color,
      dashArray: '5,5'
    }).addTo(map);
  }
}

function colorFromMac(mac) {
  let hash = 0;
  for (let i = 0; i < mac.length; i++) { hash = mac.charCodeAt(i) + ((hash << 5) - hash); }
  let h = Math.abs(hash) % 360;
  return 'hsl(' + h + ', 70%, 50%)';
}

function get_color_for_mac(mac) {
  if (colorOverrides.hasOwnProperty(mac)) { return "hsl(" + colorOverrides[mac] + ", 70%, 50%)"; }
  return colorFromMac(mac);
}

function updateComboList(data) {
  const activePlaceholder = document.getElementById("activePlaceholder");
  const inactivePlaceholder = document.getElementById("inactivePlaceholder");
  const currentTime = Date.now() / 1000;
  
  persistentMACs.forEach(mac => {
    let detection = data[mac];
    let isActive = detection && ((currentTime - detection.last_update) <= STALE_THRESHOLD);
    let item = comboListItems[mac];
    if (!item) {
      item = document.createElement("div");
      comboListItems[mac] = item;
      item.className = "drone-item";
      item.addEventListener("dblclick", () => {
         restorePaths();
         if (historicalDrones[mac]) {
             delete historicalDrones[mac];
             localStorage.setItem('historicalDrones', JSON.stringify(historicalDrones));
             if (droneMarkers[mac]) { map.removeLayer(droneMarkers[mac]); delete droneMarkers[mac]; }
             if (pilotMarkers[mac]) { map.removeLayer(pilotMarkers[mac]); delete pilotMarkers[mac]; }
             item.classList.remove("selected");
             map.closePopup();
         } else {
             historicalDrones[mac] = Object.assign({}, detection, { userLocked: true, lockTime: Date.now()/1000 });
             localStorage.setItem('historicalDrones', JSON.stringify(historicalDrones));
             showHistoricalDrone(mac, historicalDrones[mac]);
             item.classList.add("selected");
             openAliasPopup(mac);
             if (detection && detection.drone_lat && detection.drone_long && detection.drone_lat != 0 && detection.drone_long != 0) {
                 safeSetView([detection.drone_lat, detection.drone_long], 18);
             }
         }
      });
    }
    item.textContent = aliases[mac] ? aliases[mac] : mac;
    const color = get_color_for_mac(mac);
    item.style.borderColor = color;
    item.style.color = color;
    
    // Handle no-GPS styling with 5-second transmission timeout
    const det = data[mac];
    const hasGps = det && det.drone_lat && det.drone_long && det.drone_lat !== 0 && det.drone_long !== 0;
    const hasRecentTransmission = det && det.last_update && ((currentTime - det.last_update) <= 5);
    
    // Apply no-GPS styling only if drone has no GPS AND has recent transmission (within 5 seconds)
    if (!hasGps && hasRecentTransmission) {
      item.classList.add('no-gps');
    } else {
      item.classList.remove('no-gps');
    }
    
    // Mark items seen in the last 5 seconds
    const isRecent = detection && ((currentTime - detection.last_update) <= 5);
    item.classList.toggle('recent', isRecent);
    if (isActive) {
      if (item.parentNode !== activePlaceholder) { activePlaceholder.appendChild(item); }
    } else {
      if (item.parentNode !== inactivePlaceholder) { inactivePlaceholder.appendChild(item); }
    }
  });
}

// Only zoom on truly new detections—never on the initial restore
var initialLoad    = true;
var seenDrones     = {};
var seenAliased    = {};
var previousActive = {};
// Initialize seenDrones and previousActive from persisted trackedPairs to suppress reload popups
(function() {
  const stored = localStorage.getItem("trackedPairs");
  if (stored) {
    try {
      const storedPairs = JSON.parse(stored);
      for (const mac in storedPairs) {
        seenDrones[mac] = true;
        // previousActive[mac] = true;
      }
    } catch(e) { console.error("Failed to parse persisted trackedPairs", e); }
  }
})();
async function updateData() {
  try {
    const response = await fetch(window.location.origin + '/api/detections')
    const data = await response.json();
    window.tracked_pairs = data;
    // Persist current detection data to localStorage so that markers & paths remain on reload.
    localStorage.setItem("trackedPairs", JSON.stringify(data));
    const currentTime = Date.now() / 1000;
    for (const mac in data) { if (!persistentMACs.includes(mac)) { persistentMACs.push(mac); } }
    for (const mac in data) {
      if (historicalDrones[mac]) {
        if (data[mac].last_update > historicalDrones[mac].lockTime || (currentTime - historicalDrones[mac].lockTime) > STALE_THRESHOLD) {
          delete historicalDrones[mac];
          localStorage.setItem('historicalDrones', JSON.stringify(historicalDrones));
          if (droneBroadcastRings[mac]) { map.removeLayer(droneBroadcastRings[mac]); delete droneBroadcastRings[mac]; }
        } else { continue; }
      }
      const det = data[mac];
      if (!det.last_update || (currentTime - det.last_update > STALE_THRESHOLD)) {
        if (droneMarkers[mac]) { map.removeLayer(droneMarkers[mac]); delete droneMarkers[mac]; }
        if (pilotMarkers[mac]) { map.removeLayer(pilotMarkers[mac]); delete pilotMarkers[mac]; }
        if (droneCircles[mac]) { map.removeLayer(droneCircles[mac]); delete droneCircles[mac]; }
        if (pilotCircles[mac]) { map.removeLayer(pilotCircles[mac]); delete pilotCircles[mac]; }
        if (dronePolylines[mac]) { map.removeLayer(dronePolylines[mac]); delete dronePolylines[mac]; }
        if (pilotPolylines[mac]) { map.removeLayer(pilotPolylines[mac]); delete pilotPolylines[mac]; }
        if (droneBroadcastRings[mac]) { map.removeLayer(droneBroadcastRings[mac]); delete droneBroadcastRings[mac]; }
        delete dronePathCoords[mac];
        delete pilotPathCoords[mac];
        // Mark as inactive to enable revival popups
        previousActive[mac] = false;
        continue;
      }
      const droneLat = det.drone_lat, droneLng = det.drone_long;
      const pilotLat = det.pilot_lat, pilotLng = det.pilot_long;
      const validDrone = (droneLat !== 0 && droneLng !== 0);
      // State-change popup logic
      const alias     = aliases[mac];
      // New state calculation: consider time-based staleness
      const activeNow = validDrone && det.last_update && (currentTime - det.last_update <= STALE_THRESHOLD);
      const wasActive = previousActive[mac] || false;
      const isNew     = !seenDrones[mac];

      // Only fire popup on transition from inactive to active, after initial load, and within stale threshold
      // ALSO handle no-GPS drones here in centralized popup logic
      const hasGps = validDrone || (pilotLat !== 0 && pilotLng !== 0);
      const hasRecentTransmission = det.last_update && (currentTime - det.last_update <= 5);
      const isNoGpsDrone = !hasGps && hasRecentTransmission;
      
      let shouldShowPopup = false;
      let popupIsNew = false;
      
      if (!initialLoad && det.last_update && (currentTime - det.last_update <= STALE_THRESHOLD)) {
        // GPS drone popup logic
        if (!wasActive && activeNow) {
          shouldShowPopup = true;
          popupIsNew = alias ? false : !seenDrones[mac];
        }
        // No-GPS drone popup logic (centralized here)
        else if (isNoGpsDrone && !alertedNoGpsDrones.has(mac)) {
          shouldShowPopup = true;
          popupIsNew = true;
        }
      }
      
      if (shouldShowPopup) {
        showTerminalPopup(det, popupIsNew);
        seenDrones[mac] = true;
        if (isNoGpsDrone) {
          alertedNoGpsDrones.add(mac);
        }
      }
      // Persist for next update
      previousActive[mac] = activeNow;

      const validPilot = (pilotLat !== 0 && pilotLng !== 0);
      
      // Handle no-GPS drones that are still transmitting (mapping only, no popup)
      if (isNoGpsDrone) {
        // Ensure this MAC is in the persistent list for display
        if (!persistentMACs.includes(mac)) { persistentMACs.push(mac); }
      } else if (!hasRecentTransmission) {
        // Reset alert state when transmission stops
        alertedNoGpsDrones.delete(mac);
      }
      
      if (!validDrone && !validPilot) continue;
      const color = get_color_for_mac(mac);
      // First detection zoom block (keep this block only)
      if (!initialLoad && !firstDetectionZoomed && validDrone) {
        firstDetectionZoomed = true;
        safeSetView([droneLat, droneLng], 18);
      }
      if (validDrone) {
        if (droneMarkers[mac]) {
          droneMarkers[mac].setLatLng([droneLat, droneLng]);
          if (!droneMarkers[mac].isPopupOpen()) { droneMarkers[mac].setPopupContent(generatePopupContent(det, 'drone')); }
        } else {
          droneMarkers[mac] = L.marker([droneLat, droneLng], {
            icon: createIcon('🛸', color),
            pane: 'droneIconPane'
          })
                                .bindPopup(generatePopupContent(det, 'drone'))
                                .addTo(map)
                                // Remove automatic zoom on marker click:
                                //.on('click', function(){ map.setView(this.getLatLng(), map.getZoom()); });
                                ;
        }
        if (droneCircles[mac]) { droneCircles[mac].setLatLng([droneLat, droneLng]); }
        else {
          const zoomLevel = map.getZoom();
          const size = Math.max(12, Math.min(zoomLevel * 1.5, 24));
          droneCircles[mac] = L.circleMarker([droneLat, droneLng], {
            pane: 'droneCirclePane',
            radius: size * 0.45,
            color: color,
            fillColor: color,
            fillOpacity: 0.7
          }).addTo(map);
        }
        if (!dronePathCoords[mac]) { dronePathCoords[mac] = []; }
        const lastDrone = dronePathCoords[mac][dronePathCoords[mac].length - 1];
        if (!lastDrone || lastDrone[0] != droneLat || lastDrone[1] != droneLng) { dronePathCoords[mac].push([droneLat, droneLng]); }
        if (dronePolylines[mac]) { map.removeLayer(dronePolylines[mac]); }
        dronePolylines[mac] = L.polyline(dronePathCoords[mac], {color: color}).addTo(map);
        if (currentTime - det.last_update <= 5) {
          const dynamicRadius = getDynamicSize() * 0.45;
          const ringWeight = 3 * 0.8;  // 20% thinner
          const ringRadius = dynamicRadius + ringWeight / 2;  // sit just outside the main circle
          if (droneBroadcastRings[mac]) {
            droneBroadcastRings[mac].setLatLng([droneLat, droneLng]);
            droneBroadcastRings[mac].setRadius(ringRadius);
            droneBroadcastRings[mac].setStyle({ weight: ringWeight });
          } else {
            droneBroadcastRings[mac] = L.circleMarker([droneLat, droneLng], {
              pane: 'droneCirclePane',
              radius: ringRadius,
              color: "lime",
              fill: false,
              weight: ringWeight
            }).addTo(map);
          }
        } else {
          if (droneBroadcastRings[mac]) {
            map.removeLayer(droneBroadcastRings[mac]);
            delete droneBroadcastRings[mac];
          }
        }
        // Remove automatic follow-zoom (except for followLock, which is allowed)
        // (auto-zoom disabled except for followLock)
        if (followLock.enabled && followLock.type === 'drone' && followLock.id === mac) { map.setView([droneLat, droneLng], map.getZoom()); }
      }
      if (validPilot) {
        if (pilotMarkers[mac]) {
          pilotMarkers[mac].setLatLng([pilotLat, pilotLng]);
          if (!pilotMarkers[mac].isPopupOpen()) { pilotMarkers[mac].setPopupContent(generatePopupContent(det, 'pilot')); }
        } else {
          pilotMarkers[mac] = L.marker([pilotLat, pilotLng], {
            icon: createIcon('👤', color),
            pane: 'pilotIconPane'
          })
                                .bindPopup(generatePopupContent(det, 'pilot'))
                                .addTo(map)
                                // Remove automatic zoom on marker click:
                                //.on('click', function(){ map.setView(this.getLatLng(), map.getZoom()); });
                                ;
        }
        if (pilotCircles[mac]) { pilotCircles[mac].setLatLng([pilotLat, pilotLng]); }
        else {
          const zoomLevel = map.getZoom();
          const size = Math.max(12, Math.min(zoomLevel * 1.5, 24));
          pilotCircles[mac] = L.circleMarker([pilotLat, pilotLng], {
            pane: 'pilotCirclePane',
            radius: size * 0.34,
            color: color,
            fillColor: color,
            fillOpacity: 0.7
          }).addTo(map);
        }
        if (!pilotPathCoords[mac]) { pilotPathCoords[mac] = []; }
        const lastPilot = pilotPathCoords[mac][pilotPathCoords[mac].length - 1];
        if (!lastPilot || lastPilot[0] != pilotLat || lastPilot[1] != pilotLng) { pilotPathCoords[mac].push([pilotLat, pilotLng]); }
        if (pilotPolylines[mac]) { map.removeLayer(pilotPolylines[mac]); }
        pilotPolylines[mac] = L.polyline(pilotPathCoords[mac], {color: color, dashArray: '5,5'}).addTo(map);
        // Remove automatic follow-zoom (except for followLock, which is allowed)
        // (auto-zoom disabled except for followLock)
        if (followLock.enabled && followLock.type === 'pilot' && followLock.id === mac) { map.setView([pilotLat, pilotLng], map.getZoom()); }
      }
      // At end of loop iteration, remember this state for next time
      previousActive[mac] = validDrone;
    }
    initialLoad = false;
    updateComboList(data);
    updateAliases();
    // Mark that the first restore/update is done
    initialLoad = false;

    // Handle no-GPS styling and alerts in the inactive list
    for (const mac in data) {
      const det = data[mac];
      const droneElem = comboListItems[mac];
      if (!droneElem) continue;
      
      const hasGps = det.drone_lat && det.drone_long && det.drone_lat !== 0 && det.drone_long !== 0;
      const hasRecentTransmission = det.last_update && ((currentTime - det.last_update) <= 5);
      
      if (!hasGps && hasRecentTransmission) {
        // Apply no-GPS styling and one-time alert for drones with no GPS but recent transmission
        droneElem.classList.add('no-gps');
        if (!alertedNoGpsDrones.has(det.mac)) {
          // Duplicate alert removed - already handled in main loop
          // showTerminalPopup(det, true);
          alertedNoGpsDrones.add(det.mac);
        }
      } else {
        // Remove no-GPS styling and reset alert state when GPS is acquired or transmission stops
        droneElem.classList.remove('no-gps');
        if (!hasRecentTransmission) {
          alertedNoGpsDrones.delete(det.mac);
        }
      }
    }
  } catch (error) { console.error("Error fetching detection data:", error); }
}

function createIcon(emoji, color) {
  // Compute a dynamic size based on zoom
  const size = getDynamicSize();
  const actualSize = emoji === '👤' ? Math.round(size * 0.7) : Math.round(size);
  const isize = actualSize;
  const half = Math.round(actualSize / 2);
  return L.divIcon({
    html: `<div style="width:${isize}px; height:${isize}px; font-size:${isize}px; color:${color}; text-align:center; line-height:${isize}px;">${emoji}</div>`,
    className: '',
    iconSize: [isize, isize],
    iconAnchor: [half, half]
  });
}

function getDynamicSize() {
  const zoomLevel = map.getZoom();
  // Clamp between 12px and 24px, then boost by 15%
  const base = Math.max(12, Math.min(zoomLevel * 1.5, 24));
  return base * 1.15;
}

// Updated function: now updates all selected USB port statuses.
async function updateSerialStatus() {
  try {
    const response = await fetch(window.location.origin + '/api/serial_status')
    const data = await response.json();
    const statusDiv = document.getElementById('serialStatus');
    statusDiv.innerHTML = "";
    if (data.statuses) {
      for (const port in data.statuses) {
        const div = document.createElement("div");
        // Device name in neon pink and status color accordingly.
        div.innerHTML = '<span class="usb-name">' + port + '</span>: ' +
          (data.statuses[port] ? '<span style="color: lime;">Connected</span>' : '<span style="color: red;">Disconnected</span>');
        statusDiv.appendChild(div);
      }
    }
  } catch (error) { console.error("Error fetching serial status:", error); }
}
setInterval(updateSerialStatus, 1000);
updateSerialStatus();

// (Node Mode mainSwitch and polling interval are now managed solely by the DOMContentLoaded handler above.)
// Sync popup Node Mode toggle when a popup opens

function updateLockFollow() {
  if (followLock.enabled) {
    if (followLock.type === 'observer' && observerMarker) { map.setView(observerMarker.getLatLng(), map.getZoom()); }
    else if (followLock.type === 'drone' && droneMarkers[followLock.id]) { map.setView(droneMarkers[followLock.id].getLatLng(), map.getZoom()); }
    else if (followLock.type === 'pilot' && pilotMarkers[followLock.id]) { map.setView(pilotMarkers[followLock.id].getLatLng(), map.getZoom()); }
  }
}
setInterval(updateLockFollow, 200);

document.getElementById("filterToggle").addEventListener("click", function() {
  const box = document.getElementById("filterBox");
  const isCollapsed = box.classList.toggle("collapsed");
  this.textContent = isCollapsed ? "[+]" : "[-]";
  // Sync Node Mode toggle with stored setting when filter opens
  const mainSwitch = document.getElementById('nodeModeMainSwitch');
  mainSwitch.checked = (localStorage.getItem('nodeMode') === 'true');
});

async function restorePaths() {
  try {
    const response = await fetch(window.location.origin + '/api/paths')
    const data = await response.json();
    for (const mac in data.dronePaths) {
      let isActive = false;
      if (tracked_pairs[mac] && ((Date.now()/1000) - tracked_pairs[mac].last_update) <= STALE_THRESHOLD) { isActive = true; }
      if (!isActive && !historicalDrones[mac]) continue;
      dronePathCoords[mac] = data.dronePaths[mac];
      if (dronePolylines[mac]) { map.removeLayer(dronePolylines[mac]); }
      const color = get_color_for_mac(mac);
      dronePolylines[mac] = L.polyline(dronePathCoords[mac], {color: color}).addTo(map);
    }
    for (const mac in data.pilotPaths) {
      let isActive = false;
      if (tracked_pairs[mac] && ((Date.now()/1000) - tracked_pairs[mac].last_update) <= STALE_THRESHOLD) { isActive = true; }
      if (!isActive && !historicalDrones[mac]) continue;
      pilotPathCoords[mac] = data.pilotPaths[mac];
      if (pilotPolylines[mac]) { map.removeLayer(pilotPolylines[mac]); }
      const color = get_color_for_mac(mac);
      pilotPolylines[mac] = L.polyline(pilotPathCoords[mac], {color: color, dashArray: '5,5'}).addTo(map);
    }
  } catch (error) { console.error("Error restoring paths:", error); }
}
setInterval(restorePaths, 200);
restorePaths();

function updateColor(mac, hue) {
  hue = parseInt(hue);
  colorOverrides[mac] = hue;
  localStorage.setItem('colorOverrides', JSON.stringify(colorOverrides));
  var newColor = "hsl(" + hue + ", 70%, 50%)";
  if (droneMarkers[mac]) { droneMarkers[mac].setIcon(createIcon('🛸', newColor)); droneMarkers[mac].setPopupContent(generatePopupContent(tracked_pairs[mac], 'drone')); }
  if (pilotMarkers[mac]) { pilotMarkers[mac].setIcon(createIcon('👤', newColor)); pilotMarkers[mac].setPopupContent(generatePopupContent(tracked_pairs[mac], 'pilot')); }
  if (droneCircles[mac]) { droneCircles[mac].setStyle({ color: newColor, fillColor: newColor }); }
  if (pilotCircles[mac]) { pilotCircles[mac].setStyle({ color: newColor, fillColor: newColor }); }
  if (dronePolylines[mac]) { dronePolylines[mac].setStyle({ color: newColor }); }
  if (pilotPolylines[mac]) { pilotPolylines[mac].setStyle({ color: newColor }); }
  var listItems = document.getElementsByClassName("drone-item");
  for (var i = 0; i < listItems.length; i++) {
    if (listItems[i].textContent.includes(mac)) { listItems[i].style.borderColor = newColor; listItems[i].style.color = newColor; }
  }
}
</script>
<script>
  // Download buttons click handlers with purple flash
  // 3D View Toggle Button
  document.getElementById('toggle3DViewButton').addEventListener('click', function() {
    this.style.backgroundColor = 'purple';
    setTimeout(() => { this.style.backgroundColor = '#36C3FF'; }, 300);
    window.location.href = '/3d';
  });
  
  document.getElementById('downloadCsv').addEventListener('click', function() {
    this.style.backgroundColor = 'purple';
    setTimeout(() => { this.style.backgroundColor = '#333'; }, 300);
    window.location.href = '/download/csv';
  });
  document.getElementById('downloadKml').addEventListener('click', function() {
    this.style.backgroundColor = 'purple';
    setTimeout(() => { this.style.backgroundColor = '#333'; }, 300);
    window.location.href = '/download/kml';
  });
  document.getElementById('downloadAliases').addEventListener('click', function() {
    this.style.backgroundColor = 'purple';
    setTimeout(() => { this.style.backgroundColor = '#333'; }, 300);
    window.location.href = '/download/aliases';
  });
  document.getElementById('downloadCumulativeCsv').addEventListener('click', function() {
    window.location = '/download/cumulative_detections.csv';
  });
  document.getElementById('downloadCumulativeKml').addEventListener('click', function() {
    window.location = '/download/cumulative.kml';
  });
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
      .then(reg => console.log('Service Worker registered', reg))
      .catch(err => console.error('Service Worker registration failed', err));
  }
</script>
</body>
</html>
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
      .then(reg => console.log('Service Worker registered', reg))
      .catch(err => console.error('Service Worker registration failed', err));
  }
</script>
'''
# ----------------------
# New route: USB port selection for multiple ports.
# ----------------------
@app.route('/sw.js')
def service_worker():
    sw_code = '''
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open('tile-cache').then(function(cache) {
      return cache.addAll([]);
    })
  );
});
self.addEventListener('fetch', function(event) {
  var url = event.request.url;
  // Only cache tile requests
  if (url.includes('tile.openstreetmap.org') || url.includes('basemaps.cartocdn.com') || url.includes('server.arcgisonline.com') || url.includes('tile.opentopomap.org')) {
    event.respondWith(
      caches.open('tile-cache').then(function(cache) {
        return cache.match(event.request).then(function(response) {
          return response || fetch(event.request).then(function(networkResponse) {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        });
      })
    );
  }
});
'''
    response = app.make_response(sw_code)
    response.headers['Content-Type'] = 'application/javascript'
    return response


# ----------------------
# New route: USB port selection for multiple ports.
# ----------------------
@app.route('/select_ports', methods=['GET'])
def select_ports_get():
    ports = list(serial.tools.list_ports.comports())
    return render_template_string(PORT_SELECTION_PAGE, ports=ports, logo_ascii=LOGO_ASCII, bottom_ascii=BOTTOM_ASCII)


@app.route('/select_ports', methods=['POST'])
def select_ports_post():
    global SELECTED_PORTS
    # Get up to 3 ports; ignore empty values
    new_selected_ports = {}
    for i in range(1, 4):
        port = request.form.get(f'port{i}')
        if port:
            new_selected_ports[f'port{i}'] = port

    # Handle webhook URL setting
    webhook_url = request.form.get('webhook_url', '').strip()
    try:
        if webhook_url and not webhook_url.startswith(('http://', 'https://')):
            logger.warning(f"Invalid webhook URL format: {webhook_url}")
        else:
            set_server_webhook_url(webhook_url)
            if webhook_url:
                logger.info(f"Webhook URL updated to: {webhook_url}")
            else:
                logger.info("Webhook URL cleared")
    except Exception as e:
        logger.error(f"Error setting webhook URL: {e}")

    # Close connections to ports that are no longer selected
    with serial_objs_lock:
        for port_key, port_device in SELECTED_PORTS.items():
            if port_key not in new_selected_ports or new_selected_ports[port_key] != port_device:
                # This port is no longer selected or changed, close its connection
                if port_device in serial_objs:
                    try:
                        ser = serial_objs[port_device]
                        if ser and ser.is_open:
                            ser.close()
                            logger.info(f"Closed serial connection to {port_device}")
                    except Exception as e:
                        logger.error(f"Error closing serial connection to {port_device}: {e}")
                    finally:
                        serial_objs.pop(port_device, None)
                        serial_connected_status[port_device] = False
    
    # Update selected ports
    SELECTED_PORTS = new_selected_ports

    # Save selected ports for auto-connection on restart
    save_selected_ports()

    # Start serial-reader threads ONLY for newly selected ports
    for port in SELECTED_PORTS.values():
        # Only start thread if port is not already connected
        if not serial_connected_status.get(port, False):
            serial_connected_status[port] = False
            start_serial_thread(port)
            logger.info(f"Started new serial thread for {port}")
        else:
            logger.debug(f"Port {port} already connected, skipping thread creation")
    
    # Send watchdog reset to each connected microcontroller over USB
    time.sleep(1)  # Give new connections time to establish
    with serial_objs_lock:
        for port, ser in serial_objs.items():
            try:
                if ser and ser.is_open:
                    ser.write(b'WATCHDOG_RESET\n')
                    logger.debug(f"Sent watchdog reset to {port}")
            except Exception as e:
                logger.error(f"Failed to send watchdog reset to {port}: {e}")

    # Redirect to main page
    return redirect(url_for('index'))


# ----------------------
# ASCII art blocks
# ----------------------
BOTTOM_ASCII = r"""
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣄⣠⣀⡀⣀⣠⣤⣤⣤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣄⢠⣠⣼⣿⣿⣿⣟⣿⣿⣿⣿⣿⣿⣿⡿⠋⠀⠀⠀⢠⣤⣦⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠰⢦⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣼⣿⣟⣾⣿⣽⣿⣿⣅⠈⠉⠻⣿⣿⣿⣿⣿⡿⠇⠀⠀⠀⠀⠉⠀⠀⠀⠀⠀⢀⡶⠒⢉⡀⢠⣤⣶⣶⣿⣷⣆⣀⡀⠀⢲⣖⠒⠀⠀⠀⠀⠀⠀⠀
⢀⣤⣾⣶⣦⣤⣤⣶⣿⣿⣿⣿⣿⣿⣽⡿⠻⣷⣀⠀⢻⣿⣿⣿⡿⠟⠀⠀⠀⠀⠀⠀⣤⣶⣶⣤⣀⣀⣬⣷⣦⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣦⣤⣦⣼⣀⠀
⠈⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠛⠓⣿⣿⠟⠁⠘⣿⡟⠁⠀⠘⠛⠁⠀⠀⢠⣾⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠏⠙⠁
⠀⠀⠸⠟⠋⠀⠀⠙⣿⣿⣿⣿⣿⣿⣷⣦⡄⣿⣿⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⣼⣆⢘⣿⣯⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡉⠉⢱⡿⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⡿⠦⠀⠀⠀⠀⠀⠀⠀⠙⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⡗⠀⠈⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⣿⣿⣿⣿⣿⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⢿⣿⣉⣿⡿⢿⢷⣾⣾⣿⣞⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠋⣠⠟⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠹⣿⣿⣿⠿⠿⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣾⣿⣿⣷⣦⣶⣦⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⠈⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠻⣿⣤⡖⠛⠶⠤⡀⠀⠀⠀⠀⠀⠀⠀⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠁⠙⣿⣿⠿⢻⣿⣿⡿⠋⢩⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠙⠧⣤⣦⣤⣄⡀⠀⠀⠀⠀⠀⠘⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠘⣧⠀⠈⣹⡻⠇⢀⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣿⣿⣿⣿⣿⣤⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢽⣿⣿⣿⣿⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠹⣷⣴⣿⣷⢲⣦⣤⡀⢀⡀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢿⣿⣿⣿⣿⣿⣿⠟⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⣿⣷⢀⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠂⠛⣆⣤⡜⣟⠋⠙⠂⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⣿⣿⣿⠟⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⣿⣿⣿⠉⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣤⣾⣿⣿⣿⣿⣿⣆⠀⠰⠄⠀⠉⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⣿⣿⡿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⡿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⠿⠿⣿⣿⣿⠇⠀⠀⢀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣿⡿⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠁⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⠃⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠁⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠒⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
"""

LOGO_ASCII = r"""
        _____                .__      ________          __                 __       
       /     \   ____   _____|  |__   \______ \   _____/  |_  ____   _____/  |_     
      /  \ /  \_/ __ \ /  ___/  |  \   |    |  \_/ __ \   __\/ __ \_/ ___\   __\    
     /    Y    \  ___/ \___ \|   Y  \  |    `   \  ___/|  | \  ___/\  \___|  |      
     \____|__  /\___  >____  >___|  / /_______  /\___  >__|  \___  >\___  >__|      
             \/     \/     \/     \/          \/     \/     \/          \/     \/          
________                                  _____                                     
\______ \_______  ____   ____   ____     /     \ _____  ______ ______   ___________ 
 |    |  \_  __ \/  _ \ /    \_/ __ \   /  \ /  \\__  \ \____ \\____ \_/ __ \_  __ \
 |    `   \  | \(  <_> )   |  \  ___/  /    Y    \/ __ \|  |_> >  |_> >  ___/|  | \/
/_______  /__|   \____/|___|  /\___  > \____|__  (____  /   __/|   __/ \___  >__|   
        \/                  \/     \/          \/     \/|__|   |__|        \/       
"""

@app.route('/3d')
def view_3d():
    """Serve the 3D visualization view"""
    try:
        with open('templates/3d_view.html', 'r') as f:
            return f.read()
    except FileNotFoundError:
        return "3D view template not found", 404

@app.route('/')
def index():
    # Load previously saved ports and attempt auto-connection
    load_selected_ports()
    
    # If no ports are currently selected, try to auto-connect to saved ports
    if len(SELECTED_PORTS) == 0:
        return redirect(url_for('select_ports_get'))
    
    # If we have saved ports but they're not connected, try auto-connecting
    if not any(serial_connected_status.get(port, False) for port in SELECTED_PORTS.values()):
        auto_connected = auto_connect_to_saved_ports()
        if not auto_connected:
            # If auto-connection failed, redirect to port selection
            return redirect(url_for('select_ports_get'))
    
    return HTML_PAGE

@app.route('/api/detections', methods=['GET'])
def api_detections():
    return jsonify(tracked_pairs)

@app.route('/api/detections', methods=['POST'])
def post_detection():
    detection = request.get_json()
    update_detection(detection)
    return jsonify({"status": "ok"}), 200

@app.route('/api/detections_history', methods=['GET'])
def api_detections_history():
    features = []
    for det in detection_history:
        if det.get("drone_lat", 0) == 0 and det.get("drone_long", 0) == 0:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "mac": det.get("mac"),
                "rssi": det.get("rssi"),
                "time": datetime.fromtimestamp(det.get("last_update")).isoformat(),
                "details": det
            },
            "geometry": {
                "type": "Point",
                "coordinates": [det.get("drone_long"), det.get("drone_lat")]
            }
        })
    return jsonify({
        "type": "FeatureCollection",
        "features": features
    })

@app.route('/api/reactivate/<mac>', methods=['POST'])
def reactivate(mac):
    if mac in tracked_pairs:
        tracked_pairs[mac]['last_update'] = time.time()
        tracked_pairs[mac]['status'] = 'active'  # Mark as active when manually reactivated
        print(f"Reactivated {mac}")
        return jsonify({"status": "reactivated", "mac": mac})
    else:
        return jsonify({"status": "error", "message": "MAC not found"}), 404

@app.route('/api/aliases', methods=['GET'])
def api_aliases():
    return jsonify(ALIASES)

@app.route('/api/set_alias', methods=['POST'])
def api_set_alias():
    data = request.get_json()
    mac = data.get("mac")
    alias = data.get("alias")
    if mac:
        ALIASES[mac] = alias
        save_aliases()
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "MAC missing"}), 400

@app.route('/api/clear_alias/<mac>', methods=['POST'])
def api_clear_alias(mac):
    if mac in ALIASES:
        del ALIASES[mac]
        save_aliases()
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "MAC not found"}), 404

# Updated status endpoint: returns a dict of statuses for each selected USB.
@app.route('/api/ports', methods=['GET'])
def api_ports():
    ports = list(serial.tools.list_ports.comports())
    return jsonify({
        'ports': [{'device': p.device, 'description': p.description} for p in ports]
    })

# Updated status endpoint: returns a dict of statuses for each selected USB.
@app.route('/api/serial_status', methods=['GET'])
def api_serial_status():
    return jsonify({"statuses": serial_connected_status})

# New endpoint to get currently selected ports
@app.route('/api/selected_ports', methods=['GET'])
def api_selected_ports():
    return jsonify({"selected_ports": SELECTED_PORTS})

@app.route('/api/paths', methods=['GET'])
def api_paths():
    drone_paths = {}
    pilot_paths = {}
    for det in detection_history:
        mac = det.get("mac")
        if not mac:
            continue
        d_lat = det.get("drone_lat", 0)
        d_long = det.get("drone_long", 0)
        if d_lat != 0 and d_long != 0:
            drone_paths.setdefault(mac, []).append([d_lat, d_long])
        p_lat = det.get("pilot_lat", 0)
        p_long = det.get("pilot_long", 0)
        if p_lat != 0 and p_long != 0:
            pilot_paths.setdefault(mac, []).append([p_lat, p_long])
    def dedupe(path):
        if not path:
            return path
        new_path = [path[0]]
        for point in path[1:]:
            if point != new_path[-1]:
                new_path.append(point)
        return new_path
    for mac in drone_paths: drone_paths[mac] = dedupe(drone_paths[mac])
    for mac in pilot_paths: pilot_paths[mac] = dedupe(pilot_paths[mac])
    return jsonify({"dronePaths": drone_paths, "pilotPaths": pilot_paths})

# ----------------------
# Geofencing API Endpoints
# ----------------------
@app.route('/api/zones', methods=['GET'])
def api_get_zones():
    return jsonify({"zones": ZONES})

@app.route('/api/zones', methods=['POST'])
def api_create_zone():
    global ZONES
    data = request.get_json()
    
    # Generate ID if not provided
    if "id" not in data:
        import uuid
        data["id"] = str(uuid.uuid4())
    
    # Ensure enabled flag
    if "enabled" not in data:
        data["enabled"] = True
    
    ZONES.append(data)
    save_zones()
    return jsonify({"status": "ok", "zone": data})

@app.route('/api/zones/<zone_id>', methods=['PUT'])
def api_update_zone(zone_id):
    global ZONES
    data = request.get_json()
    
    for i, zone in enumerate(ZONES):
        if zone.get("id") == zone_id:
            ZONES[i].update(data)
            ZONES[i]["id"] = zone_id  # Ensure ID doesn't change
            save_zones()
            return jsonify({"status": "ok", "zone": ZONES[i]})
    
    return jsonify({"status": "error", "message": "Zone not found"}), 404

@app.route('/api/zones/<zone_id>', methods=['DELETE'])
def api_delete_zone(zone_id):
    global ZONES
    ZONES = [z for z in ZONES if z.get("id") != zone_id]
    save_zones()
    return jsonify({"status": "ok"})

@app.route('/api/zones/update-openair', methods=['POST'])
def api_update_zones_from_openair():
    """Update zones from UK OpenAir airspace data"""
    global ZONES
    
    data = request.get_json() or {}
    max_altitude_ft = data.get('max_altitude_ft', 400)
    merge_with_existing = data.get('merge', True)
    
    try:
        success = update_zones_from_openair(max_altitude_ft, merge_with_existing)
        if success:
            return jsonify({
                "status": "ok",
                "message": f"Updated zones from OpenAir data",
                "zone_count": len(ZONES)
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to update zones from OpenAir data"
            }), 500
    except Exception as e:
        logger.error(f"Error updating zones from OpenAir: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/zones/update-notam', methods=['POST'])
def api_update_zones_from_notam():
    """Update zones from UK NOTAM data"""
    global ZONES
    
    data = request.get_json() or {}
    max_altitude_ft = data.get('max_altitude_ft', 400)
    merge_with_existing = data.get('merge', True)
    
    try:
        success = update_zones_from_notam(max_altitude_ft, merge_with_existing)
        if success:
            return jsonify({
                "status": "ok",
                "message": f"Updated zones from NOTAM data",
                "zone_count": len(ZONES)
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to update zones from NOTAM data"
            }), 500
    except Exception as e:
        logger.error(f"Error updating zones from NOTAM: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ----------------------
# Incident Log API Endpoints
# ----------------------
@app.route('/api/incidents', methods=['GET'])
def api_get_incidents():
    # Get query parameters
    limit = request.args.get('limit', type=int, default=100)
    incident_type = request.args.get('type', type=str)
    start_date = request.args.get('start_date', type=str)
    end_date = request.args.get('end_date', type=str)
    
    incidents = INCIDENT_LOG.copy()
    
    # Filter by type
    if incident_type:
        incidents = [i for i in incidents if i.get("type") == incident_type]
    
    # Filter by date range
    if start_date:
        incidents = [i for i in incidents if i.get("timestamp", "") >= start_date]
    if end_date:
        incidents = [i for i in incidents if i.get("timestamp", "") <= end_date]
    
    # Sort by timestamp (newest first) and limit
    incidents.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    incidents = incidents[:limit]
    
    return jsonify({
        "incidents": incidents,
        "total": len(INCIDENT_LOG),
        "filtered": len(incidents)
    })

@app.route('/api/incidents/stats', methods=['GET'])
def api_incident_stats():
    """Get statistics about incidents"""
    stats = {
        "total": len(INCIDENT_LOG),
        "by_type": {},
        "recent_24h": 0,
        "zone_entries": 0,
        "zone_exits": 0
    }
    
    now = datetime.now()
    for incident in INCIDENT_LOG:
        # Count by type
        inc_type = incident.get("type", "unknown")
        stats["by_type"][inc_type] = stats["by_type"].get(inc_type, 0) + 1
        
        # Count recent (last 24 hours)
        try:
            inc_time = datetime.fromisoformat(incident.get("timestamp", ""))
            if (now - inc_time).total_seconds() < 86400:
                stats["recent_24h"] += 1
        except:
            pass
        
        # Count zone events
        if inc_type == "zone_entry":
            stats["zone_entries"] += 1
        elif inc_type == "zone_exit":
            stats["zone_exits"] += 1
    
    return jsonify(stats)

# ----------------------
# Serial Reader Threads: Each selected port gets its own thread.
# ----------------------
def serial_reader(port):
    ser = None
    connection_attempts = 0
    max_connection_attempts = 5
    data_received_count = 0
    last_data_time = time.time()
    
    logger.info(f"Starting serial reader thread for port: {port}")
    
    while not SHUTDOWN_EVENT.is_set():
        # Try to open or re-open the serial port
        if ser is None or not getattr(ser, 'is_open', False):
            try:
                ser = serial.Serial(port, BAUD_RATE, timeout=1)
                serial_connected_status[port] = True
                connection_attempts = 0  # Reset counter on successful connection
                logger.info(f"Opened serial port {port} at {BAUD_RATE} baud.")
                with serial_objs_lock:
                    serial_objs[port] = ser
                    
                # Broadcast the updated status immediately
                emit_serial_status()
                    
                # Send a test command to wake up the device (reduce frequency to prevent disconnects)
                try:
                    # Only send watchdog reset once, not continuously
                    if connection_attempts == 0:  # Only on first successful connection
                        time.sleep(0.5)  # Small delay before sending command
                        ser.write(b'WATCHDOG_RESET\n')
                        logger.debug(f"Sent initial watchdog reset to {port}")
                except Exception as e:
                    logger.warning(f"Failed to send watchdog reset to {port}: {e}")
                    
            except Exception as e:
                serial_connected_status[port] = False
                connection_attempts += 1
                logger.error(f"Error opening serial port {port} (attempt {connection_attempts}): {e}")
                
                # Broadcast the updated status immediately
                emit_serial_status()
                
                # If we've failed too many times, wait longer before retrying
                if connection_attempts >= max_connection_attempts:
                    logger.warning(f"Max connection attempts reached for {port}, waiting 30 seconds...")
                    time.sleep(30)
                    connection_attempts = 0  # Reset counter
                else:
                    time.sleep(1)
                continue

        try:
            # Always try to read data, don't rely only on in_waiting
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if line:
                data_received_count += 1
                last_data_time = time.time()
                
                # Log all received data for debugging (limit length to avoid spam)
                if data_received_count <= 10 or data_received_count % 50 == 0:
                    logger.info(f"Data from {port} (#{data_received_count}): {line[:200]}")
                
                # JSON extraction and detection handling...
                json_str = line
                if '{' in line:
                    json_str = line[line.find('{'):]
                    
                try:
                    detection = json.loads(json_str)
                    logger.debug(f"Parsed JSON from {port}: {detection}")
                    
                    # MAC tracking logic...
                    if 'mac' in detection:
                        last_mac_by_port[port] = detection['mac']
                        logger.debug(f"Found MAC in detection: {detection['mac']}")
                    elif port in last_mac_by_port:
                        detection['mac'] = last_mac_by_port[port]
                        logger.debug(f"Using cached MAC for {port}: {detection['mac']}")
                    else:
                        logger.warning(f"No MAC found in detection from {port}: {detection}")
                    
                    # Skip heartbeat messages
                    if 'heartbeat' in detection:
                        logger.debug(f"Skipping heartbeat from {port}")
                        continue
                    
                    # Skip status messages without detection data
                    if not any(key in detection for key in ['mac', 'drone_lat', 'pilot_lat', 'basic_id', 'remote_id']):
                        logger.debug(f"Skipping non-detection message from {port}: {detection}")
                        continue
                        
                    # Normalize remote_id field
                    if 'remote_id' in detection and 'basic_id' not in detection:
                        detection['basic_id'] = detection['remote_id']
                    
                    # Add port information for debugging
                    detection['source_port'] = port
                    
                    # Process the detection
                    logger.info(f"Processing detection from {port}: MAC={detection.get('mac', 'N/A')}, "
                              f"RSSI={detection.get('rssi', 'N/A')}, "
                              f"Drone GPS=({detection.get('drone_lat', 'N/A')}, {detection.get('drone_long', 'N/A')})")
                    
                    update_detection(detection)
                    
                    # Log detection in headless mode
                    if HEADLESS_MODE and detection.get('mac'):
                        logger.info(f"Detection from {port}: MAC {detection['mac']}, "
                                   f"RSSI {detection.get('rssi', 'N/A')}")
                        
                except json.JSONDecodeError as e:
                    # Log non-JSON data for debugging
                    logger.debug(f"Non-JSON data from {port}: {line[:100]}")
                    continue
            else:
                # Short sleep when no data
                time.sleep(0.1)
                
                # Log if we haven't received data in a while
                if time.time() - last_data_time > 30:  # 30 seconds
                    # logger.warning(f"No data received from {port} for {int(time.time() - last_data_time)} seconds")
                    last_data_time = time.time()  # Reset timer to avoid spam
                
        except (serial.SerialException, OSError) as e:
            serial_connected_status[port] = False
            logger.error(f"SerialException/OSError on {port}: {e}")
            
            # Broadcast the updated status immediately
            emit_serial_status()
            
            try:
                if ser and ser.is_open:
                    ser.close()
            except Exception:
                pass
            ser = None
            with serial_objs_lock:
                serial_objs.pop(port, None)
            time.sleep(1)
            
        except Exception as e:
            serial_connected_status[port] = False
            logger.error(f"Unexpected error on {port}: {e}")
            
            # Broadcast the updated status immediately
            emit_serial_status()
            
            try:
                if ser and ser.is_open:
                    ser.close()
            except Exception:
                pass
            ser = None
            with serial_objs_lock:
                serial_objs.pop(port, None)
            time.sleep(1)
    
    logger.info(f"Serial reader thread for {port} shutting down. Total data packets received: {data_received_count}")

def start_serial_thread(port):
    thread = threading.Thread(target=serial_reader, args=(port,), daemon=True)
    thread.start()

# Download endpoints for CSV, KML, and Aliases files
@app.route('/download/csv')
def download_csv():
    return send_file(CSV_FILENAME, as_attachment=True)

@app.route('/download/kml')
def download_kml():
    # regenerate KML to include latest detections
    generate_kml()
    return send_file(KML_FILENAME, as_attachment=True)

@app.route('/download/aliases')
def download_aliases():
    # ensure latest aliases are saved to disk
    save_aliases()
    return send_file(ALIASES_FILE, as_attachment=True)


# --- Cumulative download endpoints ---
@app.route('/download/cumulative_detections.csv')
def download_cumulative_csv():
    return send_file(
        CUMULATIVE_CSV_FILENAME,
        mimetype='text/csv',
        as_attachment=True,
        download_name='cumulative_detections.csv'
    )

@app.route('/download/cumulative.kml')
def download_cumulative_kml():
    # regenerate cumulative KML to include latest detections
    generate_cumulative_kml()
    return send_file(
        CUMULATIVE_KML_FILENAME,
        mimetype='application/vnd.google-earth.kml+xml',
        as_attachment=True,
        download_name='cumulative.kml'
    )

# ----------------------
# Startup Auto-Connection
# ----------------------
def startup_auto_connect():
    """
    Load saved ports and attempt auto-connection on startup.
    Enhanced version with better logging and headless support.
    """
    logger.info("=== DRONE MAPPER STARTUP ===")
    
    # Initialize database
    logger.info("Initializing database...")
    init_database()
    
    logger.info("Loading previously saved ports...")
    load_selected_ports()
    
    # Load webhook URL
    logger.info("Loading previously saved webhook URL...")
    # load_webhook_url()  # Temporarily disabled - will be called later
    
    # Load lightning detection settings
    logger.info("Loading lightning detection settings...")
    load_lightning_settings()
    
    # Load AIS settings on startup
    load_ais_settings()
    
    # Load AIS configuration (API keys)
    load_ais_config()
    
    # Load APRS settings on startup
    load_aprs_settings()
    
    # Load APRS configuration (API keys and callsigns)
    load_aprs_config()
    
    # Load weather settings on startup
    load_weather_settings()
    
    # Load weather configuration (API keys and locations)
    load_weather_config()
    
    if SELECTED_PORTS:
        logger.info(f"Found saved ports: {list(SELECTED_PORTS.values())}")
        auto_connected = auto_connect_to_saved_ports()
        if auto_connected:
            logger.info("Auto-connection successful! Mapping is now active.")
            if HEADLESS_MODE:
                logger.info("Running in headless mode - mapping will continue automatically")
        else:
            logger.warning("Auto-connection failed. Port selection will be required.")
            if HEADLESS_MODE:
                logger.info("Headless mode: Will monitor for port availability...")
    else:
        logger.info("No previously saved ports found.")
        if HEADLESS_MODE:
            logger.info("Headless mode: Will monitor for any available ports...")
    
    # Start monitoring and status logging
    start_port_monitoring()
    start_status_logging()
    start_websocket_broadcaster()
    
    logger.info("=== STARTUP COMPLETE ===")

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Drone Detection Mapper - Automatically detect and map drone activity',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mapper.py                    # Start with web interface
  python mapper.py --headless         # Run in headless mode (no web interface)
  python mapper.py --no-auto-start    # Disable automatic port connection
  python mapper.py --port-interval 5  # Check for ports every 5 seconds
  python mapper.py --debug            # Enable debug logging
        """
    )
    
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Run in headless mode without web interface'
    )
    
    parser.add_argument(
        '--no-auto-start',
        action='store_true',
        help='Disable automatic port connection and monitoring'
    )
    
    parser.add_argument(
        '--port-interval',
        type=int,
        default=10,
        help='Port monitoring interval in seconds (default: 10)'
    )
    
    parser.add_argument(
        '--web-port',
        type=int,
        default=5000,
        help='Web interface port (default: 5000)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    return parser.parse_args()

def main():
    """Main function with enhanced startup and configuration"""
    global HEADLESS_MODE, AUTO_START_ENABLED, PORT_MONITOR_INTERVAL
    
    # Parse command line arguments
    args = parse_arguments()
    
    # Free the port before starting (clean start)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', args.web_port))
        sock.close()
        logger.info(f"Port {args.web_port} is available")
    except OSError as e:
        if e.errno == 98:  # Address already in use
            logger.warning(f"Port {args.web_port} is in use, attempting to free it...")
            try:
                # Try to find and kill process using the port
                result = subprocess.run(
                    ['fuser', '-k', f'{args.web_port}/tcp'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                time.sleep(1)  # Give it a moment to free
                logger.info(f"Attempted to free port {args.web_port}")
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e2:
                logger.warning(f"Could not automatically free port {args.web_port}: {e2}")
                logger.warning("You may need to manually stop the process using this port")
        else:
            logger.error(f"Error checking port {args.web_port}: {e}")
    
    # Configure global settings
    HEADLESS_MODE = args.headless
    AUTO_START_ENABLED = not args.no_auto_start
    PORT_MONITOR_INTERVAL = args.port_interval
    
    # Configure logging level
    if args.debug:
        set_debug_mode(True)
    
    # Load webhook URL (now that all functions are defined)
    load_webhook_url()
    
    # Clean session state to prevent lingering from prior sessions
    global backend_seen_drones, backend_previous_active, backend_alerted_no_gps
    global tracked_pairs, detection_history
    backend_seen_drones.clear()
    backend_previous_active.clear()
    backend_alerted_no_gps.clear()
    tracked_pairs.clear()
    detection_history.clear()
    logger.info("Session state cleared - fresh session initialized")
    
    logger.info(f"Starting Drone Mapper...")
    logger.info(f"Headless mode: {HEADLESS_MODE}")
    logger.info(f"Auto-start enabled: {AUTO_START_ENABLED}")
    logger.info(f"Port monitoring interval: {PORT_MONITOR_INTERVAL}s")
    
    # Perform startup auto-connection
    startup_auto_connect()
    
    # Start cleanup timer to prevent memory leaks
    start_cleanup_timer()
    
    # Start OpenAir airspace data updater
    start_openair_updater()
    
    # Start NOTAM data updater
    start_notam_updater()
    
    # Start real-time lightning detection
    start_lightning_detection()
    
    # Start maritime AIS data updater (REST API)
    start_ais_updater()
    
    # Start maritime AIS WebSocket feed (real-time)
    start_ais_websocket()
    
    # Start port data updater (Marinesia API)
    start_ports_updater()
    
    # Start Met Office weather warnings updater
    start_metoffice_updater()
    
    # Load APRS settings on startup
    load_aprs_settings()
    
    # Load APRS configuration (API keys and callsigns)
    load_aprs_config()
    
    # Start APRS data updater
    start_aprs_updater()
    
    # Start ADSB data updater
    start_adsb_updater()
    
    # Load weather settings on startup
    load_weather_settings()
    
    # Load weather configuration (API keys and locations)
    load_weather_config()
    
    # Start weather data updater
    start_weather_updater()
    
    # Load webcams settings on startup
    load_webcams_settings()
    
    # Load webcams configuration (API keys)
    load_webcams_config()
    
    # Start webcams data updater
    start_webcams_updater()
    
    # Initial download if file doesn't exist
    if not os.path.exists(OPENAIR_FILE):
        logger.info("OpenAir file not found, downloading on startup...")
        try:
            if download_openair_file():
                # Parse and add zones
                airspaces = parse_openair_file()
                if airspaces:
                    openair_zones = convert_airspaces_to_zones(airspaces, max_altitude_ft=400)
                    if openair_zones:
                        # Merge with existing zones
                        ZONES = [z for z in ZONES if z.get('source') != 'openair']
                        ZONES.extend(openair_zones)
                        save_zones()
                        logger.info(f"Added {len(openair_zones)} OpenAir zones on startup")
        except Exception as e:
            logger.warning(f"Failed to download OpenAir data on startup: {e}")
    
    # Initial NOTAM download if file doesn't exist
    if not os.path.exists(NOTAM_FILE):
        logger.info("NOTAM file not found, downloading on startup...")
        try:
            if download_notam_file():
                # Parse and add zones
                notams = parse_notam_file()
                if notams:
                    notam_zones = convert_notams_to_zones(notams, max_altitude_ft=400)
                    if notam_zones:
                        # Merge with existing zones
                        ZONES = [z for z in ZONES if z.get('source') != 'notam']
                        ZONES.extend(notam_zones)
                        save_zones()
                        logger.info(f"Added {len(notam_zones)} NOTAM zones on startup")
        except Exception as e:
            logger.warning(f"Failed to download NOTAM data on startup: {e}")
    
    if HEADLESS_MODE:
        logger.info("Running in headless mode - press Ctrl+C to stop")
        try:
            # In headless mode, just wait for shutdown signal
            while not SHUTDOWN_EVENT.is_set():
                SHUTDOWN_EVENT.wait(60)  # Check every minute
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            signal_handler(signal.SIGTERM, None)
    else:
        logger.info(f"Starting web interface on port {args.web_port}")
        logger.info(f"Access the interface at: http://localhost:{args.web_port}")
        try:
            # Use SocketIO to run the app
            socketio.run(app, host='0.0.0.0', port=args.web_port, debug=False)
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            signal_handler(signal.SIGTERM, None)


@app.route('/api/diagnostics', methods=['GET'])
def api_diagnostics():
    """Provide detailed diagnostic information for troubleshooting"""
    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "selected_ports": SELECTED_PORTS,
        "serial_status": serial_connected_status,
        "tracked_pairs": len(tracked_pairs),
        "detection_history_count": len(detection_history),
        "last_mac_by_port": last_mac_by_port,
        "available_ports": [{"device": p.device, "description": p.description} 
                           for p in serial.tools.list_ports.comports()],
        "active_serial_objects": list(serial_objs.keys()) if serial_objs else [],
        "headless_mode": HEADLESS_MODE,
        "auto_start_enabled": AUTO_START_ENABLED,
        "shutdown_event_set": SHUTDOWN_EVENT.is_set(),
        "debug_mode": DEBUG_MODE
    }
    
    # Add recent detections if any exist
    if detection_history:
        recent_detections = detection_history[-5:]  # Last 5 detections
        diagnostics["recent_detections"] = [
            {
                "mac": d.get("mac", "N/A"),
                "timestamp": d.get("last_update", "N/A"),
                "source_port": d.get("source_port", "N/A"),
                "drone_coords": f"({d.get('drone_lat', 'N/A')}, {d.get('drone_long', 'N/A')})",
                "rssi": d.get("rssi", "N/A")
            }
            for d in recent_detections
        ]
    else:
        diagnostics["recent_detections"] = []
    
    return jsonify(diagnostics)

@app.route('/api/debug_mode', methods=['POST'])
def api_toggle_debug():
    """Toggle debug mode on/off"""
    data = request.get_json() or {}
    enabled = data.get('enabled', not DEBUG_MODE)
    set_debug_mode(enabled)
    return jsonify({"debug_mode": DEBUG_MODE, "message": f"Debug mode {'enabled' if DEBUG_MODE else 'disabled'}"})

@app.route('/api/send_command', methods=['POST'])
def api_send_command():
    """Send a test command to serial ports for debugging"""
    data = request.get_json()
    command = data.get('command', 'WATCHDOG_RESET')
    port = data.get('port')  # Optional: send to specific port
    
    results = {}
    
    with serial_objs_lock:
        ports_to_send = [port] if port and port in serial_objs else list(serial_objs.keys())
        
        for p in ports_to_send:
            try:
                ser = serial_objs.get(p)
                if ser and ser.is_open:
                    ser.write(f'{command}\n'.encode())
                    results[p] = "Command sent successfully"
                    logger.info(f"Sent command '{command}' to {p}")
                else:
                    results[p] = "Port not open or not available"
            except Exception as e:
                results[p] = f"Error: {str(e)}"
                logger.error(f"Failed to send command to {p}: {e}")
    
    return jsonify({"command": command, "results": results})

# --- SocketIO connection event ---
@socketio.on('connect')
def handle_connect():
    logger.debug("Client connected via WebSocket")
    # Send current state to newly connected client
    emit_detections()
    emit_aliases()
    emit_serial_status()
    emit_paths()
    emit_cumulative_log()
    emit_faa_cache()
    emit_weather_data()
    emit_webcams_data()
    emit_ais_vessels()
    emit_aprs_stations()
    emit_adsb_aircraft()
    emit_zones()

# Helper functions to emit all real-time data

def emit_serial_status():
    try:
        socketio.emit('serial_status', serial_connected_status, )
    except Exception as e:
        logger.debug(f"Error emitting serial status: {e}")
        pass  # Ignore if no clients connected or serialization error

def emit_aliases():
    try:
        socketio.emit('aliases', ALIASES, )
    except Exception as e:
        logger.debug(f"Error emitting aliases: {e}")

def emit_detections():
    try:
        # Convert tracked_pairs to a JSON-serializable format
        serializable_pairs = {}
        for key, value in tracked_pairs.items():
            # Ensure key is a string
            str_key = str(key)
            # Ensure value is JSON-serializable
            if isinstance(value, dict):
                serializable_pairs[str_key] = value
            else:
                serializable_pairs[str_key] = str(value)
        socketio.emit('detections', serializable_pairs, )
    except Exception as e:
        logger.debug(f"Error emitting detections: {e}")

def emit_paths():
    try:
        socketio.emit('paths', get_paths_for_emit(), )
    except Exception as e:
        logger.debug(f"Error emitting paths: {e}")

def emit_cumulative_log():
    try:
        socketio.emit('cumulative_log', get_cumulative_log_for_emit(), )
    except Exception as e:
        logger.debug(f"Error emitting cumulative log: {e}")

def emit_faa_cache():
    try:
        # Convert FAA_CACHE to JSON-serializable format
        serializable_cache = {}
        for key, value in FAA_CACHE.items():
            # Convert tuple keys to strings
            str_key = str(key) if isinstance(key, tuple) else key
            serializable_cache[str_key] = value
        socketio.emit('faa_cache', serializable_cache, )
    except Exception as e:
        logger.debug(f"Error emitting FAA cache: {e}")

def emit_ais_vessels():
    try:
        with AIS_VESSELS_LOCK:
            vessels = list(AIS_VESSELS.values())
        socketio.emit('ais_vessels', {'vessels': vessels})
    except Exception as e:
        logger.debug(f"Error emitting AIS vessels: {e}")

def emit_aprs_stations():
    try:
        with APRS_STATIONS_LOCK:
            stations = list(APRS_STATIONS.values())
        socketio.emit('aprs_stations', {'stations': stations})
    except Exception as e:
        logger.debug(f"Error emitting APRS stations: {e}")

def emit_adsb_aircraft():
    try:
        with ADSB_AIRCRAFT_LOCK:
            aircraft = list(ADSB_AIRCRAFT.values())
        socketio.emit('adsb_aircraft', {'aircraft': aircraft})
    except Exception as e:
        logger.debug(f"Error emitting ADSB aircraft: {e}")

def emit_zones():
    try:
        socketio.emit('zones_updated', {"zones": ZONES, "count": len(ZONES)})
    except Exception as e:
        logger.debug(f"Error emitting zones: {e}")

def emit_weather_data():
    try:
        socketio.emit('weather_data', {'weather': WEATHER_DATA})
    except Exception as e:
        logger.debug(f"Error emitting weather data: {e}")

def emit_webcams_data():
    try:
        socketio.emit('webcams_data', {'webcams': WEBCAMS_DATA})
    except Exception as e:
        logger.debug(f"Error emitting webcams data: {e}")

# Helper to get paths for emit

def get_paths_for_emit():
    drone_paths = {}
    pilot_paths = {}
    for det in detection_history:
        mac = det.get("mac")
        if not mac:
            continue
        d_lat = det.get("drone_lat", 0)
        d_long = det.get("drone_long", 0)
        if d_lat != 0 and d_long != 0:
            drone_paths.setdefault(mac, []).append([d_lat, d_long])
        p_lat = det.get("pilot_lat", 0)
        p_long = det.get("pilot_long", 0)
        if p_lat != 0 and p_long != 0:
            pilot_paths.setdefault(mac, []).append([p_lat, p_long])
    def dedupe(path):
        if not path:
            return path
        new_path = [path[0]]
        for point in path[1:]:
            if point != new_path[-1]:
                new_path.append(point)
        return new_path
    for mac in drone_paths: drone_paths[mac] = dedupe(drone_paths[mac])
    for mac in pilot_paths: pilot_paths[mac] = dedupe(pilot_paths[mac])
    return {"dronePaths": drone_paths, "pilotPaths": pilot_paths}

# Helper to get cumulative log for emit

def get_cumulative_log_for_emit():
    # Read the cumulative CSV and return as a list of dicts
    try:
        if os.path.exists(CUMULATIVE_CSV_FILENAME):
            with open(CUMULATIVE_CSV_FILENAME, 'r', newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                return list(reader)
        else:
            return []
    except Exception as e:
        logger.error(f"Error reading cumulative log: {e}")
        return []


@app.route('/api/set_webhook_url', methods=['POST'])
def api_set_webhook_url():
    try:
        # Check if request has JSON data
        if not request.is_json:
            return jsonify({"status": "error", "message": "Request must be JSON"}), 400
        
        data = request.get_json()
        
        # Handle case where data is None
        if data is None:
            return jsonify({"status": "error", "message": "Invalid JSON data"}), 400
        
        # Get webhook URL and handle None case
        url = data.get('webhook_url', '')
        if url is None:
            url = ''
        else:
            url = str(url).strip()
        
        # Validate URL format if not empty
        if url and not url.startswith(('http://', 'https://')):
            return jsonify({"status": "error", "message": "Invalid webhook URL - must start with http:// or https://"}), 400
        
        # Additional URL validation for common issues
        if url:
            # Check for localhost variations that might not work
            if 'localhost' in url and not url.startswith('http://localhost'):
                return jsonify({"status": "error", "message": "For localhost URLs, please use http://localhost"}), 400
        
        # Set the webhook URL
        set_server_webhook_url(url)
        
        # Log the update
        if url:
            logger.info(f"Webhook URL updated to: {url}")
        else:
            logger.info("Webhook URL cleared")
        
        return jsonify({"status": "ok", "webhook_url": WEBHOOK_URL})
        
    except Exception as e:
        logger.error(f"Error setting webhook URL: {e}")
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500

@app.route('/api/get_webhook_url', methods=['GET'])
def api_get_webhook_url():
    """Get the current webhook URL"""
    try:
        return jsonify({"status": "ok", "webhook_url": WEBHOOK_URL or ""})
    except Exception as e:
        logger.error(f"Error getting webhook URL: {e}")
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500

@app.route('/api/webhook_url', methods=['GET'])
def api_webhook_url():
    return jsonify({"webhook_url": WEBHOOK_URL or ""})

@app.route('/api/lightning_detection', methods=['GET'])
def get_lightning_detection():
    """Get current lightning detection enabled state"""
    return jsonify({"enabled": LIGHTNING_DETECTION_ENABLED})

@app.route('/api/lightning_detection', methods=['POST'])
def set_lightning_detection():
    """Toggle lightning detection on/off"""
    global LIGHTNING_DETECTION_ENABLED, LIGHTNING_WS_CONNECTION
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    LIGHTNING_DETECTION_ENABLED = enabled
    save_lightning_settings()
    
    if not enabled and LIGHTNING_WS_CONNECTION:
        # Close WebSocket connection if disabling
        try:
            LIGHTNING_WS_CONNECTION.close()
            LIGHTNING_WS_CONNECTION = None
            logger.info("Lightning detection disabled - WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error closing lightning WebSocket: {e}")
    elif enabled:
        # Start lightning detection if enabling and not already running
        logger.info("Lightning detection enabled")
        # The thread will automatically start when it checks the enabled flag
    
    return jsonify({"status": "ok", "enabled": LIGHTNING_DETECTION_ENABLED})

# ----------------------
# Maritime AIS API Endpoints
# ----------------------
@app.route('/api/ais_vessels', methods=['GET'])
def api_ais_vessels():
    """Get current AIS vessel data"""
    with AIS_VESSELS_LOCK:
        vessels = list(AIS_VESSELS.values())
    return jsonify({
        "status": "ok",
        "vessels": vessels,
        "count": len(vessels)
    })

@app.route('/api/maritime_ports', methods=['GET'])
def api_maritime_ports():
    """Get current maritime port data"""
    global PORTS
    return jsonify({
        "status": "ok",
        "ports": list(PORTS.values()),
        "count": len(PORTS)
    })

@app.route('/api/ais_detection', methods=['GET'])
def get_ais_detection():
    """Get current AIS detection enabled state"""
    return jsonify({"enabled": AIS_DETECTION_ENABLED})

@app.route('/api/ais_detection', methods=['POST'])
def set_ais_detection():
    """Toggle AIS detection on/off"""
    global AIS_DETECTION_ENABLED, AIS_WS_CONNECTION
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    AIS_DETECTION_ENABLED = enabled
    save_ais_settings()
    
    if enabled:
        # Trigger immediate update when enabling
        update_ais_data()
        logger.info("AIS detection enabled")
    else:
        # Clear vessels when disabling
        with AIS_VESSELS_LOCK:
            AIS_VESSELS.clear()
        try:
            socketio.emit('ais_vessels', {'vessels': []})
        except Exception as e:
            logger.debug(f"Error clearing AIS vessels: {e}")
        
        # Close WebSocket connection if disabling
        if AIS_WS_CONNECTION:
            try:
                AIS_WS_CONNECTION.close()
                AIS_WS_CONNECTION = None
                logger.info("AIS WebSocket connection closed")
            except Exception as e:
                logger.error(f"Error closing AIS WebSocket: {e}")
        
        logger.info("AIS detection disabled")
    
    return jsonify({"status": "ok", "enabled": AIS_DETECTION_ENABLED})

@app.route('/api/ais_update', methods=['POST'])
def api_ais_update():
    """Manually trigger AIS data update"""
    try:
        update_ais_data()
        with AIS_VESSELS_LOCK:
            count = len(AIS_VESSELS)
        return jsonify({"status": "ok", "vessel_count": count})
    except Exception as e:
        logger.error(f"Error in manual AIS update: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------
# Met Office Weather Warnings API Endpoints
# ----------------------
@app.route('/api/metoffice_warnings', methods=['GET'])
def api_metoffice_warnings():
    """Get current Met Office weather warnings"""
    global METOFFICE_WARNINGS
    return jsonify({
        "status": "ok",
        "warnings": list(METOFFICE_WARNINGS.values()),
        "count": len(METOFFICE_WARNINGS)
    })

@app.route('/api/metoffice_warnings_detection', methods=['GET'])
def get_metoffice_warnings_detection():
    """Get current Met Office warnings enabled state"""
    return jsonify({"enabled": METOFFICE_WARNINGS_ENABLED})

@app.route('/api/metoffice_warnings_detection', methods=['POST'])
def set_metoffice_warnings_detection():
    """Toggle Met Office warnings on/off"""
    global METOFFICE_WARNINGS_ENABLED
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    METOFFICE_WARNINGS_ENABLED = enabled
    
    if enabled:
        # Trigger immediate update when enabling
        update_metoffice_warnings()
        logger.info("Met Office warnings enabled")
    else:
        logger.info("Met Office warnings disabled")
    
    return jsonify({"status": "ok", "enabled": METOFFICE_WARNINGS_ENABLED})

@app.route('/api/metoffice_warnings_update', methods=['POST'])
def api_metoffice_warnings_update():
    """Manually trigger Met Office warnings update"""
    try:
        update_metoffice_warnings()
        return jsonify({"status": "ok", "warning_count": len(METOFFICE_WARNINGS)})
    except Exception as e:
        logger.error(f"Error in manual Met Office warnings update: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/metoffice_settings', methods=['GET'])
def get_metoffice_settings():
    """Get Met Office alert settings"""
    global METOFFICE_ALERT_SETTINGS
    return jsonify({
        "status": "ok",
        "settings": METOFFICE_ALERT_SETTINGS
    })

@app.route('/api/metoffice_settings', methods=['POST'])
def set_metoffice_settings():
    """Update Met Office alert settings"""
    global METOFFICE_ALERT_SETTINGS, METOFFICE_UPDATE_INTERVAL
    try:
        data = request.get_json()
        
        # Update settings
        if "eas_tones_enabled" in data:
            METOFFICE_ALERT_SETTINGS["eas_tones_enabled"] = bool(data["eas_tones_enabled"])
        if "amber_alerts_enabled" in data:
            METOFFICE_ALERT_SETTINGS["amber_alerts_enabled"] = bool(data["amber_alerts_enabled"])
        if "yellow_alerts_enabled" in data:
            METOFFICE_ALERT_SETTINGS["yellow_alerts_enabled"] = bool(data["yellow_alerts_enabled"])
        if "repeat_alerts_enabled" in data:
            METOFFICE_ALERT_SETTINGS["repeat_alerts_enabled"] = bool(data["repeat_alerts_enabled"])
        if "eas_volume" in data:
            volume = int(data["eas_volume"])
            METOFFICE_ALERT_SETTINGS["eas_volume"] = max(0, min(100, volume))
        if "update_frequency" in data:
            freq = int(data["update_frequency"])
            METOFFICE_UPDATE_INTERVAL = max(300, min(21600, freq))  # 5 min to 6 hours
            METOFFICE_ALERT_SETTINGS["update_frequency"] = METOFFICE_UPDATE_INTERVAL
        
        save_metoffice_settings()
        logger.info("Met Office alert settings updated")
        return jsonify({"status": "ok", "settings": METOFFICE_ALERT_SETTINGS})
    except Exception as e:
        logger.error(f"Error setting Met Office settings: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/ais_config', methods=['GET'])
def get_ais_config():
    """Get AIS configuration (API keys - masked for security)"""
    try:
        config = {}
        if os.path.exists(AIS_CONFIG_FILE):
            with open(AIS_CONFIG_FILE, "r") as f:
                config = json.load(f)
                # Mask API key for security
                if 'aisstream_api_key' in config:
                    key = config['aisstream_api_key']
                    if key:
                        config['aisstream_api_key'] = key[:8] + '...' + key[-4:] if len(key) > 12 else '***'
        
        # Check environment variables
        env_key = os.environ.get('AISSTREAM_API_KEY') or os.environ.get('AIS_API_KEY')
        return jsonify({
            "status": "ok",
            "config": config,
            "has_env_key": bool(env_key),
            "configured": bool(env_key or config.get('aisstream_api_key'))
        })
    except Exception as e:
        logger.error(f"Error getting AIS config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/ais_config', methods=['POST'])
def set_ais_config():
    """Update AIS configuration (API keys)"""
    global AIS_API_KEY
    try:
        data = request.get_json()
        api_key = data.get('aisstream_api_key', '').strip()
        
        if not api_key:
            return jsonify({"status": "error", "message": "API key is required"}), 400
        
        # Save to config file
        config = {'aisstream_api_key': api_key}
        with open(AIS_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        
        # Update global variable
        AIS_API_KEY = api_key
        
        # Reload AIS WebSocket connection if enabled
        if AIS_DETECTION_ENABLED:
            logger.info("AIS API key updated, restarting WebSocket connection...")
            # Close existing connection
            if AIS_WS_CONNECTION:
                try:
                    AIS_WS_CONNECTION.close()
                except:
                    pass
            # Start new connection
            start_ais_websocket()
        
        logger.info("AIS configuration updated successfully")
        return jsonify({"status": "ok", "message": "AIS configuration updated"})
    except Exception as e:
        logger.error(f"Error updating AIS config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------
# APRS API Endpoints
# ----------------------
@app.route('/api/aprs_stations', methods=['GET'])
def api_aprs_stations():
    """Get current APRS station data"""
    with APRS_STATIONS_LOCK:
        stations = list(APRS_STATIONS.values())
    return jsonify({
        "status": "ok",
        "stations": stations,
        "count": len(stations)
    })

@app.route('/api/aprs_detection', methods=['GET'])
def get_aprs_detection():
    """Get current APRS detection enabled state"""
    return jsonify({"enabled": APRS_DETECTION_ENABLED})

@app.route('/api/aprs_detection', methods=['POST'])
def set_aprs_detection():
    """Toggle APRS detection on/off"""
    global APRS_DETECTION_ENABLED
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    APRS_DETECTION_ENABLED = enabled
    save_aprs_settings()
    
    if enabled:
        # Trigger immediate update when enabling
        update_aprs_data()
        logger.info("APRS detection enabled")
    else:
        # Clear stations when disabling
        with APRS_STATIONS_LOCK:
            APRS_STATIONS.clear()
        try:
            socketio.emit('aprs_stations', {'stations': []})
        except Exception as e:
            logger.debug(f"Error clearing APRS stations: {e}")
        
        logger.info("APRS detection disabled")
    
    return jsonify({"status": "ok", "enabled": APRS_DETECTION_ENABLED})

@app.route('/api/aprs_update', methods=['POST'])
def api_aprs_update():
    """Manually trigger APRS data update"""
    try:
        update_aprs_data()
        return jsonify({"status": "ok", "station_count": len(APRS_STATIONS)})
    except Exception as e:
        logger.error(f"Error in manual APRS update: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/aprs_config', methods=['GET'])
def get_aprs_config():
    """Get APRS configuration (API keys and callsigns - masked for security)"""
    try:
        config = {}
        if os.path.exists(APRS_CONFIG_FILE):
            with open(APRS_CONFIG_FILE, "r") as f:
                config = json.load(f)
                # Mask API key for security
                if 'aprs_api_key' in config:
                    key = config['aprs_api_key']
                    if key:
                        config['aprs_api_key'] = key[:8] + '...' + key[-4:] if len(key) > 12 else '***'
        
        # Check environment variables
        env_key = os.environ.get('APRS_API_KEY')
        return jsonify({
            "status": "ok",
            "config": config,
            "has_env_key": bool(env_key),
            "configured": bool(env_key or config.get('aprs_api_key'))
        })
    except Exception as e:
        logger.error(f"Error getting APRS config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/aprs_config', methods=['POST'])
def set_aprs_config():
    """Update APRS configuration (API keys and callsigns)"""
    global APRS_API_KEY
    try:
        data = request.get_json()
        api_key = data.get('aprs_api_key', '').strip()
        callsigns = data.get('callsigns', [])
        
        if not api_key:
            return jsonify({"status": "error", "message": "API key is required"}), 400
        
        # Validate callsigns if provided
        if callsigns:
            # Basic validation: callsigns should be strings
            callsigns = [str(c).strip().upper() for c in callsigns if c and str(c).strip()]
        
        # Update global variable (only if not from environment)
        if not os.environ.get('APRS_API_KEY'):
            APRS_API_KEY = api_key
        
        # Save to config file
        config = {
            "aprs_api_key": api_key if not os.environ.get('APRS_API_KEY') else "",
            "callsigns": callsigns
        }
        
        with open(APRS_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        
        save_aprs_config()
        
        logger.info("APRS configuration updated")
        return jsonify({"status": "ok", "message": "APRS configuration updated successfully"})
    except Exception as e:
        logger.error(f"Error setting APRS config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------
# ADSB API Endpoints
# ----------------------
@app.route('/api/adsb_aircraft', methods=['GET'])
def api_adsb_aircraft():
    """Get current ADSB aircraft data"""
    with ADSB_AIRCRAFT_LOCK:
        aircraft = list(ADSB_AIRCRAFT.values())
    return jsonify({
        "status": "ok",
        "aircraft": aircraft,
        "count": len(aircraft)
    })

@app.route('/api/adsb_detection', methods=['GET'])
def get_adsb_detection():
    """Get current ADSB detection enabled state"""
    return jsonify({"enabled": ADSB_DETECTION_ENABLED})

@app.route('/api/adsb_detection', methods=['POST'])
def set_adsb_detection():
    """Toggle ADSB detection on/off"""
    global ADSB_DETECTION_ENABLED
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    ADSB_DETECTION_ENABLED = enabled
    
    if enabled:
        # Trigger immediate update
        update_adsb_data()
    
    return jsonify({"status": "ok", "enabled": ADSB_DETECTION_ENABLED})

@app.route('/api/adsb_update', methods=['POST'])
def api_adsb_update():
    """Manually trigger ADSB data update"""
    try:
        update_adsb_data()
        return jsonify({"status": "ok", "aircraft_count": len(ADSB_AIRCRAFT)})
    except Exception as e:
        logger.error(f"Error in manual ADSB update: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/adsb_settings', methods=['GET'])
def get_adsb_settings():
    """Get ADSB settings (center location and radius)"""
    global ADSB_CENTER_LAT, ADSB_CENTER_LON, ADSB_RADIUS_KM
    return jsonify({
        "status": "ok",
        "center_lat": ADSB_CENTER_LAT,
        "center_lon": ADSB_CENTER_LON,
        "radius_km": ADSB_RADIUS_KM,
        "enabled": ADSB_DETECTION_ENABLED
    })

@app.route('/api/adsb_settings', methods=['POST'])
def set_adsb_settings():
    """Update ADSB settings (center location and radius)"""
    global ADSB_CENTER_LAT, ADSB_CENTER_LON, ADSB_RADIUS_KM
    try:
        data = request.get_json()
        
        if 'center_lat' in data:
            ADSB_CENTER_LAT = float(data['center_lat'])
        if 'center_lon' in data:
            ADSB_CENTER_LON = float(data['center_lon'])
        if 'radius_km' in data:
            ADSB_RADIUS_KM = max(10, min(1000, float(data['radius_km'])))  # Clamp between 10-1000km
        
        # Trigger immediate update with new settings
        if ADSB_DETECTION_ENABLED:
            update_adsb_data()
        
        return jsonify({
            "status": "ok",
            "center_lat": ADSB_CENTER_LAT,
            "center_lon": ADSB_CENTER_LON,
            "radius_km": ADSB_RADIUS_KM
        })
    except Exception as e:
        logger.error(f"Error setting ADSB settings: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------
# Weather API Endpoints
# ----------------------
@app.route('/api/weather', methods=['GET'])
def api_weather():
    """Get current weather data for all configured locations"""
    global WEATHER_DATA
    return jsonify({
        "status": "ok",
        "weather": WEATHER_DATA,
        "count": len(WEATHER_DATA)
    })

@app.route('/api/weather/<float:lat>/<float:lon>', methods=['GET'])
def api_weather_location(lat, lon):
    """Get weather data for a specific location"""
    try:
        model = request.args.get('model', 'gfs')
        weather = fetch_weather_data(lat, lon, model=model)
        if weather:
            return jsonify({"status": "ok", "weather": weather})
        else:
            return jsonify({"status": "error", "message": "Failed to fetch weather data"}), 500
    except Exception as e:
        logger.error(f"Error fetching weather for {lat},{lon}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/weather_detection', methods=['GET'])
def get_weather_detection():
    """Get current weather detection enabled state"""
    return jsonify({"enabled": WEATHER_ENABLED})

@app.route('/api/weather_detection', methods=['POST'])
def set_weather_detection():
    """Toggle weather detection on/off"""
    global WEATHER_ENABLED
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    WEATHER_ENABLED = enabled
    save_weather_settings()
    
    if enabled:
        # Trigger immediate update when enabling
        update_weather_data()
        logger.info("Weather detection enabled")
    else:
        # Clear weather data when disabling
        WEATHER_DATA.clear()
        try:
            socketio.emit('weather_data', {'weather': {}})
        except Exception as e:
            logger.debug(f"Error clearing weather data: {e}")
        
        logger.info("Weather detection disabled")
    
    return jsonify({"status": "ok", "enabled": WEATHER_ENABLED})

@app.route('/api/weather_update', methods=['POST'])
def api_weather_update():
    """Manually trigger weather data update"""
    try:
        update_weather_data()
        return jsonify({"status": "ok", "location_count": len(WEATHER_DATA)})
    except Exception as e:
        logger.error(f"Error in manual weather update: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/weather_config', methods=['GET'])
def get_weather_config():
    """Get weather configuration (API keys - masked for security)"""
    try:
        config = {}
        if os.path.exists(WEATHER_CONFIG_FILE):
            with open(WEATHER_CONFIG_FILE, "r") as f:
                config = json.load(f)
                # Mask API key for security
                if 'windy_api_key' in config:
                    key = config['windy_api_key']
                    if key:
                        config['windy_api_key'] = key[:8] + '...' + key[-4:] if len(key) > 12 else '***'
        
        # Check environment variables
        env_key = os.environ.get('WINDY_API_KEY')
        return jsonify({
            "status": "ok",
            "config": config,
            "has_env_key": bool(env_key),
            "configured": bool(env_key or config.get('windy_api_key')),
            "locations": config.get('locations', [])
        })
    except Exception as e:
        logger.error(f"Error getting weather config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/weather_config', methods=['POST'])
def set_weather_config():
    """Update weather configuration (API keys and locations)"""
    global WEATHER_API_KEY, WEATHER_LOCATIONS
    try:
        data = request.get_json()
        api_key = data.get('windy_api_key', '').strip()
        locations = data.get('locations', [])
        
        # If API key is from environment, use that; otherwise require it in request
        env_key = os.environ.get('WINDY_API_KEY')
        if env_key:
            api_key = env_key
        elif not api_key:
            return jsonify({"status": "error", "message": "API key is required"}), 400
        
        # Validate locations if provided
        if locations:
            validated_locations = []
            for loc in locations:
                if isinstance(loc, dict) and 'lat' in loc and 'lon' in loc:
                    validated_locations.append({
                        "lat": float(loc['lat']),
                        "lon": float(loc['lon']),
                        "name": loc.get('name', f"{loc['lat']},{loc['lon']}")
                    })
            locations = validated_locations
        
        # Update global variables (only if not from environment)
        if not os.environ.get('WINDY_API_KEY'):
            WEATHER_API_KEY = api_key
        
        WEATHER_LOCATIONS = locations
        
        # Save to config file
        config = {
            "windy_api_key": api_key if not os.environ.get('WINDY_API_KEY') else "",
            "locations": locations
        }
        
        with open(WEATHER_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        
        save_weather_config()
        
        # Trigger update if enabled
        if WEATHER_ENABLED:
            update_weather_data()
        
        logger.info("Weather configuration updated")
        return jsonify({"status": "ok", "message": "Weather configuration updated successfully"})
    except Exception as e:
        logger.error(f"Error setting weather config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------
# Webcams API Endpoints
# ----------------------
@app.route('/api/webcams', methods=['GET'])
def api_webcams():
    """Get current webcam data"""
    global WEBCAMS_DATA
    return jsonify({
        "status": "ok",
        "webcams": WEBCAMS_DATA,
        "count": len(WEBCAMS_DATA)
    })

@app.route('/api/webcams_detection', methods=['GET'])
def get_webcams_detection():
    """Get current webcams detection enabled state"""
    return jsonify({"enabled": WEBCAMS_ENABLED})

@app.route('/api/webcams_detection', methods=['POST'])
def set_webcams_detection():
    """Toggle webcams detection on/off"""
    global WEBCAMS_ENABLED
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    WEBCAMS_ENABLED = enabled
    save_webcams_settings()
    
    if enabled:
        # Trigger immediate update when enabling
        update_webcams_data()
        logger.info("Webcams detection enabled")
    else:
        # Clear webcams data when disabling
        WEBCAMS_DATA.clear()
        try:
            socketio.emit('webcams_data', {'webcams': {}})
        except Exception as e:
            logger.debug(f"Error clearing webcams data: {e}")
        
        logger.info("Webcams detection disabled")
    
    return jsonify({"status": "ok", "enabled": WEBCAMS_ENABLED})

@app.route('/api/webcams_update', methods=['POST'])
def api_webcams_update():
    """Manually trigger webcams data update"""
    try:
        update_webcams_data()
        return jsonify({"status": "ok", "webcam_count": len(WEBCAMS_DATA)})
    except Exception as e:
        logger.error(f"Error in manual webcams update: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/webcams_config', methods=['GET'])
def get_webcams_config():
    """Get webcams configuration (API keys - masked for security)"""
    try:
        config = {}
        if os.path.exists(WEBCAMS_CONFIG_FILE):
            with open(WEBCAMS_CONFIG_FILE, "r") as f:
                config = json.load(f)
                # Mask API key for security
                if 'windy_webcams_api_key' in config:
                    key = config['windy_webcams_api_key']
                    if key:
                        config['windy_webcams_api_key'] = key[:8] + '...' + key[-4:] if len(key) > 12 else '***'
        
        # Check environment variables
        env_key = os.environ.get('WINDY_WEBCAMS_API_KEY')
        return jsonify({
            "status": "ok",
            "config": config,
            "has_env_key": bool(env_key),
            "configured": bool(env_key or config.get('windy_webcams_api_key'))
        })
    except Exception as e:
        logger.error(f"Error getting webcams config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/webcams_config', methods=['POST'])
def set_webcams_config():
    """Update webcams configuration (API keys)"""
    global WEBCAMS_API_KEY
    try:
        data = request.get_json()
        api_key = data.get('windy_webcams_api_key', '').strip()
        
        if not api_key:
            return jsonify({"status": "error", "message": "API key is required"}), 400
        
        # Update global variables (only if not from environment)
        if not os.environ.get('WINDY_WEBCAMS_API_KEY'):
            WEBCAMS_API_KEY = api_key
        
        # Save to config file
        config = {
            "windy_webcams_api_key": api_key if not os.environ.get('WINDY_WEBCAMS_API_KEY') else ""
        }
        
        with open(WEBCAMS_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        
        save_webcams_config()
        
        # Trigger update if enabled
        if WEBCAMS_ENABLED:
            update_webcams_data()
        
        logger.info("Webcams configuration updated")
        return jsonify({"status": "ok", "message": "Webcams configuration updated successfully"})
    except Exception as e:
        logger.error(f"Error setting webcams config: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------
# Fast Page Load API Endpoints (Database-backed)
# ----------------------
@app.route('/api/recent_data', methods=['GET'])
def api_recent_data():
    """Get all recent data for fast page load"""
    try:
        detections = get_recent_detections_from_db(minutes=5)
        ais_vessels = get_recent_ais_vessels_from_db(minutes=10)
        weather = get_recent_weather_from_db(minutes=10)
        webcams = get_recent_webcams_from_db(minutes=60)
        aprs_stations = get_recent_aprs_stations_from_db(minutes=10)
        
        # Convert detections to tracked_pairs format
        detections_dict = {}
        for det in detections:
            mac = det.get('mac')
            if mac:
                detections_dict[mac] = {
                    'mac': mac,
                    'alias': det.get('alias'),
                    'drone_lat': det.get('drone_lat'),
                    'drone_long': det.get('drone_lon'),
                    'drone_altitude': det.get('drone_altitude'),
                    'pilot_lat': det.get('pilot_lat'),
                    'pilot_long': det.get('pilot_lon'),
                    'basic_id': det.get('basic_id'),
                    'rssi': det.get('rssi'),
                    'faa_data': det.get('faa_data', {}),
                    'status': det.get('status', 'active'),
                    'last_update': det.get('last_update')
                }
        
        # Convert weather to expected format
        weather_dict = {}
        for w in weather:
            if w.get('weather'):
                location_key = w.get('location_key')
                if location_key:
                    weather_dict[location_key] = w['weather']
        
        # Convert AIS vessels to list
        ais_list = list(ais_vessels)
        
        # Convert APRS stations to list
        aprs_list = []
        for s in aprs_stations:
            aprs_list.append({
                'callsign': s.get('callsign'),
                'name': s.get('name'),
                'type': s.get('type'),
                'lat': s.get('lat'),
                'lng': s.get('lon'),
                'altitude': s.get('altitude'),
                'course': s.get('course'),
                'speed': s.get('speed'),
                'symbol': s.get('symbol'),
                'comment': s.get('comment'),
                'status': s.get('status'),
                'time': s.get('timestamp')
            })
        
        return jsonify({
            "status": "ok",
            "detections": detections_dict,
            "ais_vessels": ais_list,
            "weather": weather_dict,
            "webcams": webcams,
            "aprs_stations": aprs_list,
            "counts": {
                "detections": len(detections_dict),
                "ais_vessels": len(ais_list),
                "weather": len(weather_dict),
                "webcams": len(webcams),
                "aprs_stations": len(aprs_list)
            }
        })
    except Exception as e:
        logger.error(f"Error getting recent data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Webhook URL Persistence ---
WEBHOOK_URL_FILE = os.path.join(BASE_DIR, "webhook_url.json")

def save_webhook_url():
    """Save the current webhook URL to disk"""
    global WEBHOOK_URL
    try:
        with open(WEBHOOK_URL_FILE, "w") as f:
            json.dump({"webhook_url": WEBHOOK_URL}, f)
        logger.debug(f"Webhook URL saved to {WEBHOOK_URL_FILE}")
    except Exception as e:
        logger.error(f"Error saving webhook URL: {e}")

def load_webhook_url():
    """Load the webhook URL from disk on startup"""
    global WEBHOOK_URL
    if os.path.exists(WEBHOOK_URL_FILE):
        try:
            with open(WEBHOOK_URL_FILE, "r") as f:
                data = json.load(f)
                WEBHOOK_URL = data.get("webhook_url", None)
                if WEBHOOK_URL:
                    logger.info(f"Loaded saved webhook URL: {WEBHOOK_URL}")
                else:
                    logger.info("No webhook URL found in saved file")
        except Exception as e:
            logger.error(f"Error loading webhook URL: {e}")
            WEBHOOK_URL = None
    else:
        logger.info("No saved webhook URL file found")
        WEBHOOK_URL = None

def auto_connect_to_saved_ports():
    """
    Check if any previously saved ports are available and auto-connect to them.
    Returns True if at least one port was connected, False otherwise.
    """
    global SELECTED_PORTS
    
    if not SELECTED_PORTS:
        logger.info("No saved ports found for auto-connection")
        return False
    
    # Get currently available ports
    available_ports = {p.device for p in serial.tools.list_ports.comports()}
    logger.debug(f"Available ports: {available_ports}")
    
    # Check which saved ports are still available
    available_saved_ports = {}
    for port_key, port_device in SELECTED_PORTS.items():
        if port_device in available_ports:
            available_saved_ports[port_key] = port_device
    
    if not available_saved_ports:
        logger.warning("No previously used ports are currently available")
        return False
    
    logger.info(f"Auto-connecting to previously used ports: {list(available_saved_ports.values())}")
    
    # Update SELECTED_PORTS to only include available ports
    SELECTED_PORTS = available_saved_ports
    
    # Start serial threads for available ports
    for port in SELECTED_PORTS.values():
        serial_connected_status[port] = False
        start_serial_thread(port)
        logger.info(f"Started serial thread for port: {port}")
    
    # Send watchdog reset to each microcontroller over USB
    time.sleep(2)  # Give threads time to establish connections
    with serial_objs_lock:
        for port, ser in serial_objs.items():
            try:
                if ser and ser.is_open:
                    ser.write(b'WATCHDOG_RESET\n')
                    logger.debug(f"Sent watchdog reset to {port}")
            except Exception as e:
                logger.error(f"Failed to send watchdog reset to {port}: {e}")
    
    return True

# ----------------------
# Webhook Functions (moved here to be available before update_detection)
# ----------------------

if __name__ == '__main__':
    main()
