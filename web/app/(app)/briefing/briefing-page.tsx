"use client";

import { useSession } from "@/components/app-shell";
import { BriefingView } from "./briefing-view";

export function BriefingScreen() {
  const { industryId } = useSession();
  return <BriefingView key={industryId} />;
}
