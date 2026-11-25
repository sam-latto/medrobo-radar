"use client";

import { useSession } from "@/components/app-shell";
import { EventsView } from "./events-view";

export function EventsScreen() {
  const { industryId } = useSession();
  return <EventsView key={industryId} />;
}
