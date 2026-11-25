"use client";

import { useSession } from "@/components/app-shell";
import { EventsView } from "./events-view";

export default function EventsPage() {
  const { industryId } = useSession();
  return <EventsView key={industryId} />;
}
