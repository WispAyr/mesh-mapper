/* ============================================
   Audio Alert System
   ============================================ */

window.MeshAudio = (function() {
    'use strict';

    let enabled = true;
    let audioCtx = null;

    function getContext() {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        return audioCtx;
    }

    function isEnabled() { return enabled; }

    function toggle() {
        enabled = !enabled;
        updateUI();
        return enabled;
    }

    function setEnabled(val) {
        enabled = !!val;
        updateUI();
    }

    function updateUI() {
        var onIcon = document.getElementById('audio-on-icon');
        var offIcon = document.getElementById('audio-off-icon');
        var btn = document.getElementById('btn-audio-toggle');
        if (onIcon && offIcon) {
            onIcon.style.display = enabled ? '' : 'none';
            offIcon.style.display = enabled ? 'none' : '';
        }
        if (btn) {
            btn.classList.toggle('active', enabled);
        }
    }

    // Generate different alert sounds
    function playTone(frequency, duration, type, volume) {
        if (!enabled) return;
        try {
            var ctx = getContext();
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();

            osc.type = type || 'sine';
            osc.frequency.value = frequency || 440;
            gain.gain.value = volume || 0.15;

            osc.connect(gain);
            gain.connect(ctx.destination);

            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (duration || 0.3));

            osc.start();
            osc.stop(ctx.currentTime + (duration || 0.3));
        } catch (e) {
            // Audio context not available
        }
    }

    function droneAlert() {
        // Urgent double-beep
        playTone(880, 0.15, 'square', 0.12);
        setTimeout(function() { playTone(1100, 0.15, 'square', 0.12); }, 200);
    }

    function zoneAlert() {
        // Warning triple-beep
        playTone(660, 0.1, 'sawtooth', 0.1);
        setTimeout(function() { playTone(660, 0.1, 'sawtooth', 0.1); }, 150);
        setTimeout(function() { playTone(880, 0.2, 'sawtooth', 0.1); }, 300);
    }

    function lightningAlert() {
        // Thunder rumble
        playTone(120, 0.5, 'sawtooth', 0.08);
    }

    function connectionAlert() {
        playTone(440, 0.1, 'sine', 0.08);
        setTimeout(function() { playTone(660, 0.15, 'sine', 0.08); }, 120);
    }

    function disconnectAlert() {
        playTone(440, 0.15, 'sine', 0.08);
        setTimeout(function() { playTone(330, 0.2, 'sine', 0.08); }, 180);
    }

    return {
        isEnabled: isEnabled,
        toggle: toggle,
        setEnabled: setEnabled,
        droneAlert: droneAlert,
        zoneAlert: zoneAlert,
        lightningAlert: lightningAlert,
        connectionAlert: connectionAlert,
        disconnectAlert: disconnectAlert,
        playTone: playTone
    };
})();
