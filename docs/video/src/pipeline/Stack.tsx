import React from 'react';
import {GRID, useVideo, W} from '../chrome';
import {PAGE_W, Sheet} from '../doc';
import {EASE_IN_OUT, prog} from '../theme';
import {AnswerPageOverlay} from './Answer';
import {FieldPageOverlay, fieldGlow} from './Fields';
import {ChunkPageOverlay} from './Index';
import {OcrPageOverlay} from './Ocr';
import {SWAP_BACK, SWAP_OUT, T} from './timeline';

/**
 * The three-page document on the left. It arrives from the right, pages 1
 * and 2 step aside so that page 3 (recipients and signature) can be read,
 * and the stack leaves at the end.
 */
export const DocStack: React.FC<{frame: number}> = ({frame}) => {
  const {pal} = useVideo();
  const outro = prog(frame, T.outro, T.outro + 22, EASE_IN_OUT);
  if (frame < 56 || outro >= 1) return null;

  const arrive = (i: number) => prog(frame, 60 + i * 5, 96 + i * 5);
  const away = (i: number) =>
    prog(frame, SWAP_OUT[0] + i * 3, SWAP_OUT[1] + i * 3, EASE_IN_OUT) *
    (1 - prog(frame, SWAP_BACK[0] + (1 - i) * 3, SWAP_BACK[1] + (1 - i) * 3, EASE_IN_OUT));

  const offset = [0, 12, 24];
  const awayX = -(GRID.pageX + PAGE_W + 60);
  const fullTextGlow = fieldGlow(frame, 11);

  const pos = (i: number) => {
    // Page index i: 0 = page 1 (front), 2 = page 3 (back).
    const a = arrive(2 - i);
    let x = GRID.pageX + offset[i] + (1 - a) * (W - GRID.pageX + 80);
    let y = GRID.pageY + offset[i];
    if (i < 2) x += away(i) * awayX;
    if (i === 2) {
      const front = Math.min(away(0), away(1));
      x -= front * offset[2];
      y -= front * offset[2];
    }
    x -= outro * 900;
    return {x, y};
  };

  const p1 = pos(0);
  const p2 = pos(1);
  const p3 = pos(2);
  return (
    <div style={{position: 'absolute', inset: 0, opacity: 1 - outro}}>
      <Sheet
        page={3}
        paper={pal.paper}
        edge={pal.paperEdge}
        style={{
          left: p3.x,
          top: p3.y,
          boxShadow: `0 0 0 ${1 + fullTextGlow * 1.5}px ${fullTextGlow > 0.05 ? pal.gold : pal.paperEdge}`,
        }}
      >
        <FieldPageOverlay frame={frame} page={3} />
      </Sheet>
      <Sheet page={2} paper={pal.paper} edge={pal.paperEdge} style={{left: p2.x, top: p2.y}} />
      <Sheet page={1} paper={pal.paper} edge={pal.paperEdge} style={{left: p1.x, top: p1.y}}>
        <OcrPageOverlay frame={frame} />
        <FieldPageOverlay frame={frame} page={1} />
        <ChunkPageOverlay frame={frame} />
        <AnswerPageOverlay frame={frame} />
      </Sheet>
    </div>
  );
};
