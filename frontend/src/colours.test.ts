import { describe, it, expect } from 'vitest';
import { COLOUR_HEX } from './colours';

describe('COLOUR_HEX', () => {
  it('maps dark_blue to #1565C0', () => {
    expect(COLOUR_HEX.dark_blue).toBe('#1565C0');
  });

  it('maps light_blue to #5BC5F2', () => {
    expect(COLOUR_HEX.light_blue).toBe('#5BC5F2');
  });

  it('maps green to #2E9E5B', () => {
    expect(COLOUR_HEX.green).toBe('#2E9E5B');
  });

  it('maps amber to #E8A317', () => {
    expect(COLOUR_HEX.amber).toBe('#E8A317');
  });

  it('maps red to #E1382D', () => {
    expect(COLOUR_HEX.red).toBe('#E1382D');
  });

  it('maps dark_red to #8B1A1A', () => {
    expect(COLOUR_HEX.dark_red).toBe('#8B1A1A');
  });

  it('maps black to #2B2B2B', () => {
    expect(COLOUR_HEX.black).toBe('#2B2B2B');
  });

  it('has exactly 7 entries', () => {
    expect(Object.keys(COLOUR_HEX).length).toBe(7);
  });
});
