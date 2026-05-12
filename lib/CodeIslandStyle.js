.pragma library

var spacing = {
    edge: 2,
    xs: 6,
    s: 10,
    m: 14,
    l: 18,
    xl: 24,
};

var size = {
    border: 1,
    orb: 16,
    pillWidth: 320,
    pillHeight: 58,
    barOrb: 8,
    barPillMinWidth: 22,
    barPillMaxWidth: 126,
    barPillHeight: 26,
    barMarkWidth: 22,
    barMarkHeight: 16,
    barSessionDot: 6,
    barSessionDotSpacing: 5,
    barChipHeight: 20,
    barChipMinWidth: 56,
    barTextMaxWidth: 68,
    barCueWidth: 12,
    barCueHeight: 8,
    barSeed: 4,
    chipHeight: 24,
    chipMinWidth: 76,
    buttonHeight: 34,
    buttonMinWidth: 96,
    popoutWidth: 392,
    popoutHeight: 356,
    popoutIdleHeight: 264,
    boardWidth: 772,
    boardHeight: 618,
    boardFocusedHeight: 430,
    boardQuestionHeight: 464,
    boardRailWidth: 0,
    boardTopHeight: 34,
    boardCardHeight: 94,
    boardCardSpacing: 10,
    boardCardMinHeight: 94,
    boardGlyphWidth: 52,
    boardGlyphBadge: 38,
    boardGlyphIcon: 22,
    boardRightRailWidth: 138,
    boardChipHeight: 24,
    boardChipMinWidth: 48,
    boardAppChipMinWidth: 96,
    inputHeight: 40,
    cardMinHeight: 108,
    interactionIdleHeight: 96,
    interactionExpandedOffset: 88,
    verticalPillWidth: 70,
    verticalPillHeight: 66,
};

var radius = {
    shell: 29,
    inner: 26,
    barShell: 13,
    barCore: 12,
    chip: 14,
    button: 14,
    card: 20,
    board: 24,
    boardCard: 10,
    input: 16,
    dot: 8,
};

var font = {
    family: "Maple Mono NF CN",
    eyebrow: 11,
    title: 14,
    detail: 12,
    chip: 11,
    barTitle: 10,
    barDetail: 10,
    barChip: 10,
    body: 13,
    button: 12,
    boardTitle: 16,
    boardBody: 14,
    boardMeta: 12,
    boardChip: 12,
};

var motion = {
    pulseHalf: 520,
    breathe: 820,
    breatheMaxOpacity: 0.5,
};

var visualDefaults = {
    fontFamily: font.family,
    boardWidth: size.boardWidth,
    boardHeight: size.boardHeight,
    boardFocusedHeight: size.boardFocusedHeight,
    boardQuestionHeight: size.boardQuestionHeight,
    boardCardHeight: size.boardCardHeight,
    boardCardSpacing: size.boardCardSpacing,
    boardCardRadius: radius.boardCard,
    buttonRadius: radius.button,
    outlineAlpha: 100,
    defaultViewMode: "all",
    language: "system",
    revealOnCompletion: true,
    showGroupHeaders: true,
    showTimeChip: true,
    showAppChip: true,
    sessionDotSize: size.barSessionDot,
    sessionDotSpacing: size.barSessionDotSpacing,
    breatheDuration: motion.breathe,
    breatheMaxOpacity: Math.round(motion.breatheMaxOpacity * 100),
    glassAlpha: 100,
};

var palette = {
    shellOuter: "#090b10",
    shellInner: "#121722",
    shellSheen: "#232b38",
    shellOutline: "#2e3645",
    barShell: "#0a0d13",
    barCore: "#111722",
    barOutline: "#30394a",
    textStrong: "#f4f7fb",
    textMuted: "#bac4d8",
    textDim: "#9aa7bd",
    idleBg: "#131924",
    runningBg: "#0f1b2a",
    waitingBg: "#26180d",
    successBg: "#112118",
    failedBg: "#2a1016",
    idleAccent: "#8a94a8",
    runningAccent: "#56b9ff",
    waitingAccent: "#ffba58",
    successAccent: "#69d88f",
    failedAccent: "#ff6b79",
    chipText: "#081019",
    buttonNeutral: "#1a2130",
    buttonNeutralText: "#edf2fb",
    approveFill: "#69d88f",
    denyFill: "#ff6b79",
    answerFill: "#56b9ff",
    inputFill: "#0e131c",
    inputOutline: "#3b4558",
    boardOuter: "#000000",
    boardChrome: "#070808",
    boardCard: "#101112",
    boardCardHot: "#141719",
    boardCardBorder: "#171a1d",
    boardDivider: "#1c2024",
    boardTab: "#16191d",
    boardTabActive: "#20262b",
    boardRail: "#41a9ee",
    boardRailDeep: "#1f75b7",
    lineUser: "#41e26f",
    lineAgent: "#ff8a54",
    lineMuted: "#b2bac7",
    providerClaude: "#ff8a54",
    providerCodex: "#7e89ff",
    providerGemini: "#a675ff",
    providerOpenCode: "#36d27b",
    providerCursor: "#f4f7fb",
    providerOther: "#8a94a8",
};

function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
}

function numberSetting(source, key, fallback, minimum, maximum) {
    if (!source || typeof source !== "object" || source[key] === undefined || source[key] === null)
        return fallback;

    var value = Number(source[key]);
    if (!isFinite(value))
        return fallback;

    return clamp(value, minimum, maximum);
}

function stringSetting(source, key, fallback) {
    if (!source || typeof source !== "object" || typeof source[key] !== "string")
        return fallback;

    var value = source[key].trim();
    return value.length ? value : fallback;
}

function boolSetting(source, key, fallback) {
    if (!source || typeof source !== "object" || source[key] === undefined || source[key] === null)
        return fallback;

    if (typeof source[key] === "boolean")
        return source[key];

    if (typeof source[key] === "string") {
        var value = source[key].trim().toLowerCase();
        if (value === "true")
            return true;
        if (value === "false")
            return false;
    }

    return !!source[key];
}

function visualConfig(source) {
    return {
        fontFamily: stringSetting(source, "fontFamily", visualDefaults.fontFamily),
        boardWidth: numberSetting(source, "boardWidth", visualDefaults.boardWidth, 620, 980),
        boardHeight: numberSetting(source, "boardHeight", visualDefaults.boardHeight, 420, 780),
        boardFocusedHeight: numberSetting(source, "boardFocusedHeight", visualDefaults.boardFocusedHeight, 360, 580),
        boardQuestionHeight: numberSetting(source, "boardQuestionHeight", visualDefaults.boardQuestionHeight, 390, 640),
        boardCardHeight: numberSetting(source, "boardCardHeight", visualDefaults.boardCardHeight, 82, 124),
        boardCardSpacing: numberSetting(source, "boardCardSpacing", visualDefaults.boardCardSpacing, 6, 18),
        boardCardRadius: numberSetting(source, "boardCardRadius", visualDefaults.boardCardRadius, 4, 18),
        buttonRadius: numberSetting(source, "buttonRadius", visualDefaults.buttonRadius, 4, 18),
        outlineAlphaScale: numberSetting(source, "outlineAlpha", visualDefaults.outlineAlpha, 40, 180) / 100,
        defaultViewMode: stringSetting(source, "defaultViewMode", visualDefaults.defaultViewMode),
        language: stringSetting(source, "language", visualDefaults.language),
        revealOnCompletion: boolSetting(source, "revealOnCompletion", visualDefaults.revealOnCompletion),
        showGroupHeaders: boolSetting(source, "showGroupHeaders", visualDefaults.showGroupHeaders),
        showTimeChip: boolSetting(source, "showTimeChip", visualDefaults.showTimeChip),
        showAppChip: boolSetting(source, "showAppChip", visualDefaults.showAppChip),
        sessionDotSize: numberSetting(source, "sessionDotSize", visualDefaults.sessionDotSize, 4, 10),
        sessionDotSpacing: numberSetting(source, "sessionDotSpacing", visualDefaults.sessionDotSpacing, 3, 10),
        breatheDuration: numberSetting(source, "breatheDuration", visualDefaults.breatheDuration, 360, 1800),
        breatheMaxOpacity: numberSetting(source, "breatheMaxOpacity", visualDefaults.breatheMaxOpacity, 20, 50) / 100,
        glassAlphaScale: numberSetting(source, "glassAlpha", visualDefaults.glassAlpha, 60, 140) / 100,
    };
}

function sizeValue(config, key) {
    if (config && typeof config === "object") {
        if (key === "barSessionDot")
            return config.sessionDotSize;
        if (key === "barSessionDotSpacing")
            return config.sessionDotSpacing;
        if (key === "boardCardSpacing")
            return config.boardCardSpacing;
        if (config[key] !== undefined && config[key] !== null)
            return config[key];
    }

    return size[key];
}

function radiusValue(config, key) {
    if (config && typeof config === "object") {
        if (key === "boardCard")
            return config.boardCardRadius;
        if (key === "button")
            return config.buttonRadius;
    }

    return radius[key];
}

function boolValue(config, key) {
    if (config && typeof config === "object" && config[key] !== undefined && config[key] !== null)
        return !!config[key];

    if (visualDefaults[key] !== undefined && visualDefaults[key] !== null)
        return !!visualDefaults[key];

    return false;
}

function motionValue(config, key) {
    if (config && typeof config === "object") {
        if (key === "breathe")
            return config.breatheDuration;
        if (key === "breatheMaxOpacity")
            return config.breatheMaxOpacity;
    }

    return motion[key];
}

function fontFamily(config) {
    return config && typeof config === "object" && config.fontFamily ? config.fontFamily : font.family;
}

function scaledAlpha(config, opacity) {
    var scale = config && typeof config === "object" && config.glassAlphaScale ? config.glassAlphaScale : 1;
    return clamp(opacity * scale, 0, 1);
}

function scaledBorderAlpha(config, opacity) {
    var scale = config && typeof config === "object" && config.outlineAlphaScale ? config.outlineAlphaScale : 1;
    return clamp(opacity * scale, 0, 1);
}

function labelFor(status) {
    switch (status) {
    case "running":
        return "Running";
    case "waiting_approval":
        return "Approval";
    case "waiting_answer":
        return "Question";
    case "completed":
        return "Done";
    case "failed":
        return "Failed";
    case "cancelled":
        return "Cancelled";
    default:
        return "Idle";
    }
}

function paletteColor(theme, key) {
    if (theme && typeof theme === "object" && theme[key] !== undefined && theme[key] !== null)
        return theme[key];

    return palette[key];
}

function accentFor(status, theme) {
    switch (status) {
    case "running":
        return paletteColor(theme, "runningAccent");
    case "waiting_approval":
    case "waiting_answer":
        return paletteColor(theme, "waitingAccent");
    case "completed":
        return paletteColor(theme, "successAccent");
    case "failed":
    case "cancelled":
        return paletteColor(theme, "failedAccent");
    default:
        return paletteColor(theme, "idleAccent");
    }
}

function backgroundFor(status, theme) {
    switch (status) {
    case "running":
        return paletteColor(theme, "runningBg");
    case "waiting_approval":
    case "waiting_answer":
        return paletteColor(theme, "waitingBg");
    case "completed":
        return paletteColor(theme, "successBg");
    case "failed":
    case "cancelled":
        return paletteColor(theme, "failedBg");
    default:
        return paletteColor(theme, "idleBg");
    }
}

function glowOpacityFor(status) {
    switch (status) {
    case "running":
        return 0.24;
    case "waiting_approval":
    case "waiting_answer":
        return 0.28;
    case "completed":
        return 0.22;
    case "failed":
    case "cancelled":
        return 0.24;
    default:
        return 0.14;
    }
}

function providerAccentFor(providerKey, theme) {
    switch (providerKey) {
    case "claude":
        return paletteColor(theme, "providerClaude");
    case "codex":
        return paletteColor(theme, "providerCodex");
    case "gemini":
        return paletteColor(theme, "providerGemini");
    case "opencode":
        return paletteColor(theme, "providerOpenCode");
    case "cursor":
        return paletteColor(theme, "providerCursor");
    default:
        return paletteColor(theme, "providerOther");
    }
}

function alpha(colorValue, opacity) {
    if (!colorValue) {
        return colorValue;
    }

    if (typeof colorValue === "string") {
        var normalized = colorValue.charAt(0) === "#" ? colorValue.substring(1) : colorValue;
        if (normalized.length === 6) {
            var red = parseInt(normalized.substring(0, 2), 16) / 255;
            var green = parseInt(normalized.substring(2, 4), 16) / 255;
            var blue = parseInt(normalized.substring(4, 6), 16) / 255;
            return Qt.rgba(red, green, blue, opacity);
        }
        return colorValue;
    }

    return Qt.rgba(colorValue.r, colorValue.g, colorValue.b, opacity);
}
