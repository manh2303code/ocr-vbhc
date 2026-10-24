import React from 'react';
import {interpolate} from 'remotion';
import {Caption, Panel, SceneTitle, useVideo} from '../chrome';
import {OCR_BLOCKS} from '../data';
import {alpha, fade, prog, SANS, SERIF} from '../theme';
import {T} from './timeline';

// Flatten the blocks into tokens (words) so that the output can stream.
type Tok = {block: number; line: number; word: string; index: number};
const TOKENS: Tok[] = [];
OCR_BLOCKS.forEach((b, bi) =>
  b.lines.forEach((l, li) =>
    l.text.split(' ').forEach((word) => TOKENS.push({block: bi, line: li, word, index: TOKENS.length})),
  ),
);
const STREAM: [number, number] = [T.ocr + 20, T.ocr + 168];

export const detectFrame = (i: number) => T.ocr + 8 + i * 6;

export const ocrState = (frame: number) => {
  const visible = Math.floor(
    interpolate(frame, STREAM, [0, TOKENS.length], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
  );
  const active = visible > 0 && visible < TOKENS.length ? TOKENS[visible - 1].block : -1;
  return {visible, active};
};

/** Layout blocks drawn over page 1 while Chandra reads it. */
export const OcrPageOverlay: React.FC<{frame: number}> = ({frame}) => {
  const {pal} = useVideo();
  if (frame < T.ocr || frame > T.gate + 12) return null;
  const {active} = ocrState(frame);
  const out = 1 - prog(frame, T.gate, T.gate + 12);
  return (
    <>
      {OCR_BLOCKS.map((b, i) => {
        const d = prog(frame, detectFrame(i), detectFrame(i) + 10);
        if (d <= 0) return null;
        const pad = 4;
        const x = b.rect.x - pad;
        const y = b.rect.y - pad;
        const w = b.rect.w + 2 * pad;
        const h = b.rect.h + 2 * pad;
        const per = 2 * (w + h);
        const isActive = i === active;
        return (
          <React.Fragment key={i}>
            <svg
              style={{position: 'absolute', left: x - 2, top: y - 2, overflow: 'visible', opacity: out}}
              width={w + 4}
              height={h + 4}
            >
              <rect
                x={2}
                y={2}
                width={w}
                height={h}
                rx={3}
                fill={pal.olive}
                fillOpacity={isActive ? 0.12 : 0}
                stroke={pal.olive}
                strokeWidth={isActive ? 2.5 : 1.5}
                strokeDasharray={per}
                strokeDashoffset={per * (1 - d)}
              />
            </svg>
          </React.Fragment>
        );
      })}
    </>
  );
};

/** The recognised text streaming into the editor's full-text view. */
export const OcrScene: React.FC<{frame: number}> = ({frame}) => {
  const {pal, copy} = useVideo();
  if (frame < T.ocr - 2 || frame > T.gate + 2) return null;
  const panelOpacity = fade(frame, T.ocr + 6, 12, T.gate - 10, 10);
  const {visible} = ocrState(frame);
  return (
    <>
      <SceneTitle frame={frame} start={T.ocr} end={T.gate} title={copy.ocrTitle} sub={copy.ocrSub} />
      <Panel opacity={panelOpacity} label={copy.ocrPanel}>
        <div
          style={{
            position: 'absolute',
            top: 53,
            left: 0,
            right: 0,
            bottom: 0,
            padding: '28px 32px 22px',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'flex-end',
            WebkitMaskImage: 'linear-gradient(to bottom, transparent 0px, black 26px)',
            maskImage: 'linear-gradient(to bottom, transparent 0px, black 26px)',
          }}
        >
          <div style={{minHeight: '100%', flex: 'none'}}>
            {OCR_BLOCKS.map((b, bi) => {
              const toks = TOKENS.filter((t) => t.block === bi && t.index < visible);
              if (toks.length === 0) return null;
              return (
                <div key={bi} style={{display: 'flex', gap: 24, marginBottom: 12}}>
                  <div
                    style={{
                      width: 168,
                      flex: 'none',
                      fontFamily: SANS,
                      fontSize: 16,
                      lineHeight: '29px',
                      color: pal.muted,
                    }}
                  >
                    {copy.blockTags[b.tag]}
                  </div>
                  <div style={{flex: 1}}>
                    {b.lines.map((l, li) => {
                      const lt = toks.filter((t) => t.line === li);
                      if (lt.length === 0) return null;
                      return (
                        <div
                          key={li}
                          style={{
                            fontFamily: SERIF,
                            fontSize: 21,
                            lineHeight: '29px',
                            fontWeight: l.bold ? 700 : 400,
                            fontStyle: l.italic ? 'italic' : 'normal',
                            color: pal.text,
                          }}
                        >
                          {lt.map((t) => {
                            const age = visible - 1 - t.index;
                            const hi = age < 4 ? 1 - age / 4 : 0;
                            return (
                              <span
                                key={t.index}
                                style={{
                                  background: hi > 0 ? alpha(pal.olive, 0.18 * hi) : 'transparent',
                                  borderRadius: 2,
                                }}
                              >
                                {t.word}{' '}
                              </span>
                            );
                          })}
                          {lt[lt.length - 1].index === visible - 1 && visible < TOKENS.length ? (
                            <span
                              style={{
                                display: 'inline-block',
                                width: 2,
                                height: 22,
                                background: pal.olive,
                                verticalAlign: -4,
                              }}
                            />
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </Panel>
      <Caption frame={frame} start={T.ocr + 120} end={T.gate} lines={[copy.ocrCaption]} />
    </>
  );
};
