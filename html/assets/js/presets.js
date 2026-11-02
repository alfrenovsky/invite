/**
 * Global Presets Configuration for Boxes & RSVP Cards
 * Single source of truth for visual box presets and RSVP form styling.
 */
const BOX_PRESETS_CONFIG = {
    'none': {
        name: 'Sin fondo',
        bg_color: '',
        bg_opacity: 0,
        bg_blur: 0,
        box_radius: 12
    },
    'glass-gold': {
        name: 'Dorado',
        bg_color: '#9f8329',
        bg_opacity: 25,
        bg_blur: 10,
        box_radius: 14,
        dark_color: '#75611e',
        dark_opacity: 50
    },
    'glass-light': {
        name: 'Vidrio Claro',
        bg_color: '#ffffff',
        bg_opacity: 20,
        bg_blur: 12,
        box_radius: 16,
        dark_color: '#e6ebf5',
        dark_opacity: 35
    },
    'glass-dark': {
        name: 'Vidrio Oscuro',
        bg_color: '#000000',
        bg_opacity: 45,
        bg_blur: 14,
        box_radius: 16,
        dark_color: '#0a0f19',
        dark_opacity: 65
    },
    'white': {
        name: 'Blanco',
        bg_color: '#ffffff',
        bg_opacity: 100,
        bg_blur: 0,
        box_radius: 16
    },
    'black': {
        name: 'Negro',
        bg_color: '#000000',
        bg_opacity: 100,
        bg_blur: 0,
        box_radius: 16
    }
};

/**
 * Converts a hex color string (#rrggbb or #rgb) to an "r, g, b" triplet string.
 * @param {string} hex
 * @returns {string} e.g. "212, 175, 55"
 */
function hexToRgb(hex) {
    if (!hex || typeof hex !== 'string') return '';
    let clean = hex.replace('#', '').trim();
    if (clean.length === 3) {
        clean = clean.split('').map(c => c + c).join('');
    }
    if (clean.length !== 6) return '';
    const num = parseInt(clean, 16);
    if (isNaN(num)) return '';
    const r = (num >> 16) & 255;
    const g = (num >> 8) & 255;
    const b = num & 255;
    return `${r}, ${g}, ${b}`;
}

/**
 * Applies the given box preset to the document root as CSS variables for RSVP cards.
 * @param {string} presetName Key of the preset in BOX_PRESETS_CONFIG (defaults to 'glass-gold')
 */
function applyRsvpBoxPreset(presetName = 'glass-gold') {
    const cfg = BOX_PRESETS_CONFIG[presetName] || BOX_PRESETS_CONFIG['glass-gold'];
    if (!cfg || !document.documentElement) return;

    const root = document.documentElement;
    const bgRgb = hexToRgb(cfg.bg_color) || '212, 175, 55';
    const bgAlpha = (cfg.bg_opacity !== undefined ? cfg.bg_opacity : 25) / 100;
    const blurPx = cfg.bg_blur !== undefined ? cfg.bg_blur : 10;
    const radiusPx = cfg.box_radius || 14;

    const darkRgb = hexToRgb(cfg.dark_color) || hexToRgb(cfg.bg_color) || '145, 105, 18';
    const darkAlpha = (cfg.dark_opacity !== undefined ? cfg.dark_opacity : 50) / 100;

    root.style.setProperty('--rsvp-card-bg', `rgba(${bgRgb}, ${bgAlpha})`);
    root.style.setProperty('--rsvp-card-border', `1px solid rgba(${bgRgb}, 0.35)`);
    root.style.setProperty('--rsvp-card-border-color', `rgba(${bgRgb}, 0.35)`);
    root.style.setProperty('--rsvp-card-blur', `${blurPx}px`);
    root.style.setProperty('--rsvp-card-radius', `${radiusPx}px`);

    root.style.setProperty('--rsvp-card-expanded-bg', `rgba(${darkRgb}, ${darkAlpha})`);
    root.style.setProperty('--rsvp-card-expanded-border', `1px solid rgba(${bgRgb}, 0.65)`);
    root.style.setProperty('--rsvp-card-expanded-border-color', `rgba(${bgRgb}, 0.65)`);
}

// Auto-apply default 'glass-gold' preset as soon as DOM or script loads
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => applyRsvpBoxPreset('glass-gold'));
    } else {
        applyRsvpBoxPreset('glass-gold');
    }
}
