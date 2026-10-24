import * as THREE from "three";

export const C = {
  sky: "#C7D3F4",
  meadow: "#8DCB7F",
  meadowDeep: "#79B86C",
  soil: "#B88A5F",
  sand: "#EBD9AE",
  water: "#6DB4E6",
  stone: "#CFCBDD",
  angel: "#F7F8FF",
  seraph: "#3D6BE0",
  halo: "#F5C443",
  imp: "#E5383B",
  impDark: "#8E1B22",
  pebble: "#CFC8BA",
  ink: "#23233A",
  coin: "#F5C443",
  cursed: "#B3123A",
  news: "#F29E38",
  held: "#7FD18B",
  wobbly: "#F4C26B",
  shaky: "#EF7D73",
  unknown: "#E6DCC4",
};

const mat = (color: string, extra: THREE.MeshStandardMaterialParameters = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.7, ...extra });

// Shared so dozens of creatures cost one material each.
export const M = {
  angel: mat(C.angel, { roughness: 0.55 }),
  wing: mat("#E9EEFF", { roughness: 0.5 }),
  seraph: mat(C.seraph),
  halo: mat(C.halo, { emissive: C.halo, emissiveIntensity: 0.6, roughness: 0.3, metalness: 0.3 }),
  imp: mat(C.imp, { roughness: 0.55 }),
  impDark: mat(C.impDark),
  pebble: mat(C.pebble),
  bag: mat("#9B7653"),
  ink: mat(C.ink, { roughness: 0.3 }),
  white: mat("#FFFFFF", { roughness: 0.4 }),
  cheek: mat("#F7A1B5"),
  news: mat(C.news),
  paper: mat("#F4F1E6"),
  outsiderHat: mat("#2C2C3E", { roughness: 0.5 }),
  gold: mat(C.halo, { metalness: 0.5, roughness: 0.3 }),
};
