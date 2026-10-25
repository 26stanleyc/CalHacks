import * as THREE from "three";

// Procedural textures drawn on a canvas, so the scene needs no image downloads.

const cache = new Map<string, THREE.Texture>();

function canvasTexture(key: string, size: number, draw: (ctx: CanvasRenderingContext2D, size: number) => void) {
  const hit = cache.get(key);
  if (hit) return hit;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  draw(canvas.getContext("2d")!, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  cache.set(key, texture);
  return texture;
}

/** Soft radial glow: white core fading to transparent. */
export function glowTexture() {
  return canvasTexture("glow", 128, (ctx, s) => {
    const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.2, "rgba(255,255,255,0.8)");
    g.addColorStop(0.5, "rgba(255,255,255,0.18)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, s, s);
  });
}

/** Lumpy smoke puff built from overlapping soft blobs. */
export function smokeTexture() {
  return canvasTexture("smoke", 256, (ctx, s) => {
    let seed = 7;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < 26; i++) {
      const x = s / 2 + (rand() - 0.5) * s * 0.45;
      const y = s / 2 + (rand() - 0.5) * s * 0.45;
      const r = s * (0.12 + rand() * 0.22);
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, "rgba(255,255,255,0.16)");
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, s, s);
    }
  });
}

/** Thin bright ring, for shockwaves. */
export function ringTexture() {
  return canvasTexture("ring", 256, (ctx, s) => {
    const g = ctx.createRadialGradient(s / 2, s / 2, s * 0.3, s / 2, s / 2, s / 2);
    g.addColorStop(0, "rgba(255,255,255,0)");
    g.addColorStop(0.75, "rgba(255,255,255,0.15)");
    g.addColorStop(0.9, "rgba(255,255,255,1)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, s, s);
  });
}
