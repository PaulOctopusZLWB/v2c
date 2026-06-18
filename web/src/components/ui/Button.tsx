import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon } from "../Icon";

type ButtonVariant = "default" | "primary" | "ghost" | "icon";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: string;
  busy?: boolean;
  children?: ReactNode;
}

function classForVariant(variant: ButtonVariant, className?: string): string {
  const classes = [];
  if (variant === "primary") classes.push("primary");
  if (variant === "ghost") classes.push("ghost");
  if (variant === "icon") classes.push("icon-btn");
  if (className) classes.push(className);
  return classes.join(" ");
}

export function Button({
  variant = "default",
  icon,
  busy = false,
  children,
  className,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      className={classForVariant(variant, className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy ? <span className="spinner" aria-hidden /> : icon ? <Icon name={icon} /> : null}
      {children}
    </button>
  );
}
