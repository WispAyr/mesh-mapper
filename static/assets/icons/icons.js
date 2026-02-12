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
        return '<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" ' +
            'style="transform:rotate(' + heading + 'deg)">' +
            '<path d="M12 2L8 10H3l2 3-2 3h5l4 6 4-6h5l-2-3 2-3h-5L12 2z" ' +
            'fill="' + color + '" opacity="0.8" stroke="' + color + '" stroke-width="0.5"/>' +
            '</svg>';
    }

    function vessel(color, heading) {
        color = color || '#4a90d9';
        heading = heading || 0;
        return '<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" ' +
            'style="transform:rotate(' + heading + 'deg)">' +
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

    return {
        drone: drone,
        pilot: pilot,
        aircraft: aircraft,
        vessel: vessel,
        aprs: aprs,
        lightning: lightning,
        webcam: webcam,
        warning: warning
    };
})();
