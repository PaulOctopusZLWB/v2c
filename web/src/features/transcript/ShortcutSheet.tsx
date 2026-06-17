/** A small keyboard-shortcut help overlay for the 审核 (review) tab. Toggled by `?`;
 *  closed by Esc or clicking the dimmed backdrop. Mirrors the cmdk overlay styling. */
const SHORTCUTS: { keys: string; label: string }[] = [
  { keys: "j / ↓", label: "聚焦下一段" },
  { keys: "k / ↑", label: "聚焦上一段" },
  { keys: "a", label: "接受当前段" },
  { keys: "r", label: "拒绝当前段" },
  { keys: "f", label: "标记存疑" },
  { keys: "space", label: "播放当前段" },
  { keys: "?", label: "显示 / 隐藏帮助" }
];

export function ShortcutSheet({ onClose }: { onClose: () => void }) {
  return (
    <div className="shortcut-overlay" onMouseDown={onClose}>
      <div
        className="shortcut-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="键盘快捷键"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3>键盘快捷键</h3>
        <dl className="shortcut-list">
          {SHORTCUTS.map((s) => (
            <div className="shortcut-row" key={s.keys}>
              <dt>
                <kbd>{s.keys}</kbd>
              </dt>
              <dd>{s.label}</dd>
            </div>
          ))}
        </dl>
        <p className="dim shortcut-foot">按 Esc 或点击空白处关闭</p>
      </div>
    </div>
  );
}
