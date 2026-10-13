import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Our type tokens (app/globals.css) are font sizes; without this, tailwind-merge would treat
// `text-body` as a color and drop it next to `text-ink`.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        {
          text: [
            "display", "h1", "h2", "h3", "body-lg", "body", "small", "label", "overline",
            "brief-headline", "brief-deck", "brief-h2", "brief-number", "brief-body",
          ],
        },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
