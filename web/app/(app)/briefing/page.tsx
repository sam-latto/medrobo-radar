import { BriefingScreen } from "./briefing-page";

export const metadata = { title: "Briefing · Industry Radar" };

// Everything on this page loads in the browser from the API after sign-in, so there is
// nothing to prerender for instant navigation; opt out of that dev-time check.
export const instant = false;

export default function Page() {
  return <BriefingScreen />;
}
