import React from 'react';
import {GRID, SceneTitle, useVideo} from '../chrome';
import {CHUNKS, FUSED, LISTS, RERANKED} from '../data';
import {Icon} from '../icons';
import {CHAT} from '../strings';
import {EASE_IN_OUT, fade, lerp, prog, SANS, SERIF} from '../theme';
import {T} from './timeline';

const COL_W = 320;
const COL_GAP = 26;
const CHIP_H = 44;
const STEP = 54;
const LIST_Y = 388;
const FUSE_LABEL_Y = 572;
const FUSE_Y = 612;
const RERANK_X = 1240;
const RERANK_W = GRID.workX + GRID.workW - RERANK_X;
const TOP_H = 84;

const colX = (j: number) => GRID.workX + j * (COL_W + COL_GAP);

const Chip: React.FC<{
  x: number;
  y: number;
  w: number;
  h?: number;
  rank: number;
  id: number;
  opacity: number;
  score?: {value: number; grow: number; text: string};
  best?: number;
  snippet?: number;
}> = ({x, y, w, h = CHIP_H, rank, id, opacity, score, best = 0, snippet = 0}) => {
  const {pal, copy} = useVideo();
  const chunk = CHUNKS.find((c) => c.id === id)!;
  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        width: w,
        height: h,
        boxSizing: 'border-box',
        background: pal.surface,
        boxShadow: `0 0 0 1px ${best > 0 ? pal.gold : pal.border}`,
        borderRadius: 4,
        opacity,
        overflow: 'hidden',
      }}
    >
      <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: 4 * best, background: pal.gold}} />
      <div style={{display: 'flex', alignItems: 'center', height: CHIP_H, padding: '0 16px', gap: 14}}>
        <span style={{fontFamily: SANS, fontSize: 15, color: pal.muted, width: 12}}>{rank}</span>
        <span style={{fontFamily: SANS, fontSize: 18, fontWeight: 500, color: pal.text}}>{copy.chunk(id)}</span>
        {score ? (
          <div style={{marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12, opacity: score.grow}}>
            <div style={{width: 150, height: 6, background: pal.oliveSoft, borderRadius: 3}}>
              <div style={{width: 150 * score.value * score.grow, height: 6, background: pal.oliveText, borderRadius: 3}} />
            </div>
            <span style={{fontFamily: SANS, fontSize: 16, color: pal.text2, width: 40, textAlign: 'right'}}>
              {score.text}
            </span>
          </div>
        ) : null}
      </div>
      {snippet > 0 ? (
        <div
          style={{
            padding: '0 18px 0 42px',
            marginTop: -4,
            fontFamily: SERIF,
            fontSize: 19,
            lineHeight: '25px',
            color: pal.text,
            opacity: snippet,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {chunk.text}
        </div>
      ) : null}
    </div>
  );
};

const Label: React.FC<{x: number; y: number; text: string; sub?: string; opacity: number}> = ({
  x,
  y,
  text,
  sub,
  opacity,
}) => {
  const {pal} = useVideo();
  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        display: 'flex',
        alignItems: 'baseline',
        gap: 10,
        fontFamily: SANS,
        opacity,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{fontSize: 19, fontWeight: 500, color: pal.text}}>{text}</span>
      {sub ? <span style={{fontSize: 16, color: pal.muted}}>{sub}</span> : null}
    </div>
  );
};

export const RetrieveScene: React.FC<{frame: number}> = ({frame}) => {
  const {pal, copy, lang} = useVideo();
  if (frame < T.retr - 2 || frame > T.ans + 2) return null;
  const f = frame - T.retr;
  const out = 1 - prog(f, 96, 105);
  const typed = Math.round(lerp(0, CHAT.question.length, prog(f, 6, 24, (t) => t)));
  const lists: Array<{ids: number[]; label: string; sub: string}> = [
    {ids: LISTS.fts, label: copy.listFts, sub: 'FTS5'},
    {ids: LISTS.vec, label: copy.listVec, sub: 'bge-m3'},
    {ids: LISTS.fold, label: copy.listFold, sub: 'FTS5'},
  ];
  const fusedVisible = f >= 58;
  const rerank = prog(f, 62, 76, EASE_IN_OUT);
  const scoreGrow = prog(f, 70, 80);
  const expand = prog(f, 78, 88);
  const fmt = (v: number) => (lang === 'vi' ? v.toFixed(2).replace('.', ',') : v.toFixed(2));

  return (
    <>
      <SceneTitle frame={frame} start={T.retr} end={T.ans} title={copy.retrTitle} sub={copy.retrSub} />
      <div style={{position: 'absolute', inset: 0, opacity: out}}>
        {/* The question */}
        <div
          style={{
            position: 'absolute',
            left: GRID.workX,
            top: 236,
            width: GRID.workW,
            height: 60,
            boxSizing: 'border-box',
            background: pal.surface,
            boxShadow: `0 0 0 1px ${pal.border}`,
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            padding: '0 22px',
            gap: 14,
            opacity: fade(f, 2, 8),
          }}
        >
          <Icon name="chat" size={22} color={pal.muted} />
          <span style={{fontFamily: SANS, fontSize: 23, color: pal.text}}>
            {CHAT.question.slice(0, typed)}
          </span>
          {lang === 'en' ? (
            <span style={{fontFamily: SANS, fontSize: 17, color: pal.muted, opacity: fade(f, 22, 8)}}>
              {CHAT.questionGloss}
            </span>
          ) : null}
          <span style={{marginLeft: 'auto'}}>
            <Icon name="send" size={22} color={typed >= CHAT.question.length ? pal.oliveText : pal.muted} />
          </span>
        </div>

        {lists.map((l, j) => (
          <Label key={j} x={colX(j)} y={LIST_Y - 40} text={l.label} sub={l.sub} opacity={fade(f, 20 + j * 3, 8)} />
        ))}

        {/* Each retriever's ranked chunks stay in place, dimmed once fused */}
        {lists.map((l, j) =>
          l.ids.map((id, r) => {
            const appear = prog(f, 24 + j * 4 + r * 3, 30 + j * 4 + r * 3);
            const dim = prog(f, 44 + j * 2, 56 + j * 2);
            return (
              <Chip
                key={`ghost-${j}-${r}`}
                x={colX(j)}
                y={LIST_Y + r * STEP}
                w={COL_W}
                rank={r + 1}
                id={id}
                opacity={appear * (1 - 0.6 * dim)}
              />
            );
          }),
        )}

        {/* Copies travel to the fused list (RRF) */}
        {!fusedVisible &&
          lists.map((l, j) =>
            l.ids.map((id, r) => {
              const m = prog(f, 44 + j * 2, 56 + j * 2, EASE_IN_OUT);
              const k = FUSED.indexOf(id);
              if (k < 0 || m <= 0) return null;
              const x = lerp(colX(j), GRID.workX, m);
              const y = lerp(LIST_Y + r * STEP, FUSE_Y + k * STEP, m);
              const opacity = 1;
              return (
                <Chip
                  key={`${j}-${r}`}
                  x={x}
                  y={y}
                  w={COL_W}
                  rank={m > 0.5 && k >= 0 ? k + 1 : r + 1}
                  id={id}
                  opacity={opacity}
                />
              );
            }),
          )}

        <Label x={GRID.workX} y={FUSE_LABEL_Y} text={copy.listRrf} opacity={fade(f, 44, 8)} />
        <Label x={RERANK_X} y={FUSE_LABEL_Y} text={copy.listRerank} opacity={fade(f, 62, 8)} />

        {/* The fused order stays behind, dimmed, once ViRanker takes over */}
        {fusedVisible &&
          FUSED.map((id, k) => (
            <Chip
              key={`fused-ghost-${id}`}
              x={GRID.workX}
              y={FUSE_Y + k * STEP}
              w={COL_W}
              rank={k + 1}
              id={id}
              opacity={0.4 * prog(f, 62, 70)}
            />
          ))}

        {fusedVisible &&
          FUSED.map((id, k) => {
            const nk = RERANKED.findIndex((r) => r.id === id);
            const score = RERANKED[nk].score;
            const fromY = FUSE_Y + k * STEP;
            const toY = nk === 0 ? FUSE_Y : FUSE_Y + TOP_H + 10 + (nk - 1) * STEP;
            const restY = FUSE_Y + nk * STEP;
            const y = lerp(lerp(fromY, restY, rerank), toY, expand);
            return (
              <Chip
                key={id}
                x={lerp(GRID.workX, RERANK_X, rerank)}
                y={y}
                w={lerp(COL_W, RERANK_W, rerank)}
                h={nk === 0 ? lerp(CHIP_H, TOP_H, expand) : CHIP_H}
                rank={rerank > 0.5 ? nk + 1 : k + 1}
                id={id}
                opacity={1}
                score={{value: score, grow: scoreGrow, text: fmt(score)}}
                best={nk === 0 ? expand : 0}
                snippet={nk === 0 ? prog(f, 82, 90) : 0}
              />
            );
          })}
      </div>
    </>
  );
};
