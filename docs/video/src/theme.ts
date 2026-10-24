import {loadFont} from '@remotion/fonts';
import {Easing, interpolate, staticFile} from 'remotion';

// Colour tokens of the app (theme_tokens.py), light and dark.
export const LIGHT = {
  bg: '#EEF0EE',
  surface: '#FFFFFF',
  surfaceAlt: '#F6F7F6',
  text: '#18211B',
  text2: '#48524B',
  muted: '#5D6760',
  border: '#D5DAD5',
  olive: '#2B4A33',
  oliveText: '#2B4A33',
  oliveSoft: '#E3EBE5',
  oliveMuted: '#3D6648',
  gold: '#7A5D0F',
  goldSoft: '#F4EBCF',
  red: '#B3263E',
  success: '#1F7A3D',
  // The document is paper in both themes.
  paper: '#FFFFFF',
  paperEdge: '#D5DAD5',
  ink: '#1A1A1A',
  // Highlighter on the paper (light in both themes).
  paperMark: '#F4EBCF',
  paperMarkEdge: '#7A5D0F',
};

export type Palette = typeof LIGHT;

export const DARK: Palette = {
  bg: '#121513',
  surface: '#191D1A',
  surfaceAlt: '#1F2420',
  text: '#E6E9E6',
  text2: '#B2BAB3',
  muted: '#929B94',
  border: '#2B312C',
  olive: '#3F6A4B',
  oliveText: '#8CB497',
  oliveSoft: '#1F2B22',
  oliveMuted: '#8CB497',
  gold: '#E2C46A',
  goldSoft: '#352E17',
  red: '#F28B8B',
  success: '#6FC48E',
  paper: '#F4F5F3',
  paperEdge: '#2B312C',
  ink: '#1A1A1A',
  paperMark: '#F4EBCF',
  paperMarkEdge: '#7A5D0F',
};

export const SANS = '"Be Vietnam Pro", "Segoe UI", sans-serif';
// Nghị định 30/2020 prescribes Times New Roman for administrative documents.
export const SERIF = '"Times New Roman", Tinos, "Liberation Serif", Times, serif';

const WEIGHTS: Array<[string, string]> = [
  ['Regular', '400'],
  ['Medium', '500'],
  ['SemiBold', '600'],
  ['Bold', '700'],
];

let fontsPromise: Promise<unknown> | null = null;
export const loadFonts = () => {
  if (!fontsPromise) {
    fontsPromise = Promise.all(
      WEIGHTS.map(([name, weight]) =>
        loadFont({
          family: 'Be Vietnam Pro',
          url: staticFile(`fonts/BeVietnamPro-${name}.ttf`),
          weight,
          format: 'truetype',
        }),
      ),
    );
  }
  return fontsPromise;
};

// Calm, decisive easing: quick start, long settle, no overshoot.
export const EASE = Easing.bezier(0.2, 0, 0, 1);
export const EASE_IN_OUT = Easing.bezier(0.45, 0, 0.2, 1);

/** 0 to 1 between frames a and b, clamped, eased. */
export const prog = (frame: number, a: number, b: number, easing = EASE) =>
  interpolate(frame, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing,
  });

/** Fade in over [a, a+d], and out over [b, b+d] when b is given. */
export const fade = (frame: number, a: number, d = 10, b?: number, d2 = d) => {
  const inP = prog(frame, a, a + d);
  if (b === undefined) return inP;
  return inP * (1 - prog(frame, b, b + d2));
};

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** A token colour with an alpha. */
export const alpha = (hex: string, a: number) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

/** Deterministic pseudo random in [0, 1). */
export const rand = (seed: number) => {
  const x = Math.sin(seed * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
};
