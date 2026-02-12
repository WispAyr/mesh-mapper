"""
GPS Reader Module for Mesh-Mapper
=================================
Reads NMEA sentences from a serial GPS receiver (e.g. u-blox 7) and provides
a continuously-updated position fix.

No external dependencies beyond pyserial – parses NMEA directly.

Author: Mesh-Mapper GPS Integration
"""

import time
import logging
import threading
import serial

logger = logging.getLogger(__name__)


def _nmea_checksum(sentence):
    """Verify NMEA checksum. sentence should start with $ and contain *XX."""
    try:
        if not sentence.startswith('$'):
            return False
        star = sentence.rfind('*')
        if star < 0 or star + 3 > len(sentence):
            return False
        body = sentence[1:star]
        expected = int(sentence[star + 1:star + 3], 16)
        calc = 0
        for ch in body:
            calc ^= ord(ch)
        return calc == expected
    except (ValueError, IndexError):
        return False


def _dm_to_dd(dm_str, direction):
    """Convert NMEA DDMM.MMMM / DDDMM.MMMM to decimal degrees."""
    if not dm_str:
        return 0.0
    try:
        # Find the decimal point to determine where degrees end
        dot = dm_str.index('.')
        deg_len = dot - 2  # degrees are everything before the last 2 digits before dot
        degrees = int(dm_str[:deg_len])
        minutes = float(dm_str[deg_len:])
        dd = degrees + minutes / 60.0
        if direction in ('S', 'W'):
            dd = -dd
        return dd
    except (ValueError, IndexError):
        return 0.0


def _safe_float(s, default=0.0):
    try:
        return float(s) if s else default
    except ValueError:
        return default


def _safe_int(s, default=0):
    try:
        return int(s) if s else default
    except ValueError:
        return default


class GPSReader:
    """Reads NMEA from a serial port and maintains a live GPS fix."""

    def __init__(self, serial_port='/dev/ttyACM2', baud_rate=9600, callback=None):
        """
        Args:
            serial_port: Path to the GPS serial device.
            baud_rate:   NMEA baud rate (typically 9600 for u-blox 7).
            callback:    Optional callback(gps_data_dict) on each valid update.
        """
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.callback = callback

        self._lock = threading.Lock()
        self._position = {
            'lat': 0.0,
            'lon': 0.0,
            'alt': 0.0,
            'speed': 0.0,          # knots
            'speed_kmh': 0.0,
            'heading': 0.0,
            'fix': False,
            'fix_quality': 0,      # 0=invalid, 1=GPS, 2=DGPS, ...
            'satellites': 0,
            'hdop': 99.99,
            'timestamp': 0,        # epoch seconds of last update
            'utc_time': '',        # HHMMSS.ss from NMEA
            'utc_date': '',        # DDMMYY from NMEA
        }

        self._running = False
        self._thread = None
        self._sentence_count = 0

    def start(self):
        """Start reading GPS in a background thread."""
        if self._running:
            logger.warning("GPS reader already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name='GPSReader')
        self._thread.start()
        logger.info("GPS reader started on %s @ %d baud", self.serial_port, self.baud_rate)

    def stop(self):
        """Stop the GPS reader."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("GPS reader stopped")

    def get_position(self):
        """Return a copy of the current position dict."""
        with self._lock:
            return dict(self._position)

    @property
    def has_fix(self):
        with self._lock:
            return self._position['fix']

    @property
    def lat(self):
        with self._lock:
            return self._position['lat']

    @property
    def lon(self):
        with self._lock:
            return self._position['lon']

    # ---- internal ----

    def _read_loop(self):
        retry_delay = 2
        while self._running:
            try:
                logger.info("Opening GPS serial port %s", self.serial_port)
                ser = serial.Serial(self.serial_port, self.baud_rate, timeout=2)
                logger.info("GPS serial port open")
                retry_delay = 2

                while self._running:
                    try:
                        raw = ser.readline()
                        if not raw:
                            continue
                        line = raw.decode('ascii', errors='ignore').strip()
                        if not line.startswith('$'):
                            continue
                        self._parse_sentence(line)
                    except serial.SerialException as e:
                        logger.warning("GPS serial read error: %s", e)
                        break
                    except Exception as e:
                        logger.debug("GPS parse error: %s", e)
                        continue

            except serial.SerialException as e:
                if not self._running:
                    break
                logger.error("GPS serial open error: %s – retrying in %ds", e, retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
            except Exception as e:
                if not self._running:
                    break
                logger.error("GPS reader error: %s – retrying in %ds", e, retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
            finally:
                try:
                    ser.close()
                except Exception:
                    pass

    def _parse_sentence(self, sentence):
        """Dispatch an NMEA sentence to the appropriate parser."""
        # Optional checksum validation – skip malformed but don't crash
        if '*' in sentence and not _nmea_checksum(sentence):
            return

        # Strip checksum for field parsing
        star = sentence.find('*')
        if star > 0:
            sentence = sentence[:star]

        fields = sentence.split(',')
        msg_type = fields[0]

        if msg_type in ('$GPRMC', '$GNRMC'):
            self._parse_rmc(fields)
        elif msg_type in ('$GPGGA', '$GNGGA'):
            self._parse_gga(fields)
        elif msg_type in ('$GPGSA', '$GNGSA'):
            self._parse_gsa(fields)

    def _parse_rmc(self, fields):
        """Parse $GPRMC — Recommended Minimum sentence.
        $GPRMC,HHMMSS.ss,A/V,DDMM.MMMM,N/S,DDDMM.MMMM,E/W,speed,heading,DDMMYY,...
        """
        if len(fields) < 10:
            return

        status = fields[2]  # A=active, V=void
        is_valid = (status == 'A')

        utc_time = fields[1]
        lat = _dm_to_dd(fields[3], fields[4]) if is_valid else 0.0
        lon = _dm_to_dd(fields[5], fields[6]) if is_valid else 0.0
        speed_knots = _safe_float(fields[7])
        heading = _safe_float(fields[8])
        utc_date = fields[9] if len(fields) > 9 else ''

        with self._lock:
            if is_valid:
                self._position['lat'] = lat
                self._position['lon'] = lon
                self._position['speed'] = speed_knots
                self._position['speed_kmh'] = speed_knots * 1.852
                self._position['heading'] = heading
                self._position['fix'] = True
            else:
                self._position['fix'] = False
            self._position['utc_time'] = utc_time
            self._position['utc_date'] = utc_date
            self._position['timestamp'] = time.time()
            pos_copy = dict(self._position)

        self._sentence_count += 1
        if self.callback and is_valid:
            try:
                self.callback(pos_copy)
            except Exception as e:
                logger.debug("GPS callback error: %s", e)

    def _parse_gga(self, fields):
        """Parse $GPGGA — Global Positioning System Fix Data.
        $GPGGA,HHMMSS.ss,DDMM.MMMM,N/S,DDDMM.MMMM,E/W,fix,sats,hdop,alt,M,...
        """
        if len(fields) < 10:
            return

        fix_quality = _safe_int(fields[6])
        satellites = _safe_int(fields[7])
        hdop = _safe_float(fields[8], 99.99)
        alt = _safe_float(fields[9])  # metres above MSL

        is_valid = fix_quality > 0

        with self._lock:
            self._position['fix_quality'] = fix_quality
            self._position['satellites'] = satellites
            self._position['hdop'] = hdop
            if is_valid:
                # GGA also has position – update if valid
                lat = _dm_to_dd(fields[2], fields[3])
                lon = _dm_to_dd(fields[4], fields[5])
                if lat != 0 or lon != 0:
                    self._position['lat'] = lat
                    self._position['lon'] = lon
                self._position['alt'] = alt
                self._position['fix'] = True
            self._position['timestamp'] = time.time()

    def _parse_gsa(self, fields):
        """Parse $GPGSA — DOP and active satellites.
        We mainly care about PDOP/HDOP/VDOP here for quality info.
        """
        if len(fields) < 17:
            return
        # fields[2] = fix type: 1=no fix, 2=2D, 3=3D
        # fields[15]=PDOP, fields[16]=HDOP, fields[17]=VDOP
        # We already get HDOP from GGA, but this is a backup
        pass


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')

    import argparse
    parser = argparse.ArgumentParser(description='GPS Reader standalone test')
    parser.add_argument('-s', '--serial', default='/dev/ttyACM2', help='Serial port')
    parser.add_argument('-b', '--baud', default=9600, type=int, help='Baud rate')
    parser.add_argument('-t', '--time', default=30, type=int, help='Run duration (seconds)')
    args = parser.parse_args()

    update_count = [0]

    def on_update(pos):
        update_count[0] += 1
        fix_str = "FIX" if pos['fix'] else "NO FIX"
        print(f"[{update_count[0]}] {fix_str}  lat={pos['lat']:.6f}  lon={pos['lon']:.6f}  "
              f"alt={pos['alt']:.1f}m  speed={pos['speed_kmh']:.1f}km/h  "
              f"sats={pos['satellites']}  hdop={pos['hdop']:.1f}  "
              f"heading={pos['heading']:.0f}°")

    gps = GPSReader(serial_port=args.serial, baud_rate=args.baud, callback=on_update)
    gps.start()

    try:
        time.sleep(args.time)
    except KeyboardInterrupt:
        pass
    finally:
        gps.stop()

    pos = gps.get_position()
    print(f"\nFinal position: {pos}")
