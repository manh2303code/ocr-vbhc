import React from 'react';
import {Composition} from 'remotion';
import {Memory, MEMORY_FRAMES} from './Memory';
import {Pipeline, PIPELINE_FRAMES, VideoProps} from './Pipeline';
import {loadFonts} from './theme';

loadFonts();

const defaults: VideoProps = {lang: 'vi', theme: 'light'};

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="Pipeline"
      component={Pipeline}
      durationInFrames={PIPELINE_FRAMES}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaults}
    />
    <Composition
      id="Memory"
      component={Memory}
      durationInFrames={MEMORY_FRAMES}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaults}
    />
  </>
);
