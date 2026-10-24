import React from 'react';
import {Panel, SceneTitle, useVideo} from '../chrome';
import {FIELDS} from '../data';
import {Icon} from '../icons';
import {fade, prog, SANS, SERIF} from '../theme';
import {FIELD_FRAMES, T} from './timeline';

/** How strongly row i (and its source on the page) is lit at this frame. */
export const fieldGlow = (frame: number, i: number) => {
  const s = FIELD_FRAMES[i];
  return prog(frame, s, s + 4) * (1 - prog(frame, s + 10, s + 18));
};

/** Gold highlights on a page over the place each field is read from. */
export const FieldPageOverlay: React.FC<{frame: number; page: 1 | 3}> = ({frame, page}) => {
  const {pal} = useVideo();
  if (frame < T.fields || frame > T.index + 4) return null;
  return (
    <>
      {FIELDS.map((fd, i) => {
        if (fd.page !== page || !fd.rect) return null;
        const g = fieldGlow(frame, i);
        if (g <= 0) return null;
        const pad = 4;
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: fd.rect.x - pad,
              top: fd.rect.y - pad,
              width: fd.rect.w + 2 * pad,
              height: fd.rect.h + 2 * pad,
              background: pal.paperMark,
              mixBlendMode: 'multiply',
              boxShadow: `inset 0 0 0 1.5px ${pal.paperMarkEdge}`,
              borderRadius: 3,
              opacity: g,
            }}
          />
        );
      })}
    </>
  );
};

export const FieldsScene: React.FC<{frame: number}> = ({frame}) => {
  const {pal, copy} = useVideo();
  if (frame < T.fields - 2 || frame > T.index + 2) return null;
  const f = frame - T.fields;
  const panelOpacity = fade(f, 4, 10, 125, 10);
  let y = 0;
  return (
    <>
      <SceneTitle frame={frame} start={T.fields} end={T.index} title={copy.fieldsTitle} sub={copy.fieldsSub} />
      <Panel opacity={panelOpacity} label={copy.fieldsPanel}>
        <div style={{position: 'absolute', top: 53, left: 0, right: 0}}>
          {FIELDS.map((fd, i) => {
            const h = fd.lines === 2 ? 72 : 44;
            const top = y;
            y += h;
            const s = FIELD_FRAMES[i];
            const reveal = prog(frame, s, s + 10);
            const glow = fieldGlow(frame, i);
            const isFullText = i === FIELDS.length - 1;
            const value = fd.value ?? (isFullText ? copy.pages3 : copy.none);
            const muted = fd.value === null && !isFullText;
            return (
              <div
                key={i}
                style={{
                  position: 'absolute',
                  top,
                  left: 0,
                  right: 0,
                  height: h,
                  display: 'flex',
                  alignItems: 'flex-start',
                  padding: '0 32px',
                  borderBottom: i < FIELDS.length - 1 ? `1px solid ${pal.border}` : 'none',
                  background: glow > 0 ? `color-mix(in srgb, ${pal.goldSoft} ${Math.round(glow * 100)}%, transparent)` : 'transparent',
                }}
              >
                <div
                  style={{
                    width: 236,
                    flex: 'none',
                    fontFamily: SANS,
                    fontSize: 18,
                    lineHeight: '44px',
                    color: pal.text2,
                  }}
                >
                  {copy.fieldLabels[i]}
                </div>
                <div
                  style={{
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    paddingTop: 8,
                    clipPath: `inset(0 ${100 - reveal * 100}% 0 0)`,
                  }}
                >
                  {isFullText ? <Icon name="fileText" size={22} color={pal.text2} /> : null}
                  <div
                    style={{
                      fontFamily: isFullText || muted ? SANS : SERIF,
                      fontSize: isFullText || muted ? 18 : 21,
                      lineHeight: '28px',
                      color: fd.gold ? pal.gold : muted ? pal.muted : pal.text,
                      fontWeight: fd.gold ? 700 : 400,
                    }}
                  >
                    {value}
                  </div>
                </div>
                {fd.page ? (
                  <div
                    style={{
                      flex: 'none',
                      fontFamily: SANS,
                      fontSize: 15,
                      lineHeight: '44px',
                      color: pal.muted,
                      opacity: glow,
                      marginLeft: 16,
                    }}
                  >
                    {copy.fromPage(fd.page)}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </Panel>
    </>
  );
};
