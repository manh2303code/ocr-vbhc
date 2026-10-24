// Scene starts of the Pipeline composition, in frames at 30 fps.
export const T = {
  ocr: 84,
  gate: 270,
  fields: 370,
  index: 505,
  retr: 585,
  ans: 690,
  outro: 825,
  end: 900,
};

export const STAGE_STARTS = [T.ocr, T.gate, T.fields, T.index, T.retr, T.ans];

// Fields: rows 0 to 7 come from page 1, rows 8 to 10 from page 3.
export const FIELD_FRAMES = [10, 17, 24, 31, 38, 45, 52, 59, 88, 96, 104, 114].map(
  (f) => T.fields + f,
);
// Pages 1 and 2 leave the stack so that page 3 is in front, then return.
export const SWAP_OUT: [number, number] = [T.fields + 72, T.fields + 86];
export const SWAP_BACK: [number, number] = [T.index, T.index + 16];
