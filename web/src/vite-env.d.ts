/// <reference types="vite/client" />

// Minimal ambient declarations for the Node builtins used by the token-foundation
// test (ThemeToggle.test.tsx reads theme.css from disk). The project intentionally
// does not depend on @types/node; these narrow shims keep `tsc -b` green without it.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
declare module "node:path" {
  export function resolve(...paths: string[]): string;
}
declare const process: { cwd(): string };
