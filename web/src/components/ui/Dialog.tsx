import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Portal } from "./Portal";

/* 自研 Dialog(design handoff Phase 2)— 替代所有 window.prompt/confirm。
 * Promise 式命令 API:App 持有 useDialog(),把 confirm/promptText 作为 props 注入
 * 各面板(可测试:测试传 async () => true 桩即可,无全局状态)。
 * 键盘契约(与交互原型一致):
 *   危险确认 — esc 取消 / ⌘↵ 确认;普通 Enter 绝不触发破坏性操作。
 *   重命名   — esc 取消 / ↵ 保存 / ⇥ 采用 AI 建议(仅前向 Tab 且焦点在输入框时)。
 * Tab 在两种对话框内都被困在面板里(aria-modal 的焦点契约)。 */

export interface ConfirmOptions {
  title: string;
  /** 后果说明;不可逆词用 <strong> 包裹(样式渲染为 --err 色)。 */
  body?: ReactNode;
  /** 危险主按钮文案,默认「删除」。 */
  confirmLabel?: string;
}

export interface PromptOptions {
  title: string;
  initial?: string;
  placeholder?: string;
  /** AI 建议:显示「AI 建议:『…』 采用 ⇥」,Tab/点击将其填入输入框(不直接保存)。 */
  suggestion?: string;
  /** 主按钮文案,默认「保存」。 */
  saveLabel?: string;
}

export type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;
export type PromptFn = (opts: PromptOptions) => Promise<string | null>;

type DialogRequest =
  | ({ kind: "confirm"; id: number; resolve: (ok: boolean) => void } & ConfirmOptions)
  | ({ kind: "prompt"; id: number; resolve: (value: string | null) => void } & PromptOptions);

/** App 级 dialog 状态:request 交给 <DialogHost>,confirm/promptText 下发给面板。 */
export function useDialog() {
  const [request, setRequest] = useState<DialogRequest | null>(null);
  const requestRef = useRef<DialogRequest | null>(null);
  const seq = useRef(0);

  const open = useCallback((req: DialogRequest) => {
    // 若已有打开的对话框,先按「取消」语义结算它(不可能双开);
    // req.id 作为渲染 key,保证替换时重挂载、不复用旧输入状态。
    const prev = requestRef.current;
    if (prev) prev.resolve((prev.kind === "confirm" ? false : null) as never);
    requestRef.current = req;
    setRequest(req);
  }, []);

  const confirm: ConfirmFn = useCallback(
    (opts) => new Promise((resolve) => open({ kind: "confirm", id: ++seq.current, ...opts, resolve })),
    [open]
  );
  const promptText: PromptFn = useCallback(
    (opts) => new Promise((resolve) => open({ kind: "prompt", id: ++seq.current, ...opts, resolve })),
    [open]
  );

  const settle = useCallback((value: boolean | string | null) => {
    const req = requestRef.current;
    if (!req) return;
    requestRef.current = null;
    setRequest(null);
    req.resolve(value as never);
  }, []);

  return { request, confirm, promptText, settle };
}

/** 捕获阶段的窗口级键盘监听:对话框是模态的,处理过的键不再下传
 *  (App 的全局热键 / 面板热键都看不到),并跳过输入法组合中的按键。 */
function useDialogKeys(handle: (e: KeyboardEvent) => boolean) {
  const ref = useRef(handle);
  ref.current = handle;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // 中文输入法组合中的 Enter/Esc 属于 IME(Safari 提交组合时 keyCode 为 229)。
      if (e.isComposing || e.keyCode === 229) return;
      if (ref.current(e)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
}

/** 关闭后把焦点还给打开对话框前的元素(键盘流不断);
 *  若触发元素已随确认操作被卸载(如删除了它所在的行),则不抢焦点。 */
function useFocusReturn() {
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    return () => {
      if (prev && prev.isConnected) prev.focus?.();
    };
  }, []);
}

/** aria-modal 的焦点契约:Tab/Shift+Tab 在面板内循环,不逃逸到底层内容。 */
function trapTab(e: KeyboardEvent, panel: HTMLElement | null): boolean {
  if (!panel) return false;
  const focusables = Array.from(
    panel.querySelectorAll<HTMLElement>('button, input, select, textarea, [href], [tabindex]:not([tabindex="-1"])')
  ).filter((el) => !el.hasAttribute("disabled"));
  if (focusables.length === 0) return true;
  const idx = focusables.indexOf(document.activeElement as HTMLElement);
  const next = e.shiftKey
    ? focusables[(idx <= 0 ? focusables.length : idx) - 1]
    : focusables[(idx + 1) % focusables.length];
  next.focus();
  return true;
}

function ConfirmDialog({ req, onSettle }: { req: Extract<DialogRequest, { kind: "confirm" }>; onSettle: (v: boolean) => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const bodyId = useId();
  useFocusReturn();
  // 安全默认:焦点落在「取消」上,Space/Enter 激活的是取消而不是删除。
  useEffect(() => cancelRef.current?.focus(), []);
  useDialogKeys((e) => {
    if (e.key === "Escape") { onSettle(false); return true; }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { onSettle(true); return true; }
    // 吞掉普通 Enter:防止焦点恰好在危险按钮上时一击确认。
    if (e.key === "Enter") return true;
    if (e.key === "Tab") return trapTab(e, panelRef.current);
    return false;
  });
  return (
    <Portal>
      <div className="dialog-overlay" onMouseDown={() => onSettle(false)}>
        <div
          ref={panelRef}
          className="dialog-panel"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={req.body ? bodyId : undefined}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <h2 className="dialog-title" id={titleId}>{req.title}</h2>
          {req.body ? <p className="dialog-body" id={bodyId}>{req.body}</p> : null}
          <div className="dialog-footer">
            <button type="button" ref={cancelRef} onClick={() => onSettle(false)}>
              取消 <kbd className="key-hint">esc</kbd>
            </button>
            <button type="button" className="danger" onClick={() => onSettle(true)}>
              {req.confirmLabel ?? "删除"} <kbd className="key-hint">⌘↵</kbd>
            </button>
          </div>
        </div>
      </div>
    </Portal>
  );
}

function PromptDialog({ req, onSettle }: { req: Extract<DialogRequest, { kind: "prompt" }>; onSettle: (v: string | null) => void }) {
  const [value, setValue] = useState(req.initial ?? "");
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useFocusReturn();
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);
  useDialogKeys((e) => {
    if (e.key === "Escape") { onSettle(null); return true; }
    if (e.key === "Enter") { onSettle(inputRef.current?.value ?? value); return true; }
    if (e.key === "Tab") {
      // 前向 Tab 且焦点在输入框:采用 AI 建议(设计稿 ⇥);其余情况按模态焦点循环。
      if (!e.shiftKey && req.suggestion && document.activeElement === inputRef.current) {
        setValue(req.suggestion);
        return true;
      }
      return trapTab(e, panelRef.current);
    }
    return false;
  });
  return (
    <Portal>
      <div className="dialog-overlay" onMouseDown={() => onSettle(null)}>
        <div
          ref={panelRef}
          className="dialog-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <h2 className="dialog-title" id={titleId}>{req.title}</h2>
          <input
            ref={inputRef}
            className="dialog-input"
            type="text"
            aria-label={req.title}
            placeholder={req.placeholder}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          {req.suggestion ? (
            <div className="dialog-suggestion">
              AI 建议:「{req.suggestion}」
              <button type="button" className="dialog-suggestion-adopt" onClick={() => { setValue(req.suggestion!); inputRef.current?.focus(); }}>
                采用 <kbd className="key-hint">⇥</kbd>
              </button>
            </div>
          ) : null}
          <div className="dialog-footer">
            <button type="button" onClick={() => onSettle(null)}>
              取消 <kbd className="key-hint">esc</kbd>
            </button>
            <button type="button" className="primary" onClick={() => onSettle(value)}>
              {req.saveLabel ?? "保存"} <kbd className="key-hint">↵</kbd>
            </button>
          </div>
        </div>
      </div>
    </Portal>
  );
}

/** 渲染当前对话框(portal 到 #overlay-root,--z-modal-top:可盖在 ⌘K 之上)。
 *  key=request.id:请求被替换时强制重挂载,不复用旧输入状态。 */
export function DialogHost({ request, onSettle }: { request: DialogRequest | null; onSettle: (v: boolean | string | null) => void }) {
  if (!request) return null;
  return request.kind === "confirm" ? (
    <ConfirmDialog key={request.id} req={request} onSettle={onSettle} />
  ) : (
    <PromptDialog key={request.id} req={request} onSettle={onSettle} />
  );
}
