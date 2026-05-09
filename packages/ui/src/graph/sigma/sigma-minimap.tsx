"use client";

import { useRef, useEffect } from "react";
import type Sigma from "sigma";

interface SigmaMinimapProps {
  sigma: Sigma | null;
  graphTheme: "light" | "dark";
}

export function SigmaMinimap({ sigma, graphTheme }: SigmaMinimapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!sigma) return;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = () => {
      const width = canvas.width;
      const height = canvas.height;
      const graph = sigma.getGraph();

      ctx.clearRect(0, 0, width, height);

      if (graph.order === 0) return;

      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;

      graph.forEachNode((_node, attrs) => {
        if (attrs.x < minX) minX = attrs.x;
        if (attrs.x > maxX) maxX = attrs.x;
        if (attrs.y < minY) minY = attrs.y;
        if (attrs.y > maxY) maxY = attrs.y;
      });

      const rangeX = maxX - minX || 1;
      const rangeY = maxY - minY || 1;
      const padding = 8;
      const drawW = width - padding * 2;
      const drawH = height - padding * 2;

      graph.forEachNode((_node, attrs) => {
        const px = padding + ((attrs.x - minX) / rangeX) * drawW;
        const py = padding + ((attrs.y - minY) / rangeY) * drawH;
        ctx.fillStyle = (attrs.color as string) || "#6b7280";
        ctx.beginPath();
        ctx.arc(px, py, 1.5, 0, Math.PI * 2);
        ctx.fill();
      });

      const camera = sigma.getCamera();
      const state = camera.getState();
      const vw = (1 / (state.ratio || 1)) * 0.3;
      const vh = (1 / (state.ratio || 1)) * 0.3;
      const vx = padding + ((state.x - minX) / rangeX) * drawW - (vw * drawW) / 2;
      const vy = padding + ((state.y - minY) / rangeY) * drawH - (vh * drawH) / 2;

      ctx.strokeStyle = graphTheme === "dark" ? "rgba(255,255,255,0.3)" : "rgba(0,0,0,0.3)";
      ctx.lineWidth = 1;
      ctx.strokeRect(vx, vy, vw * drawW, vh * drawH);
    };

    sigma.on("afterRender", draw);
    draw();

    return () => {
      sigma.off("afterRender", draw);
    };
  }, [sigma, graphTheme]);

  const isDark = graphTheme === "dark";

  return (
    <canvas
      ref={canvasRef}
      width={120}
      height={80}
      className={`absolute bottom-3 left-3 z-10 rounded-lg border hidden sm:block ${
        isDark
          ? "bg-[#1a1a2e] border-white/10 shadow-lg shadow-black/40"
          : "bg-[var(--color-bg-surface)] border-[var(--color-border-default)] shadow-lg shadow-black/20"
      }`}
    />
  );
}
