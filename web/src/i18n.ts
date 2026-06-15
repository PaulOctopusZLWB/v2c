export const STAGE_LABELS = ["设备", "导入", "转写", "审核", "观点", "发布"] as const;

export const t = {
  app: { title: "个人上下文节点", running: "运行中", idle: "空闲" },
  nav: { days: "日期", sessions: "会话", tasks: "任务", device: "设备" },
  day: { processing: "处理中", ready: "可审" },
  device: { detected: "已接入", known: "已知源", newAudio: "个新录音", import: "导入", refresh: "自动刷新", none: "未检测到设备" },
  run: { run: "开始", stop: "停止", retry: "重试", running: "运行中", idle: "空闲", tasks: "任务", failedOnly: "仅看失败", retryAllFailed: "重试全部失败" },
  review: { accepted: "接受", rejected: "拒绝", needs_fix: "存疑", pending_review: "待审", blocked: "受阻", acceptRemaining: "全部接受剩余" },
  speaker: { speaker: "发言人", assign: "指派发言人", newPerson: "新建人物", reassign: "改人" },
  viewpoint: { title: "观点", readOnly: "只读", confirmInObsidian: "确认/拒绝请在 Obsidian 完成", evidence: "证据", none: "暂无观点" },
  gate: { on: "仅消费已验收转写", off: "消费全部转写" },
  empty: {
    firstRun: "先在「设备」接入录音器并导入",
    firstRunHint: "导入后会自动转写，转写完成即可在此审核。",
    pickDay: "从左侧选择日期开始审核",
    pickDayHint: "左栏「日期」列出了已有录音的日子。",
    pickSession: "选择一个会话",
    pickSessionHint: "选中日期下的某个会话查看转写并逐段审核。"
  },
  error: { title: "操作失败" }
} as const;

export type ReviewKey = keyof typeof t.review;
