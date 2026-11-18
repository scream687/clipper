// A full-frame insert spliced over the speaker: B-roll (a stock clip or photo) or a card (a number or a name over
// the speaker's own frame, blurred). Clipper renders one per B-roll moment; captions stay on top of it.
import React from "react";
import {
  AbsoluteFill, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";

export type InsertProps = {
  kind: "stock" | "card";
  text: string;          // the key words (stock) or the big number / name (card)
  sub: string;           // a short line under a card
  media: string;         // file in the render's public dir: the stock clip/photo, or the speaker still for a card
  mediaType: "video" | "image";
  accent: string;
  fps: number;
  frames: number;
  width: number;
  height: number;
};

const FONT = "Clip Arial Black";

const fontFace = `@font-face { font-family: "${FONT}"; src: url("${staticFile("arial-black.ttf")}"); }`;

const Words: React.FC<{ text: string; size: number; accent: string }> = ({ text, size, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const words = text.toUpperCase().split(/\s+/).filter(Boolean);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: size * 0.28, padding: "0 7%" }}>
      {words.map((word, i) => {
        const pop = spring({ frame: frame - i * 3, fps, config: { damping: 12, stiffness: 180 } });
        return (
          <span key={i} style={{
            fontFamily: FONT, fontSize: size, lineHeight: 1.05, color: i === words.length - 1 ? accent : "#fff",
            transform: `translateY(${(1 - pop) * size * 0.6}px) scale(${0.7 + pop * 0.3})`, opacity: pop,
            WebkitTextStroke: `${Math.round(size / 9)}px #000`, paintOrder: "stroke fill",
          }}>{word}</span>
        );
      })}
    </div>
  );
};

export const Insert: React.FC<InsertProps> = ({ kind, text, sub, media, mediaType, accent, frames, width }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const push = interpolate(frame, [0, frames], [1.0, 1.1]);                    // slow Ken Burns push
  const enter = interpolate(frame, [0, 5], [0, 1], { extrapolateRight: "clamp" });  // quick cut-in
  const bar = spring({ frame: frame - 4, fps, config: { damping: 200 } });
  const src = media ? staticFile(media) : "";
  const picture = !src ? null : mediaType === "video"
    ? <OffthreadVideo src={src} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    : <Img src={src} style={{ width: "100%", height: "100%", objectFit: "cover" }} />;
  const size = Math.round(width * (kind === "card" ? 0.16 : 0.1));
  return (
    <AbsoluteFill style={{ backgroundColor: "#0b0b0b", opacity: enter }}>
      <style>{fontFace}</style>
      <AbsoluteFill style={{ transform: `scale(${push})`, filter: kind === "card" ? "blur(28px) brightness(0.45)" : "none" }}>
        {picture}
      </AbsoluteFill>
      {/* vignette: the grade layer of the motion-graphics stack, so inserts sit in the same world as the footage */}
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 45%, transparent 55%, rgba(0,0,0,0.45) 100%)" }} />
      {kind === "stock" ? (
        <AbsoluteFill style={{ background: "linear-gradient(to bottom, rgba(0,0,0,0.55), rgba(0,0,0,0) 35%)" }}>
          <div style={{ position: "absolute", top: "11%", width: "100%" }}><Words text={text} size={size} accent={accent} /></div>
        </AbsoluteFill>
      ) : (
        // The card sits in the upper half: the captions keep running across the lower third.
        <AbsoluteFill style={{ justifyContent: "flex-start", alignItems: "center", flexDirection: "column", gap: size * 0.35,
                               paddingTop: "24%" }}>
          <Words text={text} size={size} accent={accent} />
          <div style={{ width: `${bar * 38}%`, height: Math.max(6, size / 14), background: accent, borderRadius: 99 }} />
          {sub ? <div style={{ fontFamily: FONT, fontSize: size * 0.32, color: "rgba(255,255,255,0.9)", textAlign: "center",
                               padding: "0 10%", opacity: bar }}>{sub.toUpperCase()}</div> : null}
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};
