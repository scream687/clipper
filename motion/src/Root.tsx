import React from "react";
import { Composition } from "remotion";
import { Insert, InsertProps } from "./Insert";

const defaults: InsertProps = {
  kind: "card", text: "10x faster", sub: "than last year", media: "", mediaType: "image", accent: "#FFE14D",
  fps: 25, frames: 75, width: 1080, height: 1920,
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Insert"
    component={Insert}
    defaultProps={defaults}
    durationInFrames={defaults.frames}
    fps={defaults.fps}
    width={defaults.width}
    height={defaults.height}
    calculateMetadata={({ props }) => ({ durationInFrames: props.frames, fps: props.fps, width: props.width, height: props.height })}
  />
);
