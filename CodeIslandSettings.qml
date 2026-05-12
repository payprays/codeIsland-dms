import QtQuick
import Quickshell
import qs.Common
import qs.Modules.Plugins
import qs.Widgets

import "./lib/CodeIslandI18n.js" as LocalI18n
import "./lib/CodeIslandProtocol.js" as Protocol
import "./lib/CodeIslandStyle.js" as Style

PluginSettings {
    id: root

    pluginId: "codeIsland"

    property string defaultSocketPath: Protocol.defaultSocketPath(Quickshell.env("XDG_RUNTIME_DIR"), Quickshell.env("UID"))
    property string language: Style.visualDefaults.language
    readonly property string activeLocale: LocalI18n.resolveLocale(root.language, I18n.currentLocale || Qt.locale().name)

    function tr(term) {
        return LocalI18n.tr(term, root.activeLocale);
    }

    function trf(term, values) {
        return LocalI18n.format(term, root.activeLocale, values);
    }

    StyledText {
        width: parent.width
        color: Theme.surfaceText
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Font.Bold
        text: "CodeIsland"
    }

    StyledText {
        width: parent.width
        color: Theme.surfaceVariantText
        font.pixelSize: Theme.fontSizeSmall
        text: root.tr("This widget stays as a projection of the Linux skeleton daemon. Leave the socket blank to follow the daemon's default runtime path.")
        wrapMode: Text.WordWrap
    }

    SelectionSetting {
        id: languageSetting

        settingKey: "language"
        label: root.tr("Language")
        description: root.tr("Language used by CodeIsland plugin settings.")
        defaultValue: Style.visualDefaults.language
        options: [
            { label: root.tr("Follow system"), value: "system" },
            { label: root.tr("English"), value: "en" },
            { label: root.tr("Chinese (Simplified)"), value: "zh_CN" }
        ]
        onValueChanged: root.language = value
    }

    StyledText {
        width: parent.width
        color: Theme.surfaceText
        font.pixelSize: Theme.fontSizeMedium
        font.weight: Font.Bold
        text: root.tr("Connection")
    }

    StringSetting {
        settingKey: "socketPath"
        label: root.tr("Daemon socket path")
        description: root.trf("Default: %1", [root.defaultSocketPath])
        placeholder: root.defaultSocketPath
        defaultValue: ""
    }

    StyledText {
        width: parent.width
        color: Theme.surfaceText
        font.pixelSize: Theme.fontSizeMedium
        font.weight: Font.Bold
        text: root.tr("Appearance")
    }

    SelectionSetting {
        settingKey: "defaultViewMode"
        label: root.tr("Default list tab")
        description: root.tr("Which tab is selected when the popout opens without an explicit mode.")
        defaultValue: Style.visualDefaults.defaultViewMode
        options: [
            { label: root.tr("All"), value: "all" },
            { label: root.tr("Finished"), value: "finished" },
            { label: root.tr("Active"), value: "active" }
        ]
    }

    ToggleSetting {
        settingKey: "revealOnCompletion"
        label: root.tr("Pop out on completion")
        description: root.tr("Automatically open the focused card when an agent tool finishes. Approval and question cards still pop out.")
        defaultValue: Style.visualDefaults.revealOnCompletion
    }

    StringSetting {
        settingKey: "fontFamily"
        label: root.tr("Font family")
        description: root.trf("Leave blank to use %1.", [Style.visualDefaults.fontFamily])
        placeholder: Style.visualDefaults.fontFamily
        defaultValue: ""
    }

    SliderSetting {
        settingKey: "boardWidth"
        label: root.tr("Board width")
        description: root.tr("Popout width used by the session board.")
        defaultValue: Style.visualDefaults.boardWidth
        minimum: 620
        maximum: 980
        unit: "px"
    }

    SliderSetting {
        settingKey: "boardHeight"
        label: root.tr("Board height")
        description: root.tr("Popout height used by the grouped session board.")
        defaultValue: Style.visualDefaults.boardHeight
        minimum: 420
        maximum: 780
        unit: "px"
    }

    SliderSetting {
        settingKey: "boardFocusedHeight"
        label: root.tr("Focused height")
        description: root.tr("Popout height for approval and completion cards.")
        defaultValue: Style.visualDefaults.boardFocusedHeight
        minimum: 360
        maximum: 580
        unit: "px"
    }

    SliderSetting {
        settingKey: "boardQuestionHeight"
        label: root.tr("Question height")
        description: root.tr("Popout height when the session is waiting for an answer.")
        defaultValue: Style.visualDefaults.boardQuestionHeight
        minimum: 390
        maximum: 640
        unit: "px"
    }

    SliderSetting {
        settingKey: "boardCardHeight"
        label: root.tr("Session card height")
        description: root.tr("Height of each session row in the board.")
        defaultValue: Style.visualDefaults.boardCardHeight
        minimum: 82
        maximum: 124
        unit: "px"
    }

    SliderSetting {
        settingKey: "boardCardSpacing"
        label: root.tr("Session card spacing")
        description: root.tr("Vertical space between session rows.")
        defaultValue: Style.visualDefaults.boardCardSpacing
        minimum: 6
        maximum: 18
        unit: "px"
    }

    SliderSetting {
        settingKey: "boardCardRadius"
        label: root.tr("Card roundness")
        description: root.tr("Corner radius for session and focused cards.")
        defaultValue: Style.visualDefaults.boardCardRadius
        minimum: 4
        maximum: 18
        unit: "px"
    }

    SliderSetting {
        settingKey: "buttonRadius"
        label: root.tr("Button roundness")
        description: root.tr("Corner radius for action buttons.")
        defaultValue: Style.visualDefaults.buttonRadius
        minimum: 4
        maximum: 18
        unit: "px"
    }

    SliderSetting {
        settingKey: "outlineAlpha"
        label: root.tr("Outline strength")
        description: root.tr("Scales subtle card, divider, and input outlines.")
        defaultValue: Style.visualDefaults.outlineAlpha
        minimum: 40
        maximum: 180
        unit: "%"
    }

    SliderSetting {
        settingKey: "sessionDotSize"
        label: root.tr("Bar dot size")
        description: root.tr("Size of each session dot in the DMS bar widget.")
        defaultValue: Style.visualDefaults.sessionDotSize
        minimum: 4
        maximum: 10
        unit: "px"
    }

    SliderSetting {
        settingKey: "sessionDotSpacing"
        label: root.tr("Bar dot spacing")
        description: root.tr("Spacing between session dots in the DMS bar widget.")
        defaultValue: Style.visualDefaults.sessionDotSpacing
        minimum: 3
        maximum: 10
        unit: "px"
    }

    SliderSetting {
        settingKey: "glassAlpha"
        label: root.tr("Glass opacity scale")
        description: root.tr("Scales the widget's theme-derived translucent layers.")
        defaultValue: Style.visualDefaults.glassAlpha
        minimum: 60
        maximum: 140
        unit: "%"
    }

    StyledText {
        width: parent.width
        color: Theme.surfaceText
        font.pixelSize: Theme.fontSizeMedium
        font.weight: Font.Bold
        text: root.tr("Details")
    }

    ToggleSetting {
        settingKey: "showGroupHeaders"
        label: root.tr("Show provider groups")
        description: root.tr("Show provider headings such as Codex and OpenCode above grouped cards.")
        defaultValue: Style.visualDefaults.showGroupHeaders
    }

    ToggleSetting {
        settingKey: "showTimeChip"
        label: root.tr("Show time chip")
        description: root.tr("Show the relative age chip on session cards.")
        defaultValue: Style.visualDefaults.showTimeChip
    }

    ToggleSetting {
        settingKey: "showAppChip"
        label: root.tr("Show terminal chip")
        description: root.tr("Show the terminal/app chip such as WezTerm on session cards.")
        defaultValue: Style.visualDefaults.showAppChip
    }

    StyledText {
        width: parent.width
        color: Theme.surfaceText
        font.pixelSize: Theme.fontSizeMedium
        font.weight: Font.Bold
        text: root.tr("Motion")
    }

    SliderSetting {
        settingKey: "breatheDuration"
        label: root.tr("Breathing speed")
        description: root.tr("Duration of each breathing half-cycle for running sessions.")
        defaultValue: Style.visualDefaults.breatheDuration
        minimum: 360
        maximum: 1800
        unit: "ms"
    }

    SliderSetting {
        settingKey: "breatheMaxOpacity"
        label: root.tr("Breathing brightness")
        description: root.tr("Maximum running logo and dot opacity. This is capped at 50 percent.")
        defaultValue: Style.visualDefaults.breatheMaxOpacity
        minimum: 20
        maximum: 50
        unit: "%"
    }
}
