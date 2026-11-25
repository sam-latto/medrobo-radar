"use client";

import { useSession } from "@/components/app-shell";
import { BriefingView } from "./briefing-view";

export default function BriefingPage() {
  const { industryId } = useSession();
  return <BriefingView key={industryId} />;
}
