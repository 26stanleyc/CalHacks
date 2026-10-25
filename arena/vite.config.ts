import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two pages: the TagTeam overview at / and the 3D arena demo at /demo/.
export default defineConfig({
  plugins: [react()],
  server: { port: 5174 },
  build: {
    rolldownOptions: {
      input: {
        main: resolve(import.meta.dirname, "index.html"),
        demo: resolve(import.meta.dirname, "demo/index.html"),
        ethereum: resolve(import.meta.dirname, "ethereum/index.html"),
      },
    },
  },
});
