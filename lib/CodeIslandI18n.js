.pragma library

var translations = {
    zh_CN: {
        "This widget stays as a projection of the Linux skeleton daemon. Leave the socket blank to follow the daemon's default runtime path.": "这个组件会作为 Linux skeleton 守护进程的投影视图运行。socket 留空时会使用守护进程默认运行路径。",
        "Language": "语言",
        "Language used by CodeIsland plugin settings.": "CodeIsland 插件设置页使用的语言。",
        "Follow system": "跟随系统",
        "English": "英语",
        "Chinese (Simplified)": "简体中文",
        "Connection": "连接",
        "Daemon socket path": "守护进程 socket 路径",
        "Default: %1": "默认：%1",
        "Appearance": "外观",
        "Default list tab": "默认列表标签",
        "Which tab is selected when the popout opens without an explicit mode.": "未指定打开模式时，弹出窗口默认选中的标签。",
        "All": "全部",
        "Finished": "已完成",
        "Active": "进行中",
        "Focus": "聚焦",
        "Review": "审核",
        "Reply": "回复",
        "Live": "实时",
        "Recent": "最近",
        "Paused": "已暂停",
        "Offline": "离线",
        "Quiet": "空闲",
        "Running": "运行中",
        "Approval": "待审批",
        "Question": "待回答",
        "Done": "完成",
        "Failed": "失败",
        "Cancelled": "已取消",
        "Idle": "空闲",
        "Waiting for daemon": "正在等待守护进程",
        "No active sessions": "没有进行中的会话",
        "No finished sessions": "没有已完成的会话",
        "No sessions": "没有会话",
        "%1 active": "%1 个进行中",
        "%1 finished": "%1 个已完成",
        "%1 sessions": "%1 个会话",
        "Other": "其他",
        "Action needed": "需要操作",
        "Approve": "批准",
        "Deny": "拒绝",
        "Send": "发送",
        "Decline": "拒绝",
        "Open": "打开",
        "List": "列表",
        "answer": "回答",
        "Pop out on completion": "完成后弹出",
        "Automatically open the focused card when an agent tool finishes. Approval and question cards still pop out.": "agent 工具完成时自动打开聚焦卡片。审批和问题卡片仍会自动弹出。",
        "Font family": "字体",
        "Leave blank to use %1.": "留空则使用 %1。",
        "Board width": "面板宽度",
        "Popout width used by the session board.": "会话面板使用的弹出窗口宽度。",
        "Board height": "面板高度",
        "Popout height used by the grouped session board.": "分组会话面板使用的弹出窗口高度。",
        "Focused height": "聚焦高度",
        "Popout height for approval and completion cards.": "审批和完成卡片使用的弹出窗口高度。",
        "Question height": "问题高度",
        "Popout height when the session is waiting for an answer.": "会话等待回答时使用的弹出窗口高度。",
        "Session card height": "会话卡片高度",
        "Height of each session row in the board.": "面板中每个会话行的高度。",
        "Session card spacing": "会话卡片间距",
        "Vertical space between session rows.": "会话行之间的垂直间距。",
        "Card roundness": "卡片圆角",
        "Corner radius for session and focused cards.": "会话卡片和聚焦卡片的圆角半径。",
        "Button roundness": "按钮圆角",
        "Corner radius for action buttons.": "操作按钮的圆角半径。",
        "Outline strength": "描边强度",
        "Scales subtle card, divider, and input outlines.": "缩放卡片、分割线和输入框的细微描边。",
        "Bar dot size": "栏位圆点大小",
        "Size of each session dot in the DMS bar widget.": "DMS 栏组件中每个会话圆点的大小。",
        "Bar dot spacing": "栏位圆点间距",
        "Spacing between session dots in the DMS bar widget.": "DMS 栏组件中会话圆点之间的间距。",
        "Glass opacity scale": "玻璃透明度缩放",
        "Scales the widget's theme-derived translucent layers.": "缩放组件中由系统主题派生的半透明层。",
        "Details": "细节",
        "Show provider groups": "显示提供商分组",
        "Show provider headings such as Codex and OpenCode above grouped cards.": "在分组卡片上方显示 Codex、OpenCode 等提供商标题。",
        "Show time chip": "显示时间标签",
        "Show the relative age chip on session cards.": "在会话卡片上显示相对时间标签。",
        "Show terminal chip": "显示终端标签",
        "Show the terminal/app chip such as WezTerm on session cards.": "在会话卡片上显示 WezTerm 等终端或应用标签。",
        "Motion": "动效",
        "Breathing speed": "呼吸速度",
        "Duration of each breathing half-cycle for running sessions.": "运行中会话每个呼吸半周期的时长。",
        "Breathing brightness": "呼吸亮度",
        "Maximum running logo and dot opacity. This is capped at 50 percent.": "运行中 logo 和圆点的最高不透明度，上限为 50%。"
    }
};

function normalizedLanguage(value) {
    if (value === "system" || value === "en" || value === "zh_CN")
        return value;

    return "system";
}

function localeKey(value) {
    var locale = typeof value === "string" ? value.replace("-", "_") : "";
    if (locale.indexOf("zh") === 0)
        return "zh_CN";

    return "en";
}

function resolveLocale(language, systemLocale) {
    var normalized = normalizedLanguage(language);
    if (normalized === "system")
        return localeKey(systemLocale);

    return localeKey(normalized);
}

function tr(term, locale) {
    var key = localeKey(locale);
    if (key === "en")
        return term;

    var catalog = translations[key] || {};
    return catalog[term] || term;
}

function format(term, locale, values) {
    var text = tr(term, locale);
    var replacements = Array.isArray(values) ? values : [];
    for (var index = 0; index < replacements.length; index += 1)
        text = text.replace("%" + (index + 1), replacements[index]);

    return text;
}

function statusLabel(status, locale) {
    switch (status) {
    case "running":
        return tr("Running", locale);
    case "waiting_approval":
        return tr("Approval", locale);
    case "waiting_answer":
        return tr("Question", locale);
    case "completed":
        return tr("Done", locale);
    case "failed":
        return tr("Failed", locale);
    case "cancelled":
        return tr("Cancelled", locale);
    default:
        return tr("Idle", locale);
    }
}

function viewModeLabel(mode, locale) {
    if (mode === "finished")
        return tr("Finished", locale);

    if (mode === "active")
        return tr("Active", locale);

    return tr("All", locale);
}

function tabLabel(mode, locale) {
    var label = viewModeLabel(mode, locale);
    return localeKey(locale) === "en" ? label.toUpperCase() : label;
}
