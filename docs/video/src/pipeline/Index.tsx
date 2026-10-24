import React from 'react';
import {GRID, SceneTitle, useVideo} from '../chrome';
import {P1, PAGE_W} from '../doc';
import {CHUNKS, P1_CUTS} from '../data';
import {Icon} from '../icons';
import {fade, prog, rand, SANS, SERIF} from '../theme';
import {T} from './timeline';

const ROW_Y = 262;
const ROW_H = 78;
const BARS = 48;
const VEC_X = GRID.workX + GRID.workW - BARS * 7;

/** Chunk boundaries on page 1, kept faintly while the passages are searched. */
export const ChunkPageOverlay: React.FC<{frame: number}> = ({frame}) => {
  const {pal} = useVideo();
  if (frame < T.index + 10 || frame > T.ans + 14) return null;
  const draw = prog(frame, T.index + 14, T.index + 30);
  const opacity = 1 - 0.55 * prog(frame, T.retr, T.retr + 12) - 0.45 * prog(frame, T.ans, T.ans + 14);
  const starts = [P1.agency.y - 6, ...P1_CUTS];
  return (
    <div style={{position: 'absolute', inset: 0, opacity}}>
      {P1_CUTS.map((y) => (
        <div
          key={y}
          style={{
            position: 'absolute',
            left: 0,
            top: y,
            width: PAGE_W * draw,
            borderTop: `1.5px dashed ${pal.olive}`,
          }}
        />
      ))}
      {starts.map((y, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: 10,
            top: y + 6,
            width: 26,
            height: 26,
            borderRadius: 3,
            background: pal.olive,
            color: '#FFFFFF',
            fontFamily: SANS,
            fontSize: 15,
            fontWeight: 600,
            lineHeight: '26px',
            textAlign: 'center',
            opacity: prog(frame, T.index + 18 + i * 3, T.index + 24 + i * 3),
          }}
        >
          {i + 1}
        </div>
      ))}
    </div>
  );
};

const Vector: React.FC<{seed: number; grow: number; color: string}> = ({seed, grow, color}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: 3, height: 50}}>
    {Array.from({length: BARS}, (_, j) => {
      const h = 6 + Math.round(rand(seed * 97 + j * 13) * 40);
      const g = Math.min(1, Math.max(0, grow * 1.6 - (j / BARS) * 0.6));
      return (
        <div
          key={j}
          style={{
            width: 4,
            height: h * g,
            background: color,
            opacity: 0.35 + 0.65 * rand(seed * 31 + j * 7),
          }}
        />
      );
    })}
  </div>
);

export const IndexScene: React.FC<{frame: number}> = ({frame}) => {
  const {pal, copy} = useVideo();
  if (frame < T.index - 2 || frame > T.retr + 2) return null;
  const f = frame - T.index;
  const out = 1 - prog(f, 72, 80);
  return (
    <>
      <SceneTitle frame={frame} start={T.index} end={T.retr} title={copy.indexTitle} sub={copy.indexSub} />
      <div style={{position: 'absolute', inset: 0, opacity: out}}>
        <div
          style={{
            position: 'absolute',
            left: VEC_X,
            top: ROW_Y - 34,
            fontFamily: SANS,
            fontSize: 16,
            color: pal.muted,
            opacity: fade(f, 20, 10),
          }}
        >
          {copy.vecHead}
        </div>
        {CHUNKS.map((c, i) => {
          const a = prog(f, 12 + i * 5, 26 + i * 5);
          const grow = prog(f, 24 + i * 5, 42 + i * 5);
          return (
            <div
              key={c.id}
              style={{
                position: 'absolute',
                left: GRID.workX + (1 - a) * -70,
                top: ROW_Y + i * ROW_H,
                width: GRID.workW,
                height: ROW_H,
                display: 'flex',
                alignItems: 'center',
                borderTop: `1px solid ${pal.border}`,
                opacity: a,
              }}
            >
              <div style={{width: 128, flex: 'none'}}>
                <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 500, color: pal.text, lineHeight: '24px'}}>
                  {copy.chunk(c.id)}
                </div>
                <div style={{fontFamily: SANS, fontSize: 15, color: pal.muted, lineHeight: '22px'}}>
                  {copy.pageShort(c.page)}
                </div>
              </div>
              <div
                style={{
                  width: VEC_X - GRID.workX - 128 - 36,
                  flex: 'none',
                  fontFamily: SERIF,
                  fontSize: 19.5,
                  lineHeight: '26px',
                  color: pal.text,
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}
              >
                {c.text}
              </div>
              <div style={{marginLeft: 36}}>
                <Vector seed={c.id} grow={grow} color={pal.oliveText} />
              </div>
            </div>
          );
        })}
        <div
          style={{
            position: 'absolute',
            left: GRID.workX,
            top: ROW_Y + CHUNKS.length * ROW_H + 30,
            display: 'flex',
            gap: 64,
            opacity: fade(f, 50, 10),
          }}
        >
          {[copy.storeFts, copy.storeVec].map((s) => (
            <div
              key={s}
              style={{display: 'flex', alignItems: 'center', gap: 12, fontFamily: SANS, fontSize: 19, color: pal.text2}}
            >
              <Icon name="database" size={26} color={pal.oliveText} />
              {s}
              <span style={{opacity: prog(f, 58, 64)}}>
                <Icon name="check" size={20} color={pal.success} />
              </span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};
