/**
 * Traffic-light colour palette for the PET supply grid.
 * Each name maps to the exact hex from the design spec.
 * These are status colours (not categorical series colours) — the supply
 * engine assigns them; the UI renders them unchanged.
 */

export type ColourName =
  | 'dark_blue'
  | 'light_blue'
  | 'green'
  | 'amber'
  | 'red'
  | 'dark_red'
  | 'black';

export const COLOUR_HEX: Record<ColourName, string> = {
  dark_blue: '#1565C0', // cover > 3 wks
  light_blue: '#5BC5F2', // cover 2-3 wks
  green: '#2E9E5B', // cover 1-2 wks
  amber: '#E8A317', // cover 0-1 wks
  red: '#E1382D', // stock-out, > reaction_window out
  dark_red: '#8B1A1A', // stock-out, <= reaction_window (lost sale)
  black: '#2B2B2B', // over-cover past MLOR window
};

/** Foreground text colour for a given traffic-light cell background. */
export function colourFg(name: ColourName): string {
  // amber needs dark text for contrast; all others use white
  return name === 'amber' ? '#1a2733' : '#ffffff';
}
