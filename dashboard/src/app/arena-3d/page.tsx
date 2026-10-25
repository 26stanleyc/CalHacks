import { Inter, JetBrains_Mono } from "next/font/google";
import { Suspense } from "react";

import CyberArena from "@/components/arena/CyberArena";

const hud = Inter({ subsets: ["latin"], weight: ["300", "400", "500", "600"], variable: "--font-hud" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

/** The immune-response scene by default; ?scene=astrolabe brings back the Astrolabe stage. */
async function Arena({ searchParams }: { searchParams: PageProps<"/arena-3d">["searchParams"] }) {
  const { scene } = await searchParams;
  return <CyberArena scene={scene === "astrolabe" ? "astrolabe" : "immune"} />;
}

// Full-bleed below the 48px header (h-12 in layout.tsx).
export default function Page({ searchParams }: PageProps<"/arena-3d">) {
  return (
    <div className={`fixed inset-x-0 bottom-0 top-12 bg-[#140306] ${hud.variable} ${mono.variable}`}>
      <Suspense fallback={null}>
        <Arena searchParams={searchParams} />
      </Suspense>
    </div>
  );
}
