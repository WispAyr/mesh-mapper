"""
BLE Radar Module for Mesh-Mapper
================================
Uses a Sniffle-firmware CC2652P dongle to passively capture BLE advertisements,
classify devices by type (drones, phones, trackers, beacons, etc.), and expose
them to the mesh-mapper system.

Requires Sniffle (https://github.com/bkerler/Sniffle) cloned on the same host.

Author: Mesh-Mapper BLE Integration
License: GPLv3
"""

import os
import sys
import time
import json
import struct
import logging
import threading
from collections import defaultdict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sniffle import helper – the library lives in the Sniffle checkout
# ---------------------------------------------------------------------------
SNIFFLE_PATHS = [
    os.environ.get('SNIFFLE_PYTHON_CLI', ''),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Sniffle', 'python_cli'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Sniffle', 'python_cli'),
    '/home/drone/Sniffle/python_cli',
    os.path.expanduser('~/Sniffle/python_cli'),
]

_sniffle_available = False

# First, add all valid Sniffle paths to sys.path
for _p in SNIFFLE_PATHS:
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# Now attempt the import
try:
    from sniffle.sniffle_hw import make_sniffle_hw, SnifferMode
    from sniffle.packet_decoder import (
        PacketMessage, DPacketMessage, AdvertMessage, AdvaMessage,
        AdvIndMessage, AdvNonconnIndMessage, AdvScanIndMessage,
        ScanRspMessage, AdvExtIndMessage, AuxAdvIndMessage,
        AuxScanRspMessage, AdvDirectIndMessage,
    )
    from sniffle.advdata.decoder import decode_adv_data
    from sniffle.advdata.ad_types import (
        ManufacturerSpecificDataRecord, ServiceData16Record,
        ServiceList16Record, ServiceList128Record,
        CompleteLocalNameRecord, ShortenedLocalNameRecord,
    )
    _sniffle_available = True
    logger.info("Sniffle library loaded successfully")
except ImportError as exc:
    logger.warning("Sniffle library not found – BLE Radar will be unavailable: %s", exc)

# ---------------------------------------------------------------------------
# Constants – BLE company IDs and service UUIDs for classification
# ---------------------------------------------------------------------------
COMPANY_APPLE = 0x004C
COMPANY_SAMSUNG = 0x0075
COMPANY_MICROSOFT = 0x0006
COMPANY_GOOGLE = 0x00E0
COMPANY_TESLA = 0x02E5
COMPANY_FITBIT = 0x0139
COMPANY_GARMIN = 0x0087
COMPANY_TILE = 0x0258
COMPANY_BOSE = 0x009E
COMPANY_SONY = 0x012D

# Apple Continuity protocol types
APPLE_NEARBY = 0x10
APPLE_FINDMY = 0x12
APPLE_AIRPODS = 0x07
APPLE_HANDOFF = 0x0C
APPLE_HOTSPOT = 0x0F
APPLE_AIRPLAY = 0x09

# Service UUIDs
SVC_OPEN_DRONE_ID = 0xFFFA
SVC_EDDYSTONE = 0xFEAA
SVC_TILE = 0xFEED
SVC_FITBIT = 0xFEEC
SVC_BATTERY = 0x180F
SVC_HEART_RATE = 0x180D
SVC_GOOGLE_FAST_PAIR = 0xFE2C

# Open Drone ID message types (ASTM F3411)
ODID_MSG_BASIC_ID = 0x0
ODID_MSG_LOCATION = 0x1
ODID_MSG_AUTH = 0x2
ODID_MSG_SELF_ID = 0x3
ODID_MSG_SYSTEM = 0x4
ODID_MSG_OPERATOR_ID = 0x5
ODID_MSG_PACK = 0xF

# ---------------------------------------------------------------------------
# Open Drone ID Parser
# ---------------------------------------------------------------------------
def parse_open_drone_id(service_data):
    """Parse Open Drone ID / Remote ID BLE advertisement data.
    Returns a dict with parsed fields, or None on failure."""
    if len(service_data) < 1:
        return None

    result = {}
    # The first byte contains message type (upper nibble) and protocol version (lower nibble)
    msg_type = (service_data[0] >> 4) & 0x0F
    proto_ver = service_data[0] & 0x0F
    result['proto_version'] = proto_ver
    result['msg_type'] = msg_type

    payload = service_data[1:]

    if msg_type == ODID_MSG_BASIC_ID and len(payload) >= 20:
        id_type = (payload[0] >> 4) & 0x0F
        ua_type = payload[0] & 0x0F
        serial = payload[1:21].rstrip(b'\x00').decode('ascii', errors='replace')
        result['id_type'] = id_type
        result['ua_type'] = ua_type
        result['serial'] = serial

    elif msg_type == ODID_MSG_LOCATION and len(payload) >= 18:
        status = (payload[0] >> 4) & 0x0F
        height_type = payload[0] & 0x01
        direction = payload[1] * 1.0  # degrees
        speed_h = payload[2] * 0.25  # m/s (0.25 resolution)
        speed_v_raw = struct.unpack('<b', bytes([payload[3]]))[0]
        speed_v = speed_v_raw * 0.5  # m/s
        lat = struct.unpack('<i', payload[4:8])[0] * 1e-7
        lon = struct.unpack('<i', payload[8:12])[0] * 1e-7
        alt_press = struct.unpack('<H', payload[12:14])[0] * 0.5 - 1000  # meters
        alt_geo = struct.unpack('<H', payload[14:16])[0] * 0.5 - 1000  # meters
        height = struct.unpack('<H', payload[16:18])[0] * 0.5 - 1000
        result['status'] = status
        result['lat'] = lat
        result['lon'] = lon
        result['alt'] = alt_geo
        result['alt_pressure'] = alt_press
        result['height'] = height
        result['speed'] = speed_h
        result['speed_v'] = speed_v
        result['heading'] = direction

    elif msg_type == ODID_MSG_SYSTEM and len(payload) >= 18:
        op_class = payload[0] & 0x03
        op_lat = struct.unpack('<i', payload[1:5])[0] * 1e-7
        op_lon = struct.unpack('<i', payload[5:9])[0] * 1e-7
        area_count = struct.unpack('<H', payload[9:11])[0]
        area_radius = payload[11] * 10  # meters
        area_ceil = struct.unpack('<H', payload[12:14])[0] * 0.5 - 1000
        area_floor = struct.unpack('<H', payload[14:16])[0] * 0.5 - 1000
        result['operator_lat'] = op_lat
        result['operator_lon'] = op_lon
        result['area_count'] = area_count
        result['area_radius'] = area_radius

    elif msg_type == ODID_MSG_OPERATOR_ID and len(payload) >= 20:
        op_id_type = payload[0]
        operator_id = payload[1:21].rstrip(b'\x00').decode('ascii', errors='replace')
        result['operator_id_type'] = op_id_type
        result['operator_id'] = operator_id

    elif msg_type == ODID_MSG_SELF_ID and len(payload) >= 23:
        desc_type = payload[0]
        description = payload[1:24].rstrip(b'\x00').decode('ascii', errors='replace')
        result['description_type'] = desc_type
        result['description'] = description

    elif msg_type == ODID_MSG_PACK and len(payload) >= 2:
        # Message pack – contains multiple sub-messages
        pack_count = payload[0]
        result['pack_count'] = pack_count
        result['sub_messages'] = []
        offset = 1
        for _ in range(min(pack_count, 9)):
            if offset + 25 > len(payload):
                break
            sub_data = payload[offset:offset + 25]
            sub_result = parse_open_drone_id(sub_data)
            if sub_result:
                result['sub_messages'].append(sub_result)
            offset += 25

    return result


# ---------------------------------------------------------------------------
# BLE Device Classification Engine
# ---------------------------------------------------------------------------
def classify_device(mac_bytes, is_random, adv_records, adv_data_raw, local_name=None):
    """Classify a BLE device based on its advertisement data.

    Returns (category, subcategory, company, flags, remote_id_data).
    """
    category = 'unknown'
    subcategory = ''
    company = ''
    flags = []
    remote_id = None

    if is_random:
        flags.append('randomized_mac')

    # Gather all useful info from advertisement records
    mfr_data_list = []  # list of (company_id, company_data)
    service_uuids_16 = []
    service_uuids_128 = []
    service_data_16 = []  # list of (uuid, data)
    device_name = local_name or ''

    for rec in adv_records:
        if isinstance(rec, ManufacturerSpecificDataRecord):
            mfr_data_list.append((rec.company, rec.company_data))
        elif isinstance(rec, ServiceList16Record):
            service_uuids_16.extend(rec.services)
        elif isinstance(rec, ServiceList128Record):
            service_uuids_128.extend([str(u) for u in rec.services])
        elif isinstance(rec, ServiceData16Record):
            service_data_16.append((rec.service, rec.service_data))
        elif isinstance(rec, (CompleteLocalNameRecord, ShortenedLocalNameRecord)):
            device_name = rec.name

    # --- 1. Open Drone ID / Remote ID ---
    if SVC_OPEN_DRONE_ID in service_uuids_16:
        category = 'drone'
        subcategory = 'remote_id'
        company = 'Open Drone ID'
        for svc_uuid, svc_data in service_data_16:
            if svc_uuid == SVC_OPEN_DRONE_ID:
                remote_id = parse_open_drone_id(svc_data)
                break
        return category, subcategory, company, flags, remote_id

    # Also check for ODID in service data without explicit UUID listing
    for svc_uuid, svc_data in service_data_16:
        if svc_uuid == SVC_OPEN_DRONE_ID:
            category = 'drone'
            subcategory = 'remote_id'
            company = 'Open Drone ID'
            remote_id = parse_open_drone_id(svc_data)
            return category, subcategory, company, flags, remote_id

    # --- 2. Apple Devices ---
    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_APPLE:
            company = 'Apple'
            if len(comp_data) >= 2:
                continuity_type = comp_data[0]
                if continuity_type == APPLE_FINDMY:
                    category = 'tracker'
                    subcategory = 'apple_findmy'
                    # Distinguish AirTag from other FindMy devices
                    if len(comp_data) >= 3:
                        # AirTags have specific patterns
                        subcategory = 'apple_airtag'
                    return category, subcategory, company, flags, remote_id
                elif continuity_type == APPLE_NEARBY:
                    category = 'phone'
                    subcategory = 'apple_iphone'
                    return category, subcategory, company, flags, remote_id
                elif continuity_type == APPLE_AIRPODS:
                    category = 'audio'
                    subcategory = 'apple_airpods'
                    return category, subcategory, company, flags, remote_id
                elif continuity_type == APPLE_HANDOFF:
                    category = 'phone'
                    subcategory = 'apple_handoff'
                    return category, subcategory, company, flags, remote_id
                elif continuity_type == APPLE_HOTSPOT:
                    category = 'phone'
                    subcategory = 'apple_hotspot'
                    return category, subcategory, company, flags, remote_id
                elif continuity_type == APPLE_AIRPLAY:
                    category = 'audio'
                    subcategory = 'apple_airplay'
                    return category, subcategory, company, flags, remote_id
                else:
                    # iBeacon check: type 0x02, length 0x15
                    if continuity_type == 0x02 and len(comp_data) >= 2 and comp_data[1] == 0x15:
                        category = 'beacon'
                        subcategory = 'ibeacon'
                        return category, subcategory, company, flags, remote_id
                    category = 'phone'
                    subcategory = 'apple_other'
                    return category, subcategory, company, flags, remote_id

    # --- 3. Samsung ---
    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_SAMSUNG:
            company = 'Samsung'
            category = 'phone'
            subcategory = 'samsung_phone'
            # SmartTag detection – Samsung uses specific patterns
            if len(comp_data) >= 4:
                # SmartTag advertisements typically have specific identifiers
                if comp_data[0] == 0x42:
                    category = 'tracker'
                    subcategory = 'samsung_smarttag'
            return category, subcategory, company, flags, remote_id

    # --- 4. Tile ---
    if SVC_TILE in service_uuids_16:
        category = 'tracker'
        subcategory = 'tile'
        company = 'Tile'
        return category, subcategory, company, flags, remote_id

    # --- 5. Microsoft ---
    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_MICROSOFT:
            company = 'Microsoft'
            category = 'phone'
            subcategory = 'windows_device'
            return category, subcategory, company, flags, remote_id

    # --- 6. Tesla ---
    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_TESLA:
            company = 'Tesla'
            category = 'vehicle'
            subcategory = 'tesla'
            return category, subcategory, company, flags, remote_id

    # --- 7. Eddystone beacon ---
    if SVC_EDDYSTONE in service_uuids_16:
        category = 'beacon'
        subcategory = 'eddystone'
        return category, subcategory, company, flags, remote_id

    # --- 8. Google Fast Pair ---
    if SVC_GOOGLE_FAST_PAIR in service_uuids_16:
        company = 'Google'
        category = 'audio'
        subcategory = 'google_fast_pair'
        return category, subcategory, company, flags, remote_id

    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_GOOGLE:
            company = 'Google'
            category = 'phone'
            subcategory = 'google_device'
            return category, subcategory, company, flags, remote_id

    # --- 9. Fitbit ---
    if SVC_FITBIT in service_uuids_16:
        category = 'wearable'
        subcategory = 'fitbit'
        company = 'Fitbit'
        return category, subcategory, company, flags, remote_id

    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_FITBIT:
            company = 'Fitbit'
            category = 'wearable'
            subcategory = 'fitbit'
            return category, subcategory, company, flags, remote_id

    # --- 10. Garmin ---
    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_GARMIN:
            company = 'Garmin'
            category = 'wearable'
            subcategory = 'garmin'
            return category, subcategory, company, flags, remote_id

    # --- 11. Bose ---
    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_BOSE:
            company = 'Bose'
            category = 'audio'
            subcategory = 'bose'
            return category, subcategory, company, flags, remote_id

    # --- 12. Sony ---
    for comp_id, comp_data in mfr_data_list:
        if comp_id == COMPANY_SONY:
            company = 'Sony'
            category = 'audio'
            subcategory = 'sony'
            return category, subcategory, company, flags, remote_id

    # --- 13. Heart rate / fitness by service ---
    if SVC_HEART_RATE in service_uuids_16:
        category = 'wearable'
        subcategory = 'heart_rate_monitor'
        return category, subcategory, company, flags, remote_id

    # --- 14. Name-based heuristics ---
    name_lower = device_name.lower()
    if name_lower:
        # Meta/Oculus
        if any(kw in name_lower for kw in ['quest', 'oculus', 'meta']):
            category = 'phone'  # treat as smart device
            subcategory = 'meta_quest'
            company = 'Meta'
            return category, subcategory, company, flags, remote_id
        # Headphones / speakers
        if any(kw in name_lower for kw in ['buds', 'headphone', 'earphone', 'speaker', 'jbl', 'beats']):
            category = 'audio'
            subcategory = 'audio_device'
            return category, subcategory, company, flags, remote_id
        # Smart watches
        if any(kw in name_lower for kw in ['watch', 'band', 'mi band', 'galaxy watch']):
            category = 'wearable'
            subcategory = 'smartwatch'
            return category, subcategory, company, flags, remote_id

    # --- Fallback ---
    # Assign company from first manufacturer data if we have it
    if mfr_data_list and not company:
        comp_id = mfr_data_list[0][0]
        company = f'0x{comp_id:04X}'

    return category, subcategory, company, flags, remote_id


# ---------------------------------------------------------------------------
# BLERadar – main class
# ---------------------------------------------------------------------------
class BLERadar:
    """BLE Radar: passively captures and classifies BLE advertisements."""

    def __init__(self, serial_port='/dev/ttyUSB0', baud_rate=921600,
                 sniffle_path=None, callback=None, rssi_min=-100):
        """
        Args:
            serial_port:  Serial port of the Sniffle dongle.
            baud_rate:    Baud rate (921600 for CP2102-based dongles).
            sniffle_path: Optional path to Sniffle python_cli directory.
            callback:     Optional callback(event_type, device_data) for each detection.
            rssi_min:     Minimum RSSI to accept (-128 for everything).
        """
        if not _sniffle_available:
            raise RuntimeError("Sniffle library not available – cannot start BLE Radar")

        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.callback = callback
        self.rssi_min = rssi_min

        # Devices dict: {mac_str: device_dict}
        self._devices = {}
        self._lock = threading.Lock()

        # Drone detections (merged from multiple ODID messages): {mac_str: drone_data}
        self._drones = {}
        self._drones_lock = threading.Lock()

        # Stats
        self._stats = {
            'total_packets': 0,
            'start_time': 0,
            'by_category': defaultdict(int),
        }

        # Thread control
        self._running = False
        self._thread = None
        self._hw = None

    def start(self):
        """Start scanning in a background thread."""
        if self._running:
            logger.warning("BLE Radar already running")
            return

        self._running = True
        self._stats['start_time'] = time.time()
        self._thread = threading.Thread(target=self._scan_loop, daemon=True, name='BLERadar')
        self._thread.start()
        logger.info("BLE Radar started on %s @ %d baud", self.serial_port, self.baud_rate)

    def stop(self):
        """Stop the scanner."""
        self._running = False
        if self._hw:
            try:
                self._hw.cancel_recv()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("BLE Radar stopped")

    def get_devices(self):
        """Return a copy of the current device dict."""
        with self._lock:
            return dict(self._devices)

    def get_stats(self):
        """Return statistics dict."""
        elapsed = time.time() - self._stats['start_time'] if self._stats['start_time'] else 0
        with self._lock:
            total = len(self._devices)
            by_cat = dict(self._stats['by_category'])
        return {
            'total_devices': total,
            'total_packets': self._stats['total_packets'],
            'by_category': by_cat,
            'scan_duration': elapsed,
            'scan_rate': self._stats['total_packets'] / max(elapsed, 1),
        }

    def get_drone_detections(self):
        """Return only Remote ID drone detections, in mesh-mapper compatible format."""
        with self._drones_lock:
            return dict(self._drones)

    def prune_stale(self, max_age=300):
        """Remove devices not seen for max_age seconds."""
        now = time.time()
        stale_macs = []
        with self._lock:
            for mac, dev in self._devices.items():
                if now - dev['last_seen'] > max_age:
                    stale_macs.append(mac)
            for mac in stale_macs:
                cat = self._devices[mac].get('category', 'unknown')
                del self._devices[mac]
                if self._stats['by_category'].get(cat, 0) > 0:
                    self._stats['by_category'][cat] -= 1
        with self._drones_lock:
            stale_drones = [m for m, d in self._drones.items() if now - d.get('last_seen', 0) > max_age]
            for m in stale_drones:
                del self._drones[m]

    # ----- internal -----

    def _scan_loop(self):
        """Main scanning loop – runs in background thread."""
        retry_delay = 2
        while self._running:
            try:
                logger.info("Connecting to Sniffle dongle on %s", self.serial_port)
                self._hw = make_sniffle_hw(self.serial_port, baudrate=self.baud_rate)

                # Verify firmware
                ver = self._hw.probe_fw_version()
                if ver:
                    logger.info("Sniffle firmware: %s", ver)

                # Configure: passive advertisement sniffer with ext-adv support
                self._hw.setup_sniffer(
                    mode=SnifferMode.CONN_FOLLOW,
                    chan=37,
                    ext_adv=True,
                    rssi_min=self.rssi_min,
                )
                self._hw.mark_and_flush()
                logger.info("BLE Radar scanning active")
                retry_delay = 2

                while self._running:
                    try:
                        msg = self._hw.recv_and_decode()
                    except Exception as e:
                        if not self._running:
                            break
                        logger.warning("recv error: %s", e)
                        time.sleep(0.1)
                        continue

                    if msg is None:
                        continue

                    if isinstance(msg, AdvertMessage):
                        self._stats['total_packets'] += 1
                        self._handle_advert(msg)

            except Exception as e:
                if not self._running:
                    break
                logger.error("BLE Radar error: %s – retrying in %ds", e, retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

            finally:
                if self._hw:
                    try:
                        self._hw.ser.close()
                    except Exception:
                        pass
                    self._hw = None

    def _handle_advert(self, msg):
        """Process a single BLE advertisement."""
        # Extract MAC address
        mac_bytes = None
        is_random = False
        adv_data_raw = b''

        if hasattr(msg, 'AdvA') and msg.AdvA:
            mac_bytes = msg.AdvA
            is_random = bool(msg.TxAdd)
        else:
            return  # Can't track without a MAC

        mac_str = ':'.join(['%02X' % b for b in reversed(mac_bytes)])

        # Get raw advertisement data
        if hasattr(msg, 'adv_data') and msg.adv_data:
            adv_data_raw = msg.adv_data

        # Decode advertisement records
        adv_records = []
        local_name = None
        if adv_data_raw:
            try:
                adv_records = decode_adv_data(adv_data_raw)
                for rec in adv_records:
                    if isinstance(rec, (CompleteLocalNameRecord, ShortenedLocalNameRecord)):
                        local_name = rec.name
            except Exception as e:
                logger.debug("adv_data decode error for %s: %s", mac_str, e)

        # Classify
        category, subcategory, company, dev_flags, remote_id = classify_device(
            mac_bytes, is_random, adv_records, adv_data_raw, local_name
        )

        now = time.time()

        # Determine connectable flag
        connectable = isinstance(msg, (AdvIndMessage, AdvScanIndMessage))
        if connectable:
            dev_flags.append('connectable')

        # Build/update device record
        with self._lock:
            existing = self._devices.get(mac_str)
            if existing:
                existing['rssi'] = msg.rssi
                existing['last_seen'] = now
                existing['advert_count'] += 1
                # Update category if previously unknown and now classified
                if existing['category'] == 'unknown' and category != 'unknown':
                    # Adjust stats
                    self._stats['by_category']['unknown'] = max(0, self._stats['by_category'].get('unknown', 1) - 1)
                    self._stats['by_category'][category] += 1
                    existing['category'] = category
                    existing['subcategory'] = subcategory
                    existing['company'] = company
                if local_name and not existing.get('name'):
                    existing['name'] = local_name
                if remote_id:
                    existing['remote_id'] = remote_id
                device = existing
            else:
                device = {
                    'mac': mac_str,
                    'mac_type': 'random' if is_random else 'public',
                    'category': category,
                    'subcategory': subcategory,
                    'company': company,
                    'name': local_name or '',
                    'rssi': msg.rssi,
                    'first_seen': now,
                    'last_seen': now,
                    'advert_count': 1,
                    'flags': dev_flags,
                }
                if remote_id:
                    device['remote_id'] = remote_id
                self._devices[mac_str] = device
                self._stats['by_category'][category] += 1

        # Handle drone Remote ID specifically
        if category == 'drone' and remote_id:
            self._update_drone(mac_str, device, remote_id, msg.rssi, now)

        # Fire callback
        if self.callback:
            try:
                evt = 'ble_drone' if category == 'drone' else 'ble_device'
                self.callback(evt, device)
            except Exception as e:
                logger.debug("BLE callback error: %s", e)

    def _update_drone(self, mac_str, device, remote_id, rssi, now):
        """Merge ODID message into drone tracking state."""
        with self._drones_lock:
            drone = self._drones.get(mac_str, {
                'mac': mac_str,
                'source': 'ble_remoteid',
                'rssi': rssi,
                'first_seen': now,
                'last_seen': now,
                'basic_id': '',
                'drone_lat': 0,
                'drone_long': 0,
                'drone_altitude': 0,
                'horizontal_speed': 0,
                'vertical_speed': 0,
                'heading': 0,
                'pilot_lat': 0,
                'pilot_long': 0,
                'operator_id': '',
                'description': '',
            })

            drone['rssi'] = rssi
            drone['last_seen'] = now

            msg_type = remote_id.get('msg_type')
            if msg_type == ODID_MSG_BASIC_ID:
                drone['basic_id'] = remote_id.get('serial', '')
            elif msg_type == ODID_MSG_LOCATION:
                drone['drone_lat'] = remote_id.get('lat', 0)
                drone['drone_long'] = remote_id.get('lon', 0)
                drone['drone_altitude'] = remote_id.get('alt', 0)
                drone['horizontal_speed'] = remote_id.get('speed', 0)
                drone['vertical_speed'] = remote_id.get('speed_v', 0)
                drone['heading'] = remote_id.get('heading', 0)
            elif msg_type == ODID_MSG_SYSTEM:
                drone['pilot_lat'] = remote_id.get('operator_lat', 0)
                drone['pilot_long'] = remote_id.get('operator_lon', 0)
            elif msg_type == ODID_MSG_OPERATOR_ID:
                drone['operator_id'] = remote_id.get('operator_id', '')
            elif msg_type == ODID_MSG_SELF_ID:
                drone['description'] = remote_id.get('description', '')
            elif msg_type == ODID_MSG_PACK:
                # Process sub-messages recursively
                for sub in remote_id.get('sub_messages', []):
                    self._update_drone(mac_str, device, sub, rssi, now)
                    return  # Already updated everything

            self._drones[mac_str] = drone


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')

    import argparse
    parser = argparse.ArgumentParser(description='BLE Radar standalone test')
    parser.add_argument('-s', '--serial', default='/dev/ttyUSB0', help='Serial port')
    parser.add_argument('-b', '--baud', default=921600, type=int, help='Baud rate')
    parser.add_argument('-r', '--rssi', default=-100, type=int, help='Min RSSI filter')
    parser.add_argument('-t', '--time', default=30, type=int, help='Scan duration (seconds)')
    args = parser.parse_args()

    def on_event(evt, data):
        cat = data.get('category', '?')
        sub = data.get('subcategory', '')
        mac = data.get('mac', '?')
        rssi = data.get('rssi', 0)
        name = data.get('name', '')
        company = data.get('company', '')
        count = data.get('advert_count', 0)
        print(f"  [{evt}] {cat}/{sub} MAC={mac} RSSI={rssi} Company={company} Name={name} Count={count}")

    radar = BLERadar(
        serial_port=args.serial,
        baud_rate=args.baud,
        rssi_min=args.rssi,
        callback=on_event,
    )

    radar.start()
    try:
        time.sleep(args.time)
    except KeyboardInterrupt:
        pass
    finally:
        radar.stop()

    # Print summary
    stats = radar.get_stats()
    print(f"\n=== BLE Radar Summary ===")
    print(f"Duration: {stats['scan_duration']:.1f}s")
    print(f"Packets:  {stats['total_packets']}")
    print(f"Devices:  {stats['total_devices']}")
    print(f"Rate:     {stats['scan_rate']:.1f} pkt/s")
    print(f"\nBy category:")
    for cat, count in sorted(stats['by_category'].items()):
        print(f"  {cat}: {count}")

    print(f"\nAll devices:")
    for mac, dev in sorted(radar.get_devices().items(), key=lambda x: x[1]['advert_count'], reverse=True):
        print(f"  {mac} [{dev['category']}/{dev['subcategory']}] "
              f"RSSI={dev['rssi']} Count={dev['advert_count']} "
              f"Company={dev['company']} Name={dev.get('name', '')}")
