"use client";

import { useSession } from "@/components/app-shell";
import { CompaniesView } from "./companies-view";

export function CompaniesScreen() {
  const { industryId } = useSession();
  return <CompaniesView key={industryId} />;
}
