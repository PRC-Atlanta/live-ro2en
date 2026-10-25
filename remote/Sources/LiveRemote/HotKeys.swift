import AppKit
import Carbon.HIToolbox

/// Global shortcuts that work while ProPresenter is in front, with no Accessibility
/// permission: Carbon's RegisterEventHotKey.
final class HotKeys {
    struct Key { let id: UInt32; let code: UInt32; let letter: String }
    /// The actions and their letters; the modifiers are a setting (Modifiers).
    static let keys: [String: Key] = [
        "pause":   Key(id: 1, code: UInt32(kVK_ANSI_H), letter: "H"),
        "hold":    Key(id: 2, code: UInt32(kVK_ANSI_G), letter: "G"),
        "blank":   Key(id: 3, code: UInt32(kVK_ANSI_K), letter: "K"),
        "retract": Key(id: 4, code: UInt32(kVK_ANSI_Z), letter: "Z"),
        "song":    Key(id: 5, code: UInt32(kVK_ANSI_S), letter: "S"),
        "names":   Key(id: 6, code: UInt32(kVK_ANSI_N), letter: "N"),
    ]

    enum Modifiers: String, CaseIterable {
        case ctrlOpt, ctrlShift, cmdOpt, off
        var carbon: UInt32 {
            switch self {
            case .ctrlOpt: return UInt32(controlKey | optionKey)
            case .ctrlShift: return UInt32(controlKey | shiftKey)
            case .cmdOpt: return UInt32(cmdKey | optionKey)
            case .off: return 0
            }
        }
        var symbol: String {
            switch self { case .ctrlOpt: return "⌃⌥"; case .ctrlShift: return "⌃⇧"; case .cmdOpt: return "⌘⌥"; case .off: return "" }
        }
        var title: String {
            switch self {
            case .ctrlOpt: return "Control-Option (⌃⌥)"
            case .ctrlShift: return "Control-Shift (⌃⇧)"
            case .cmdOpt: return "Command-Option (⌘⌥)"
            case .off: return "Off"
            }
        }
    }

    private var refs: [EventHotKeyRef] = []
    private var handler: EventHandlerRef?
    private var actions: [UInt32: String] = [:]
    var onPress: ((String) -> Void)?
    private(set) var failed: [String] = []         // taken by another app

    static func label(_ action: String, _ m: Modifiers) -> String {
        guard m != .off, let k = keys[action] else { return "" }
        return m.symbol + k.letter
    }

    func install(_ m: Modifiers) {
        uninstall()
        guard m != .off else { return }
        if handler == nil {
            var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
            let me = Unmanaged.passUnretained(self).toOpaque()
            InstallEventHandler(GetApplicationEventTarget(), { _, event, data in
                guard let event, let data else { return noErr }
                var hk = EventHotKeyID()
                GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                  nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
                let me = Unmanaged<HotKeys>.fromOpaque(data).takeUnretainedValue()
                if let a = me.actions[hk.id] { DispatchQueue.main.async { me.onPress?(a) } }
                return noErr
            }, 1, &spec, me, &handler)
        }
        failed = []
        for (action, k) in Self.keys {
            var ref: EventHotKeyRef?
            let id = EventHotKeyID(signature: OSType(0x4c54_5254), id: k.id)   // 'LTRT'
            if RegisterEventHotKey(k.code, m.carbon, id, GetApplicationEventTarget(), 0, &ref) == noErr, let ref {
                refs.append(ref)
                actions[k.id] = action
            } else {
                failed.append(action)
            }
        }
    }

    func uninstall() {
        refs.forEach { UnregisterEventHotKey($0) }
        refs = []
        actions = [:]
    }
}
