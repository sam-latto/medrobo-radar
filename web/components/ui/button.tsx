import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** Button variants from docs/DESIGN.md section 5. At most one `main` per view. */
export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-base text-body whitespace-nowrap cursor-pointer " +
    "disabled:cursor-default disabled:text-disabled",
  {
    variants: {
      variant: {
        main: "bg-teal text-white font-semibold hover:bg-teal-dark disabled:bg-surface",
        secondary:
          "bg-white border border-border-input text-ink font-medium hover:bg-surface",
        text: "text-teal font-medium hover:text-teal-deeper",
        danger: "bg-white border border-border-input text-danger font-medium hover:bg-danger-tint",
        pill:
          "h-[30px] rounded-full border border-teal text-teal text-small font-medium px-3 " +
          "aria-pressed:bg-teal-tint aria-pressed:text-teal-dark",
      },
      size: {
        default: "h-10 px-4",
        lg: "h-11 px-5",
        inline: "h-auto p-0",
      },
    },
    compoundVariants: [{ variant: "pill", className: "h-[30px] px-3" }],
    defaultVariants: { variant: "secondary", size: "default" },
  },
);

export function Button({
  className,
  variant,
  size,
  type = "button",
  ...props
}: ComponentProps<"button"> & VariantProps<typeof buttonVariants>) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
