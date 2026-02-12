/* ============================================
   SVG Icon Library for Map Markers
   ============================================ */

window.MeshIcons = (function() {
    'use strict';

    function drone(color) {
        color = color || '#ff4444';
        return '<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">' +
            '<g fill="none" stroke="' + color + '" stroke-width="1.5">' +
            // Body
            '<rect x="12" y="12" width="8" height="8" rx="2" fill="' + color + '" opacity="0.3"/>' +
            // Arms
            '<line x1="14" y1="14" x2="6" y2="6"/>' +
            '<line x1="18" y1="14" x2="26" y2="6"/>' +
            '<line x1="14" y1="18" x2="6" y2="26"/>' +
            '<line x1="18" y1="18" x2="26" y2="26"/>' +
            // Rotors
            '<circle cx="6" cy="6" r="4" fill="' + color + '" opacity="0.2"/>' +
            '<circle cx="26" cy="6" r="4" fill="' + color + '" opacity="0.2"/>' +
            '<circle cx="6" cy="26" r="4" fill="' + color + '" opacity="0.2"/>' +
            '<circle cx="26" cy="26" r="4" fill="' + color + '" opacity="0.2"/>' +
            // Center dot
            '<circle cx="16" cy="16" r="2" fill="' + color + '"/>' +
            '</g></svg>';
    }

    function pilot(color) {
        color = color || '#ff8800';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<circle cx="10" cy="10" r="6" fill="' + color + '" opacity="0.3" stroke="' + color + '" stroke-width="1.5"/>' +
            '<circle cx="10" cy="8" r="2.5" fill="' + color + '"/>' +
            '<path d="M5 16c0-3 2-5 5-5s5 2 5 5" fill="' + color + '" opacity="0.5"/>' +
            '</svg>';
    }

    function aircraft(color, heading) {
        color = color || '#00d4ff';
        heading = heading || 0;
        // When heading is 0 or not provided, return unrotated SVG (for MapLibre icon-rotate)
        var rotateAttr = heading ? ' style="transform:rotate(' + heading + 'deg)"' : '';
        return '<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"' + rotateAttr + '>' +
            '<path d="M12 2L8 10H3l2 3-2 3h5l4 6 4-6h5l-2-3 2-3h-5L12 2z" ' +
            'fill="' + color + '" opacity="0.8" stroke="' + color + '" stroke-width="0.5"/>' +
            '</svg>';
    }

    function vessel(color, heading) {
        color = color || '#4a90d9';
        heading = heading || 0;
        // When heading is 0 or not provided, return unrotated SVG (for MapLibre icon-rotate)
        var rotateAttr = heading ? ' style="transform:rotate(' + heading + 'deg)"' : '';
        return '<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"' + rotateAttr + '>' +
            '<path d="M12 3L7 12H4l1 4c0 0 2 3 7 3s7-3 7-3l1-4h-3L12 3z" ' +
            'fill="' + color + '" opacity="0.7" stroke="' + color + '" stroke-width="0.5"/>' +
            '</svg>';
    }

    function aprs(color) {
        color = color || '#ffd700';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<circle cx="10" cy="10" r="3" fill="' + color + '"/>' +
            '<path d="M10 3a7 7 0 0 1 7 7" fill="none" stroke="' + color + '" stroke-width="1.5" opacity="0.6"/>' +
            '<path d="M10 5.5a4.5 4.5 0 0 1 4.5 4.5" fill="none" stroke="' + color + '" stroke-width="1.5" opacity="0.4"/>' +
            '</svg>';
    }

    function lightning(color) {
        color = color || '#ffee00';
        return '<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M10 1L4 10h4l-2 7 8-10h-4l2-6z" fill="' + color + '" opacity="0.9"/>' +
            '</svg>';
    }

    function webcam(color) {
        color = color || '#66bb6a';
        return '<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">' +
            '<rect x="2" y="4" width="10" height="8" rx="1" fill="' + color + '" opacity="0.7"/>' +
            '<path d="M12 6l4-2v8l-4-2V6z" fill="' + color + '" opacity="0.5"/>' +
            '</svg>';
    }

    function warning(color) {
        color = color || '#ff9900';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M10 2L1 18h18L10 2z" fill="' + color + '" opacity="0.2" stroke="' + color + '" stroke-width="1.5"/>' +
            '<text x="10" y="15" text-anchor="middle" fill="' + color + '" font-size="10" font-weight="bold">!</text>' +
            '</svg>';
    }

    // BLE device category icons
    function blePhone(color) {
        color = color || '#00aaff';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<rect x="6" y="2" width="8" height="16" rx="2" fill="' + color + '" opacity="0.8"/>' +
            '<rect x="8" y="4" width="4" height="9" rx="0.5" fill="#0a0e1a" opacity="0.5"/>' +
            '<circle cx="10" cy="15.5" r="1" fill="#0a0e1a"/>' +
            '</svg>';
    }

    function bleTracker(color) {
        color = color || '#ff9900';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<circle cx="10" cy="10" r="7" fill="' + color + '" opacity="0.3" stroke="' + color + '" stroke-width="1.5"/>' +
            '<circle cx="10" cy="10" r="3" fill="' + color + '" opacity="0.7"/>' +
            '<line x1="10" y1="2" x2="10" y2="5" stroke="' + color + '" stroke-width="1.5"/>' +
            '<line x1="10" y1="15" x2="10" y2="18" stroke="' + color + '" stroke-width="1.5"/>' +
            '<line x1="2" y1="10" x2="5" y2="10" stroke="' + color + '" stroke-width="1.5"/>' +
            '<line x1="15" y1="10" x2="18" y2="10" stroke="' + color + '" stroke-width="1.5"/>' +
            '</svg>';
    }

    function bleBeacon(color) {
        color = color || '#aa66ff';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<circle cx="10" cy="12" r="3" fill="' + color + '"/>' +
            '<path d="M5 8a7 7 0 0 1 10 0" fill="none" stroke="' + color + '" stroke-width="1.5" opacity="0.7"/>' +
            '<path d="M3 5.5a10 10 0 0 1 14 0" fill="none" stroke="' + color + '" stroke-width="1.5" opacity="0.4"/>' +
            '<line x1="10" y1="15" x2="10" y2="18" stroke="' + color + '" stroke-width="2"/>' +
            '</svg>';
    }

    function bleWearable(color) {
        color = color || '#ff66aa';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<rect x="6" y="5" width="8" height="10" rx="3" fill="' + color + '" opacity="0.7"/>' +
            '<rect x="8" y="7" width="4" height="5" rx="1" fill="#0a0e1a" opacity="0.4"/>' +
            '<path d="M9 5V2h2v3" fill="none" stroke="' + color + '" stroke-width="1.5"/>' +
            '<path d="M9 15v3h2v-3" fill="none" stroke="' + color + '" stroke-width="1.5"/>' +
            '</svg>';
    }

    function bleAudio(color) {
        color = color || '#66ddff';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M4 8v4h3l4 3V5L7 8H4z" fill="' + color + '" opacity="0.8"/>' +
            '<path d="M13 7c1 1 1 5 0 6" fill="none" stroke="' + color + '" stroke-width="1.5" opacity="0.7"/>' +
            '<path d="M15 5c2 2 2 8 0 10" fill="none" stroke="' + color + '" stroke-width="1.5" opacity="0.4"/>' +
            '</svg>';
    }

    function bleVehicle(color) {
        color = color || '#22cc44';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M3 12l2-5h10l2 5v3H3v-3z" fill="' + color + '" opacity="0.7"/>' +
            '<rect x="4" y="9" width="12" height="4" rx="1" fill="' + color + '" opacity="0.3"/>' +
            '<circle cx="6" cy="15" r="1.5" fill="' + color + '"/>' +
            '<circle cx="14" cy="15" r="1.5" fill="' + color + '"/>' +
            '</svg>';
    }

    function bleUnknown(color) {
        color = color || '#888888';
        return '<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">' +
            '<circle cx="10" cy="10" r="7" fill="' + color + '" opacity="0.3" stroke="' + color + '" stroke-width="1.5"/>' +
            '<circle cx="10" cy="10" r="2.5" fill="' + color + '" opacity="0.6"/>' +
            '</svg>';
    }

    return {
        drone: drone,
        pilot: pilot,
        aircraft: aircraft,
        vessel: vessel,
        aprs: aprs,
        lightning: lightning,
        webcam: webcam,
        warning: warning,
        blePhone: blePhone,
        bleTracker: bleTracker,
        bleBeacon: bleBeacon,
        bleWearable: bleWearable,
        bleAudio: bleAudio,
        bleVehicle: bleVehicle,
        bleUnknown: bleUnknown
    };
})();
