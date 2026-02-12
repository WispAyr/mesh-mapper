"""
Bluetooth Research Toolkit — bt_toolkit.py
==========================================
Backend module for the Mesh Mapper Bluetooth research toolkit.

Provides BLE scanning, advertising, GATT exploration, Classic BT discovery,
HCI monitoring, and resilience testing tools via hci0 (built-in radio).

Dependencies (install on Pi):
    pip install bleak dbus-next

Does NOT conflict with ble_radar.py which uses Sniffle on /dev/ttyUSB0.
This module uses the built-in hci0 adapter via BlueZ/D-Bus.

Thread-safety: All operations use proper asyncio/threading bridges.
Safety: All adversarial tests have automatic timeouts (max 60s default).
"""

import asyncio
import json
import logging
import os
import re
import signal
import struct
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger('bt_toolkit')

# ---------------------------------------------------------------------------
# Lazy imports — bleak / dbus-next may not be installed
# ---------------------------------------------------------------------------
_bleak_available = False
_dbus_next_available = False

try:
    import bleak
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    _bleak_available = True
except ImportError:
    logger.warning("bleak not installed — BLE scanning/GATT features unavailable. pip install bleak")

try:
    import dbus_next
    _dbus_next_available = True
except ImportError:
    logger.warning("dbus-next not installed — BLE advertising features unavailable. pip install dbus-next")


# ===========================================================================
#  Adapter Management
# ===========================================================================

class AdapterManager:
    """Discover and control Bluetooth adapters via btmgmt / hciconfig."""

    @staticmethod
    def list_adapters() -> List[Dict[str, Any]]:
        """List all Bluetooth adapters with capabilities and status."""
        adapters = []
        try:
            result = subprocess.run(
                ['btmgmt', 'info'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                adapters = AdapterManager._parse_btmgmt_info(result.stdout)
        except FileNotFoundError:
            logger.warning("btmgmt not found, falling back to hciconfig")
        except Exception as e:
            logger.error(f"btmgmt failed: {e}")

        if not adapters:
            adapters = AdapterManager._list_via_hciconfig()

        return adapters

    @staticmethod
    def _parse_btmgmt_info(output: str) -> List[Dict[str, Any]]:
        """Parse btmgmt info output into adapter dicts."""
        adapters = []
        current = None

        for line in output.splitlines():
            line = line.strip()

            # New controller block: "hci0:	Primary controller"
            m = re.match(r'^(hci\d+):\s+(.*)', line)
            if m:
                if current:
                    adapters.append(current)
                current = {
                    'id': m.group(1),
                    'type': m.group(2).strip(),
                    'address': '',
                    'powered': False,
                    'discoverable': False,
                    'pairable': False,
                    'le': False,
                    'bredr': False,
                    'name': '',
                    'settings': [],
                    'supported_settings': [],
                }
                continue

            if current is None:
                continue

            if line.startswith('addr'):
                parts = line.split()
                if len(parts) >= 2:
                    current['address'] = parts[1]

            elif line.startswith('name'):
                current['name'] = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ''

            elif line.startswith('current settings:'):
                settings_str = line.split(':', 1)[1].strip()
                settings = [s.strip() for s in settings_str.split() if s.strip()]
                current['settings'] = settings
                current['powered'] = 'powered' in settings
                current['discoverable'] = 'discoverable' in settings
                current['pairable'] = 'pairable' in settings
                current['le'] = 'le' in settings
                current['bredr'] = 'bredr' in settings

            elif line.startswith('supported settings:'):
                settings_str = line.split(':', 1)[1].strip()
                current['supported_settings'] = [s.strip() for s in settings_str.split() if s.strip()]

        if current:
            adapters.append(current)

        return adapters

    @staticmethod
    def _list_via_hciconfig() -> List[Dict[str, Any]]:
        """Fallback: list adapters via hciconfig."""
        adapters = []
        try:
            result = subprocess.run(
                ['hciconfig', '-a'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return adapters

            current = None
            for line in result.stdout.splitlines():
                m = re.match(r'^(hci\d+):', line)
                if m:
                    if current:
                        adapters.append(current)
                    current = {
                        'id': m.group(1),
                        'type': 'Primary controller',
                        'address': '',
                        'powered': 'UP' in line,
                        'discoverable': False,
                        'pairable': False,
                        'le': True,
                        'bredr': True,
                        'name': '',
                        'settings': [],
                        'supported_settings': [],
                    }
                    if 'UP' in line:
                        current['settings'].append('powered')
                    if 'RUNNING' in line:
                        current['settings'].append('running')
                    continue

                if current is None:
                    continue

                line_stripped = line.strip()
                if line_stripped.startswith('BD Address:'):
                    parts = line_stripped.split()
                    if len(parts) >= 3:
                        current['address'] = parts[2]
                elif line_stripped.startswith('Name:'):
                    current['name'] = line_stripped.split(':', 1)[1].strip().strip("'")

            if current:
                adapters.append(current)

        except Exception as e:
            logger.error(f"hciconfig fallback failed: {e}")

        return adapters

    @staticmethod
    def configure_adapter(adapter_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Apply settings to an adapter via btmgmt."""
        results = {}
        hci_index = adapter_id.replace('hci', '')

        setting_map = {
            'powered': 'power',
            'discoverable': 'discov',
            'pairable': 'bondable',
            'privacy': 'privacy',
            'le': 'le',
            'bredr': 'bredr',
        }

        for key, value in settings.items():
            cmd_name = setting_map.get(key)
            if not cmd_name:
                results[key] = {'status': 'error', 'message': f'Unknown setting: {key}'}
                continue

            val_str = 'on' if value else 'off'
            try:
                result = subprocess.run(
                    ['btmgmt', '--index', hci_index, cmd_name, val_str],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    results[key] = {'status': 'ok', 'value': value}
                else:
                    results[key] = {'status': 'error', 'message': result.stderr.strip() or result.stdout.strip()}
            except Exception as e:
                results[key] = {'status': 'error', 'message': str(e)}

        return results


# ===========================================================================
#  BLE Scanner
# ===========================================================================

class BLEScanner:
    """Active/passive BLE scanning via bleak with SocketIO streaming."""

    def __init__(self, socketio=None):
        self._socketio = socketio
        self._scanning = False
        self._scan_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._devices: Dict[str, Dict[str, Any]] = OrderedDict()
        self._devices_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    def get_devices(self) -> Dict[str, Dict[str, Any]]:
        with self._devices_lock:
            return dict(self._devices)

    def start_scan(self, duration: float = 30.0, active: bool = True,
                   adapter: str = 'hci0') -> bool:
        """Start a BLE scan in a background thread."""
        if not _bleak_available:
            logger.error("bleak not available — cannot scan")
            return False
        if self._scanning:
            logger.warning("Scan already in progress")
            return False

        self._scanning = True
        self._stop_event.clear()

        self._scan_thread = threading.Thread(
            target=self._scan_worker,
            args=(duration, active, adapter),
            daemon=True,
            name='BLEToolkitScan'
        )
        self._scan_thread.start()
        return True

    def stop_scan(self):
        """Stop the current scan."""
        if not self._scanning:
            return
        self._stop_event.set()
        self._scanning = False

    def clear_devices(self):
        """Clear discovered devices."""
        with self._devices_lock:
            self._devices.clear()

    def _scan_worker(self, duration: float, active: bool, adapter: str):
        """Run the async BLE scan in its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_scan(duration, active, adapter))
        except Exception as e:
            logger.error(f"BLE scan error: {e}")
        finally:
            self._scanning = False
            self._loop = None
            loop.close()
            if self._socketio:
                try:
                    with self._devices_lock:
                        device_count = len(self._devices)
                    self._socketio.emit('bt_scan_complete', {
                        'device_count': device_count,
                        'duration': duration,
                    })
                except Exception:
                    pass

    async def _async_scan(self, duration: float, active: bool, adapter: str):
        """Async BLE scan using bleak."""
        scan_mode = 'active' if active else 'passive'
        logger.info(f"Starting BLE {scan_mode} scan for {duration}s on {adapter}")

        kwargs = {}
        # bleak on Linux uses adapter argument
        kwargs['adapter'] = adapter
        if not active:
            kwargs['scanning_mode'] = 'passive'

        scanner = BleakScanner(
            detection_callback=self._detection_callback,
            **kwargs
        )

        await scanner.start()
        try:
            # Wait for duration or stop event
            start_time = time.time()
            while time.time() - start_time < duration and not self._stop_event.is_set():
                await asyncio.sleep(0.5)
        finally:
            await scanner.stop()

        logger.info(f"BLE scan complete. Found {len(self._devices)} devices")

    def _detection_callback(self, device: 'BLEDevice', adv_data: 'AdvertisementData'):
        """Called for each BLE advertisement detected."""
        addr = device.address
        now = time.time()

        # Parse manufacturer data
        mfr_data = {}
        if adv_data.manufacturer_data:
            for company_id, data in adv_data.manufacturer_data.items():
                mfr_data[str(company_id)] = data.hex()

        # Parse service UUIDs
        service_uuids = list(adv_data.service_uuids) if adv_data.service_uuids else []

        # Parse service data
        svc_data = {}
        if adv_data.service_data:
            for svc_uuid, data in adv_data.service_data.items():
                svc_data[str(svc_uuid)] = data.hex()

        device_info = {
            'address': addr,
            'name': adv_data.local_name or device.name or '',
            'rssi': adv_data.rssi,
            'tx_power': adv_data.tx_power,
            'manufacturer_data': mfr_data,
            'service_uuids': service_uuids,
            'service_data': svc_data,
            'address_type': getattr(device.details, 'AddressType', 'unknown') if hasattr(device, 'details') else 'unknown',
            'last_seen': now,
        }

        is_new = False
        with self._devices_lock:
            if addr not in self._devices:
                device_info['first_seen'] = now
                device_info['seen_count'] = 1
                is_new = True
            else:
                device_info['first_seen'] = self._devices[addr].get('first_seen', now)
                device_info['seen_count'] = self._devices[addr].get('seen_count', 0) + 1
                # Keep name if we had one before and this update is empty
                if not device_info['name'] and self._devices[addr].get('name'):
                    device_info['name'] = self._devices[addr]['name']

            self._devices[addr] = device_info

        # Emit via SocketIO
        if self._socketio:
            try:
                event = 'bt_device_found' if is_new else 'bt_device_updated'
                self._socketio.emit(event, device_info)
            except Exception:
                pass


# ===========================================================================
#  BLE Advertiser (BlueZ D-Bus)
# ===========================================================================

class BLEAdvertiser:
    """Manage BLE advertisements via BlueZ D-Bus LEAdvertisingManager."""

    def __init__(self, socketio=None):
        self._socketio = socketio
        self._active_adverts: Dict[str, Dict[str, Any]] = {}
        self._advert_threads: Dict[str, threading.Thread] = {}
        self._stop_events: Dict[str, threading.Event] = {}

    @property
    def active_count(self) -> int:
        return len(self._active_adverts)

    def get_status(self) -> Dict[str, Any]:
        return {
            'active_count': self.active_count,
            'advertisements': {k: {
                'name': v.get('name', ''),
                'type': v.get('type', 'peripheral'),
                'started_at': v.get('started_at', 0),
            } for k, v in self._active_adverts.items()}
        }

    def start_advertisement(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Start a BLE advertisement.

        Config keys:
            name: str - Local name to advertise
            service_uuids: list[str] - Service UUIDs
            manufacturer_data: dict - {company_id: hex_data}
            tx_power: int - TX power level
            adv_type: str - 'peripheral' or 'broadcast'
            preset: str - 'ibeacon' or 'eddystone' (overrides other fields)
            adapter: str - hci adapter (default 'hci0')
        """
        adv_id = str(uuid.uuid4())[:8]

        # Handle presets
        preset = config.get('preset')
        if preset == 'ibeacon':
            config = self._build_ibeacon_config(config)
        elif preset == 'eddystone':
            config = self._build_eddystone_config(config)

        config['started_at'] = time.time()
        self._active_adverts[adv_id] = config

        stop_event = threading.Event()
        self._stop_events[adv_id] = stop_event

        thread = threading.Thread(
            target=self._advert_worker,
            args=(adv_id, config, stop_event),
            daemon=True,
            name=f'BLEAdvert-{adv_id}'
        )
        self._advert_threads[adv_id] = thread
        thread.start()

        return {'status': 'ok', 'adv_id': adv_id, 'config': config}

    def stop_advertisement(self, adv_id: str = None):
        """Stop a specific advertisement or all if adv_id is None."""
        if adv_id:
            if adv_id in self._stop_events:
                self._stop_events[adv_id].set()
                self._active_adverts.pop(adv_id, None)
                self._stop_events.pop(adv_id, None)
        else:
            for evt in self._stop_events.values():
                evt.set()
            self._active_adverts.clear()
            self._stop_events.clear()

    def _build_ibeacon_config(self, config: Dict) -> Dict:
        """Build iBeacon advertisement configuration."""
        beacon_uuid = config.get('uuid', str(uuid.uuid4()))
        major = config.get('major', 1)
        minor = config.get('minor', 1)
        tx_power = config.get('measured_power', -59)

        # iBeacon uses Apple company ID (0x004C)
        # Prefix: 02 15 (iBeacon identifier)
        uuid_bytes = uuid.UUID(beacon_uuid).bytes
        payload = b'\x02\x15' + uuid_bytes + struct.pack('>HHb', major, minor, tx_power)

        config['manufacturer_data'] = {'76': payload.hex()}  # 76 = 0x004C Apple
        config['adv_type'] = 'broadcast'
        config['name'] = config.get('name', 'iBeacon')
        return config

    def _build_eddystone_config(self, config: Dict) -> Dict:
        """Build Eddystone-UID advertisement configuration."""
        namespace = config.get('namespace', 'aabbccddeeff00112233')
        instance = config.get('instance', '000000000001')
        tx_power = config.get('tx_power', -20)

        # Eddystone-UID frame
        frame = struct.pack('b', 0x00)  # Frame type: UID
        frame += struct.pack('b', tx_power)
        frame += bytes.fromhex(namespace[:20].ljust(20, '0'))
        frame += bytes.fromhex(instance[:12].ljust(12, '0'))
        frame += b'\x00\x00'  # RFU

        config['service_uuids'] = ['0000feaa-0000-1000-8000-00805f9b34fb']
        config['service_data'] = {'0000feaa-0000-1000-8000-00805f9b34fb': frame.hex()}
        config['adv_type'] = 'broadcast'
        config['name'] = config.get('name', 'Eddystone')
        return config

    def _advert_worker(self, adv_id: str, config: Dict, stop_event: threading.Event):
        """Run advertisement registration via bluetoothctl/hcitool as fallback.

        Primary method uses btmgmt add-adv for direct HCI control.
        """
        adapter = config.get('adapter', 'hci0')
        hci_index = adapter.replace('hci', '')
        name = config.get('name', 'MeshMapper')

        logger.info(f"Starting BLE advertisement {adv_id}: name={name} on {adapter}")

        try:
            # Use btmgmt to add advertisement
            # First, ensure adapter is powered
            subprocess.run(
                ['btmgmt', '--index', hci_index, 'power', 'on'],
                capture_output=True, timeout=5
            )

            # Set local name
            if name:
                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'name', name],
                    capture_output=True, timeout=5
                )

            # Use hcitool for raw advertising if manufacturer data specified
            mfr_data = config.get('manufacturer_data', {})
            service_uuids = config.get('service_uuids', [])

            if mfr_data or service_uuids:
                adv_data = self._build_hci_adv_data(config)
                self._set_hci_advertising(hci_index, adv_data, stop_event)
            else:
                # Simple name-only advertising via btmgmt
                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'discov', 'on'],
                    capture_output=True, timeout=5
                )

                # Keep advertising until stopped
                while not stop_event.is_set():
                    stop_event.wait(1.0)

                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'discov', 'off'],
                    capture_output=True, timeout=5
                )

        except Exception as e:
            logger.error(f"Advertisement {adv_id} error: {e}")
        finally:
            self._active_adverts.pop(adv_id, None)
            self._stop_events.pop(adv_id, None)
            logger.info(f"Advertisement {adv_id} stopped")

    def _build_hci_adv_data(self, config: Dict) -> bytes:
        """Build HCI advertising data payload."""
        data = b''

        # Flags
        data += bytes([0x02, 0x01, 0x06])

        # Local name
        name = config.get('name', '')
        if name:
            name_bytes = name.encode('utf-8')[:24]
            data += bytes([len(name_bytes) + 1, 0x09]) + name_bytes

        # Manufacturer data
        for company_id_str, hex_data in config.get('manufacturer_data', {}).items():
            company_id = int(company_id_str)
            mfr_bytes = struct.pack('<H', company_id) + bytes.fromhex(hex_data)
            data += bytes([len(mfr_bytes) + 1, 0xFF]) + mfr_bytes

        return data

    def _set_hci_advertising(self, hci_index: str, adv_data: bytes, stop_event: threading.Event):
        """Set advertising data via hcitool."""
        try:
            # Enable advertising
            subprocess.run(
                ['hcitool', '-i', f'hci{hci_index}', 'cmd', '0x08', '0x0008'] +
                [f'{len(adv_data):02x}'] + [f'{b:02x}' for b in adv_data],
                capture_output=True, timeout=5
            )
            # Enable advertising
            subprocess.run(
                ['hcitool', '-i', f'hci{hci_index}', 'cmd', '0x08', '0x000a', '01'],
                capture_output=True, timeout=5
            )

            while not stop_event.is_set():
                stop_event.wait(1.0)

            # Disable advertising
            subprocess.run(
                ['hcitool', '-i', f'hci{hci_index}', 'cmd', '0x08', '0x000a', '00'],
                capture_output=True, timeout=5
            )
        except Exception as e:
            logger.error(f"HCI advertising error: {e}")


# ===========================================================================
#  GATT Explorer
# ===========================================================================

class GATTExplorer:
    """Connect to BLE devices and explore GATT services/characteristics."""

    def __init__(self, socketio=None):
        self._socketio = socketio
        self._client: Optional[Any] = None  # BleakClient
        self._connected_address: Optional[str] = None
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._notification_handlers: Dict[str, bool] = {}

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def connected_device(self) -> Optional[str]:
        return self._connected_address if self.is_connected else None

    def _ensure_loop(self):
        """Ensure we have a running event loop for async operations."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever,
                daemon=True,
                name='GATTEventLoop'
            )
            self._loop_thread.start()

    def _run_async(self, coro):
        """Run an async coroutine in the GATT event loop."""
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def connect(self, address: str, adapter: str = 'hci0') -> Dict[str, Any]:
        """Connect to a BLE device."""
        if not _bleak_available:
            return {'status': 'error', 'message': 'bleak not installed'}

        with self._lock:
            if self.is_connected:
                self.disconnect()

            try:
                result = self._run_async(self._async_connect(address, adapter))
                return result
            except Exception as e:
                logger.error(f"GATT connect error: {e}")
                return {'status': 'error', 'message': str(e)}

    def disconnect(self) -> Dict[str, Any]:
        """Disconnect from current device."""
        with self._lock:
            if not self.is_connected:
                return {'status': 'ok', 'message': 'Not connected'}
            try:
                self._run_async(self._async_disconnect())
                return {'status': 'ok', 'message': 'Disconnected'}
            except Exception as e:
                self._client = None
                self._connected_address = None
                return {'status': 'ok', 'message': f'Disconnected (with error: {e})'}

    def get_services(self) -> Dict[str, Any]:
        """Enumerate services and characteristics."""
        if not self.is_connected:
            return {'status': 'error', 'message': 'Not connected'}

        try:
            services = []
            for service in self._client.services:
                chars = []
                for char in service.characteristics:
                    descriptors = []
                    for desc in char.descriptors:
                        descriptors.append({
                            'uuid': str(desc.uuid),
                            'handle': desc.handle,
                        })
                    chars.append({
                        'uuid': str(char.uuid),
                        'handle': char.handle,
                        'properties': char.properties,
                        'descriptors': descriptors,
                    })
                services.append({
                    'uuid': str(service.uuid),
                    'handle': service.handle,
                    'characteristics': chars,
                })
            return {'status': 'ok', 'services': services, 'device': self._connected_address}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def read_characteristic(self, char_uuid: str) -> Dict[str, Any]:
        """Read a characteristic value."""
        if not self.is_connected:
            return {'status': 'error', 'message': 'Not connected'}

        try:
            value = self._run_async(self._client.read_gatt_char(char_uuid))
            result = {
                'status': 'ok',
                'uuid': char_uuid,
                'value_hex': value.hex(),
                'value_ascii': value.decode('utf-8', errors='replace'),
                'length': len(value),
            }
            if self._socketio:
                self._socketio.emit('bt_gatt_data', {
                    'type': 'read',
                    'device': self._connected_address,
                    **result
                })
            return result
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def write_characteristic(self, char_uuid: str, value_hex: str,
                             with_response: bool = True) -> Dict[str, Any]:
        """Write a value to a characteristic."""
        if not self.is_connected:
            return {'status': 'error', 'message': 'Not connected'}

        try:
            data = bytes.fromhex(value_hex)
            self._run_async(
                self._client.write_gatt_char(char_uuid, data, response=with_response)
            )
            return {
                'status': 'ok',
                'uuid': char_uuid,
                'written_hex': value_hex,
                'length': len(data),
            }
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def subscribe_notifications(self, char_uuid: str) -> Dict[str, Any]:
        """Subscribe to characteristic notifications."""
        if not self.is_connected:
            return {'status': 'error', 'message': 'Not connected'}

        try:
            def notification_handler(sender, data):
                if self._socketio:
                    self._socketio.emit('bt_gatt_data', {
                        'type': 'notification',
                        'device': self._connected_address,
                        'uuid': char_uuid,
                        'sender': str(sender),
                        'value_hex': data.hex(),
                        'value_ascii': data.decode('utf-8', errors='replace'),
                        'length': len(data),
                        'timestamp': time.time(),
                    })

            self._run_async(
                self._client.start_notify(char_uuid, notification_handler)
            )
            self._notification_handlers[char_uuid] = True
            return {'status': 'ok', 'uuid': char_uuid, 'subscribed': True}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    async def _async_connect(self, address: str, adapter: str) -> Dict[str, Any]:
        """Async connect to device."""
        self._client = BleakClient(address, adapter=adapter)
        await self._client.connect()
        self._connected_address = address
        logger.info(f"GATT connected to {address}")
        return {
            'status': 'ok',
            'address': address,
            'mtu': self._client.mtu_size if hasattr(self._client, 'mtu_size') else None,
        }

    async def _async_disconnect(self):
        """Async disconnect."""
        if self._client:
            # Stop all notifications first
            for char_uuid in list(self._notification_handlers.keys()):
                try:
                    await self._client.stop_notify(char_uuid)
                except Exception:
                    pass
            self._notification_handlers.clear()

            await self._client.disconnect()
            self._connected_address = None
            self._client = None
            logger.info("GATT disconnected")


# ===========================================================================
#  Classic Bluetooth Discovery
# ===========================================================================

class ClassicBTDiscovery:
    """Classic BR/EDR Bluetooth device discovery and SDP lookup."""

    # Major device class mapping
    MAJOR_DEVICE_CLASSES = {
        0: 'Miscellaneous',
        1: 'Computer',
        2: 'Phone',
        3: 'LAN/Network Access Point',
        4: 'Audio/Video',
        5: 'Peripheral',
        6: 'Imaging',
        7: 'Wearable',
        8: 'Toy',
        9: 'Health',
        31: 'Uncategorized',
    }

    # Minor device class for Computer (major=1)
    MINOR_COMPUTER = {
        0: 'Uncategorized', 1: 'Desktop', 2: 'Server',
        3: 'Laptop', 4: 'Handheld', 5: 'Palm-sized', 6: 'Wearable',
    }

    # Minor device class for Phone (major=2)
    MINOR_PHONE = {
        0: 'Uncategorized', 1: 'Cellular', 2: 'Cordless',
        3: 'Smartphone', 4: 'Wired modem', 5: 'Common ISDN',
    }

    # Minor device class for Audio/Video (major=4)
    MINOR_AV = {
        0: 'Uncategorized', 1: 'Wearable Headset', 2: 'Hands-free',
        4: 'Microphone', 5: 'Loudspeaker', 6: 'Headphones',
        7: 'Portable Audio', 8: 'Car Audio', 9: 'Set-top Box',
        10: 'HiFi Audio', 11: 'VCR', 12: 'Video Camera',
        13: 'Camcorder', 14: 'Video Monitor', 15: 'Video Display and Loudspeaker',
        16: 'Video Conferencing', 18: 'Gaming/Toy',
    }

    @staticmethod
    def parse_device_class(cod: int) -> Dict[str, str]:
        """Parse a Class of Device (CoD) value into human-readable form."""
        major = (cod >> 8) & 0x1F
        minor = (cod >> 2) & 0x3F

        major_str = ClassicBTDiscovery.MAJOR_DEVICE_CLASSES.get(major, f'Reserved ({major})')

        minor_str = 'Unknown'
        if major == 1:
            minor_str = ClassicBTDiscovery.MINOR_COMPUTER.get(minor, f'Unknown ({minor})')
        elif major == 2:
            minor_str = ClassicBTDiscovery.MINOR_PHONE.get(minor, f'Unknown ({minor})')
        elif major == 4:
            minor_str = ClassicBTDiscovery.MINOR_AV.get(minor, f'Unknown ({minor})')

        # Service classes
        services = []
        svc_bits = (cod >> 13) & 0x7FF
        svc_names = [
            (0, 'Limited Discoverable'), (1, 'LE Audio'), (2, 'Reserved'),
            (3, 'Positioning'), (4, 'Networking'), (5, 'Rendering'),
            (6, 'Capturing'), (7, 'Object Transfer'), (8, 'Audio'),
            (9, 'Telephony'), (10, 'Information'),
        ]
        for bit, name in svc_names:
            if svc_bits & (1 << bit):
                services.append(name)

        return {
            'major': major_str,
            'minor': minor_str,
            'services': services,
            'raw': f'0x{cod:06x}',
        }

    @staticmethod
    def discover(duration: int = 8, adapter: str = 'hci0') -> List[Dict[str, Any]]:
        """Run classic BT inquiry scan."""
        devices = []
        try:
            # Use hcitool for inquiry
            result = subprocess.run(
                ['hcitool', '-i', adapter, 'inq', '--length', str(duration)],
                capture_output=True, text=True, timeout=duration + 10
            )

            if result.returncode != 0:
                logger.error(f"Classic BT inquiry failed: {result.stderr}")
                return devices

            for line in result.stdout.splitlines():
                line = line.strip()
                # Format: "AA:BB:CC:DD:EE:FF  clock offset: 0x1234  class: 0x5a020c"
                m = re.match(r'([0-9A-Fa-f:]{17})\s+.*class:\s*(0x[0-9A-Fa-f]+)', line)
                if m:
                    addr = m.group(1)
                    cod = int(m.group(2), 16)

                    # Try to get name
                    name = ClassicBTDiscovery._get_name(addr, adapter)

                    device = {
                        'address': addr,
                        'name': name,
                        'class_of_device': cod,
                        'device_class': ClassicBTDiscovery.parse_device_class(cod),
                        'discovered_at': time.time(),
                    }
                    devices.append(device)

        except subprocess.TimeoutExpired:
            logger.warning("Classic BT inquiry timed out")
        except FileNotFoundError:
            logger.error("hcitool not found")
        except Exception as e:
            logger.error(f"Classic BT discovery error: {e}")

        return devices

    @staticmethod
    def _get_name(address: str, adapter: str = 'hci0') -> str:
        """Attempt to resolve a device name."""
        try:
            result = subprocess.run(
                ['hcitool', '-i', adapter, 'name', address],
                capture_output=True, text=True, timeout=5
            )
            name = result.stdout.strip()
            return name if name else ''
        except Exception:
            return ''

    @staticmethod
    def sdp_lookup(address: str) -> List[Dict[str, Any]]:
        """Perform SDP service discovery on a device."""
        services = []
        try:
            result = subprocess.run(
                ['sdptool', 'browse', address],
                capture_output=True, text=True, timeout=15
            )

            if result.returncode != 0:
                return services

            current_service = None
            for line in result.stdout.splitlines():
                line = line.strip()

                if line.startswith('Service Name:'):
                    if current_service:
                        services.append(current_service)
                    current_service = {
                        'name': line.split(':', 1)[1].strip(),
                        'description': '',
                        'protocol': '',
                        'channel': '',
                        'profiles': [],
                    }
                elif current_service:
                    if line.startswith('Service Description:'):
                        current_service['description'] = line.split(':', 1)[1].strip()
                    elif line.startswith('Protocol Descriptor List:'):
                        pass  # Next lines will have protocol info
                    elif '"RFCOMM"' in line:
                        current_service['protocol'] = 'RFCOMM'
                        m = re.search(r'Channel:\s*(\d+)', line)
                        if m:
                            current_service['channel'] = m.group(1)
                    elif '"L2CAP"' in line:
                        if not current_service['protocol']:
                            current_service['protocol'] = 'L2CAP'
                        m = re.search(r'PSM:\s*(0x[0-9a-fA-F]+)', line)
                        if m:
                            current_service['channel'] = m.group(1)
                    elif line.startswith('Profile Descriptor List:'):
                        pass
                    elif '"' in line and 'Version' in line:
                        current_service['profiles'].append(line.strip(' "'))

            if current_service:
                services.append(current_service)

        except subprocess.TimeoutExpired:
            logger.warning(f"SDP lookup timed out for {address}")
        except FileNotFoundError:
            logger.error("sdptool not found")
        except Exception as e:
            logger.error(f"SDP lookup error: {e}")

        return services


# ===========================================================================
#  HCI Monitor
# ===========================================================================

class HCIMonitor:
    """Lightweight wrapper around btmon for HCI event monitoring."""

    def __init__(self, socketio=None):
        self._socketio = socketio
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._filters: set = set()  # Active filters
        self._buffer: list = []  # Recent events buffer
        self._max_buffer = 500

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, filters: List[str] = None):
        """Start btmon and stream events via SocketIO."""
        if self._running:
            return

        self._filters = set(filters or [])
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_worker,
            daemon=True,
            name='HCIMonitor'
        )
        self._monitor_thread.start()

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    def get_buffer(self) -> List[Dict]:
        """Return recent events from buffer."""
        return list(self._buffer)

    def _monitor_worker(self):
        """Run btmon and parse output."""
        logger.info("Starting HCI monitor (btmon)")
        try:
            self._process = subprocess.Popen(
                ['btmon', '--priority', 'low'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            current_event = None

            for line in iter(self._process.stdout.readline, ''):
                if not self._running:
                    break

                line = line.rstrip()
                if not line:
                    continue

                # Parse btmon output
                event = self._parse_btmon_line(line, current_event)
                if event:
                    current_event = event

                    # Apply filters
                    if self._filters:
                        event_type = event.get('type', '').lower()
                        if not any(f.lower() in event_type for f in self._filters):
                            continue

                    # Buffer
                    self._buffer.append(event)
                    if len(self._buffer) > self._max_buffer:
                        self._buffer.pop(0)

                    # Emit via SocketIO
                    if self._socketio:
                        try:
                            self._socketio.emit('bt_hci_event', event)
                        except Exception:
                            pass

        except FileNotFoundError:
            logger.error("btmon not found — install bluez-utils")
        except Exception as e:
            logger.error(f"HCI monitor error: {e}")
        finally:
            self._running = False
            logger.info("HCI monitor stopped")

    def _parse_btmon_line(self, line: str, current_event: Optional[Dict]) -> Optional[Dict]:
        """Parse a btmon output line into a structured event."""
        # btmon lines starting with > or < indicate new events
        # > = outgoing (host → controller)
        # < = incoming (controller → host)
        # @ = timestamps/meta

        severity = 'info'

        if line.startswith(('>', '<', '@')):
            direction = 'out' if line[0] == '>' else 'in' if line[0] == '<' else 'meta'
            content = line[2:].strip()

            # Detect event type
            event_type = 'unknown'
            if 'HCI Command' in content:
                event_type = 'command'
            elif 'HCI Event' in content:
                event_type = 'event'
            elif 'ACL Data' in content:
                event_type = 'acl_data'
            elif 'SCO Data' in content:
                event_type = 'sco_data'
            elif 'Advertising' in content or 'LE Advertising' in content:
                event_type = 'advertising'
            elif 'Connection' in content:
                event_type = 'connection'
            elif 'Error' in content or 'Failed' in content:
                event_type = 'error'
                severity = 'error'
            elif 'Disconnect' in content:
                event_type = 'disconnection'
                severity = 'warning'

            return {
                'timestamp': time.time(),
                'direction': direction,
                'type': event_type,
                'content': content,
                'severity': severity,
                'raw': line,
            }

        return None


# ===========================================================================
#  Resilience Testing Tools
# ===========================================================================

class ResilienceTests:
    """Adversarial resilience testing tools for Bluetooth.

    All tests have automatic safety timeouts (max 60s default).
    These test our OWN infrastructure's resilience, not attack other devices.
    """

    MAX_TIMEOUT = 60  # Maximum allowed timeout in seconds

    def __init__(self, socketio=None):
        self._socketio = socketio
        self._active_tests: Dict[str, Dict[str, Any]] = {}
        self._stop_events: Dict[str, threading.Event] = {}
        self._test_threads: Dict[str, threading.Thread] = {}

    def get_status(self) -> Dict[str, Any]:
        """Get status of all running tests."""
        status = {}
        for test_id, info in self._active_tests.items():
            status[test_id] = {
                'type': info.get('type', ''),
                'started_at': info.get('started_at', 0),
                'elapsed': time.time() - info.get('started_at', time.time()),
                'iterations': info.get('iterations', 0),
                'config': info.get('config', {}),
            }
        return {'active_tests': status, 'count': len(status)}

    def stop_all(self):
        """Stop all running tests."""
        for evt in self._stop_events.values():
            evt.set()
        # Wait briefly for threads to finish
        for thread in self._test_threads.values():
            thread.join(timeout=3)
        self._active_tests.clear()
        self._stop_events.clear()
        self._test_threads.clear()

    def start_adv_flood(self, config: Dict = None) -> Dict[str, Any]:
        """Start advertising flood test — rapidly cycle random BLE advertisements.

        Tests how nearby scanners handle rapid advertisement changes.
        """
        config = config or {}
        timeout = min(config.get('timeout', 30), self.MAX_TIMEOUT)
        interval_ms = max(config.get('interval_ms', 100), 20)  # Min 20ms
        adapter = config.get('adapter', 'hci0')

        test_id = f'adv_flood_{int(time.time())}'
        return self._start_test(test_id, 'adv_flood', {
            'timeout': timeout,
            'interval_ms': interval_ms,
            'adapter': adapter,
        }, self._adv_flood_worker)

    def start_name_rotation(self, config: Dict = None) -> Dict[str, Any]:
        """Start name rotation test — rapidly change device name.

        Tests discovery stability when device names change frequently.
        """
        config = config or {}
        timeout = min(config.get('timeout', 30), self.MAX_TIMEOUT)
        interval_ms = max(config.get('interval_ms', 200), 50)
        adapter = config.get('adapter', 'hci0')
        names = config.get('names', [
            'Test_Alpha', 'Test_Bravo', 'Test_Charlie', 'Test_Delta',
            'Test_Echo', 'Test_Foxtrot', 'Test_Golf', 'Test_Hotel',
        ])

        test_id = f'name_rotate_{int(time.time())}'
        return self._start_test(test_id, 'name_rotation', {
            'timeout': timeout,
            'interval_ms': interval_ms,
            'adapter': adapter,
            'names': names,
        }, self._name_rotation_worker)

    def start_connection_stress(self, config: Dict = None) -> Dict[str, Any]:
        """Start connection stress test — rapid connect/disconnect cycles.

        Tests how a target device handles rapid connection attempts.
        Target should be our own test device.
        """
        if not _bleak_available:
            return {'status': 'error', 'message': 'bleak not installed'}

        config = config or {}
        target = config.get('target')
        if not target:
            return {'status': 'error', 'message': 'target address required'}

        timeout = min(config.get('timeout', 30), self.MAX_TIMEOUT)
        adapter = config.get('adapter', 'hci0')

        test_id = f'conn_stress_{int(time.time())}'
        return self._start_test(test_id, 'connection_stress', {
            'timeout': timeout,
            'adapter': adapter,
            'target': target,
        }, self._connection_stress_worker)

    def get_channel_assessment(self, adapter: str = 'hci0') -> Dict[str, Any]:
        """Read channel map / assessment from adapter.

        Shows local RF environment quality per BLE channel.
        """
        try:
            hci_index = adapter.replace('hci', '')

            # Read AFH channel map via hcitool
            result = subprocess.run(
                ['hcitool', '-i', adapter, 'cmd', '0x05', '0x0006'],
                capture_output=True, text=True, timeout=5
            )

            # Also try LE channel map
            le_result = subprocess.run(
                ['hcitool', '-i', adapter, 'cmd', '0x08', '0x0015'],
                capture_output=True, text=True, timeout=5
            )

            # Parse btmgmt info for additional channel data
            info_result = subprocess.run(
                ['btmgmt', '--index', hci_index, 'info'],
                capture_output=True, text=True, timeout=5
            )

            return {
                'status': 'ok',
                'adapter': adapter,
                'afh_response': result.stdout.strip() if result.returncode == 0 else result.stderr.strip(),
                'le_channel_response': le_result.stdout.strip() if le_result.returncode == 0 else le_result.stderr.strip(),
                'adapter_info': info_result.stdout.strip() if info_result.returncode == 0 else '',
                'timestamp': time.time(),
            }
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _start_test(self, test_id: str, test_type: str, config: Dict,
                    worker_fn: Callable) -> Dict[str, Any]:
        """Generic test starter."""
        stop_event = threading.Event()
        self._stop_events[test_id] = stop_event

        info = {
            'type': test_type,
            'started_at': time.time(),
            'iterations': 0,
            'config': config,
        }
        self._active_tests[test_id] = info

        thread = threading.Thread(
            target=self._test_wrapper,
            args=(test_id, worker_fn, config, stop_event, info),
            daemon=True,
            name=f'BT-Test-{test_id}'
        )
        self._test_threads[test_id] = thread
        thread.start()

        return {
            'status': 'ok',
            'test_id': test_id,
            'type': test_type,
            'config': config,
        }

    def _test_wrapper(self, test_id: str, worker_fn: Callable,
                      config: Dict, stop_event: threading.Event, info: Dict):
        """Wrapper with safety timeout and cleanup."""
        timeout = config.get('timeout', 30)

        # Safety timeout timer
        timer = threading.Timer(timeout, stop_event.set)
        timer.start()

        try:
            worker_fn(config, stop_event, info)
        except Exception as e:
            logger.error(f"Test {test_id} error: {e}")
        finally:
            timer.cancel()
            stop_event.set()
            self._active_tests.pop(test_id, None)
            self._stop_events.pop(test_id, None)
            self._test_threads.pop(test_id, None)

            if self._socketio:
                self._socketio.emit('bt_test_status', {
                    'test_id': test_id,
                    'type': info.get('type'),
                    'status': 'completed',
                    'iterations': info.get('iterations', 0),
                    'elapsed': time.time() - info.get('started_at', time.time()),
                })
            logger.info(f"Test {test_id} completed: {info.get('iterations', 0)} iterations")

    def _adv_flood_worker(self, config: Dict, stop_event: threading.Event, info: Dict):
        """Advertising flood test worker."""
        adapter = config['adapter']
        interval_s = config['interval_ms'] / 1000.0
        hci_index = adapter.replace('hci', '')
        iteration = 0

        # Ensure adapter is powered on
        subprocess.run(
            ['btmgmt', '--index', hci_index, 'power', 'on'],
            capture_output=True, timeout=5
        )

        while not stop_event.is_set():
            iteration += 1
            info['iterations'] = iteration

            # Generate random advertising name and data
            rand_name = f'FLOOD_{iteration:04d}_{os.urandom(2).hex()}'

            try:
                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'name', rand_name],
                    capture_output=True, timeout=2
                )

                # Toggle discoverable rapidly
                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'discov', 'on'],
                    capture_output=True, timeout=2
                )
                time.sleep(interval_s / 2)
                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'discov', 'off'],
                    capture_output=True, timeout=2
                )
            except Exception:
                pass

            # Emit progress
            if self._socketio and iteration % 10 == 0:
                self._socketio.emit('bt_test_status', {
                    'test_id': None,
                    'type': 'adv_flood',
                    'status': 'running',
                    'iterations': iteration,
                    'elapsed': time.time() - info['started_at'],
                })

            stop_event.wait(interval_s / 2)

        # Cleanup
        subprocess.run(
            ['btmgmt', '--index', hci_index, 'discov', 'off'],
            capture_output=True, timeout=5
        )

    def _name_rotation_worker(self, config: Dict, stop_event: threading.Event, info: Dict):
        """Name rotation test worker."""
        adapter = config['adapter']
        interval_s = config['interval_ms'] / 1000.0
        names = config.get('names', ['Test_A', 'Test_B'])
        hci_index = adapter.replace('hci', '')
        iteration = 0

        # Save original name
        orig_name = ''
        try:
            r = subprocess.run(['btmgmt', '--index', hci_index, 'info'],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if line.strip().startswith('name'):
                    orig_name = line.strip().split(None, 1)[1] if len(line.strip().split(None, 1)) > 1 else ''
                    break
        except Exception:
            pass

        while not stop_event.is_set():
            iteration += 1
            info['iterations'] = iteration

            name = names[iteration % len(names)]
            try:
                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'name', name],
                    capture_output=True, timeout=2
                )
            except Exception:
                pass

            if self._socketio and iteration % 5 == 0:
                self._socketio.emit('bt_test_status', {
                    'test_id': None,
                    'type': 'name_rotation',
                    'status': 'running',
                    'iterations': iteration,
                    'current_name': name,
                    'elapsed': time.time() - info['started_at'],
                })

            stop_event.wait(interval_s)

        # Restore original name
        if orig_name:
            try:
                subprocess.run(
                    ['btmgmt', '--index', hci_index, 'name', orig_name],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass

    def _connection_stress_worker(self, config: Dict, stop_event: threading.Event, info: Dict):
        """Connection stress test worker — rapid connect/disconnect."""
        target = config['target']
        adapter = config['adapter']
        iteration = 0

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def stress_cycle():
            nonlocal iteration
            while not stop_event.is_set():
                iteration += 1
                info['iterations'] = iteration
                success = False
                error_msg = ''

                try:
                    client = BleakClient(target, adapter=adapter)
                    await asyncio.wait_for(client.connect(), timeout=5)
                    success = client.is_connected
                    await client.disconnect()
                except asyncio.TimeoutError:
                    error_msg = 'timeout'
                except Exception as e:
                    error_msg = str(e)

                if self._socketio and iteration % 5 == 0:
                    self._socketio.emit('bt_test_status', {
                        'test_id': None,
                        'type': 'connection_stress',
                        'status': 'running',
                        'iterations': iteration,
                        'last_success': success,
                        'last_error': error_msg,
                        'target': target,
                        'elapsed': time.time() - info['started_at'],
                    })

                await asyncio.sleep(0.5)

        try:
            loop.run_until_complete(stress_cycle())
        except Exception as e:
            logger.error(f"Connection stress error: {e}")
        finally:
            loop.close()


# ===========================================================================
#  BT Toolkit — Main Coordinator
# ===========================================================================

class BTToolkit:
    """Main coordinator for all Bluetooth toolkit functionality.

    Initialized once and passed the Flask-SocketIO instance.
    """

    def __init__(self, socketio=None):
        self.socketio = socketio
        self.adapter_manager = AdapterManager()
        self.ble_scanner = BLEScanner(socketio=socketio)
        self.ble_advertiser = BLEAdvertiser(socketio=socketio)
        self.gatt_explorer = GATTExplorer(socketio=socketio)
        self.classic_discovery = ClassicBTDiscovery()
        self.hci_monitor = HCIMonitor(socketio=socketio)
        self.resilience_tests = ResilienceTests(socketio=socketio)

        logger.info("BT Toolkit initialized (bleak=%s, dbus-next=%s)",
                     _bleak_available, _dbus_next_available)

    def get_capabilities(self) -> Dict[str, bool]:
        """Return what features are available based on installed deps."""
        return {
            'bleak': _bleak_available,
            'dbus_next': _dbus_next_available,
            'ble_scan': _bleak_available,
            'ble_advertise': True,  # Uses btmgmt fallback
            'gatt_explorer': _bleak_available,
            'classic_bt': True,  # Uses hcitool
            'hci_monitor': True,  # Uses btmon
            'resilience_tests': True,
        }
