import ServiceManagement
import WebKit

/// "Open when the Mac starts" (Settings → Basic): the app itself as a login item (SMAppService).
/// macOS keeps the switch, not this app: System Settings → General → Login Items shows the same
/// entry, and turning it off there is what the page reads next time.
enum LoginItem {
    /// on, off, or approval (registered, but waiting for the user's OK in Login Items)
    static var state: String {
        switch SMAppService.mainApp.status {
        case .enabled: return "on"
        case .requiresApproval: return "approval"
        default: return "off"
        }
    }

    static func set(_ on: Bool) throws {
        if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        if state == "approval" { SMAppService.openSystemSettingsLoginItems() }
    }
}

/// The page's side (screens/settings/index.js): `await webkit.messageHandlers.loginItem.postMessage({})`
/// answers {state, error}; `postMessage({on: true|false})` changes it first.
final class LoginItemBridge: NSObject, WKScriptMessageHandlerWithReply {
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        var failed: String?
        if message.frameInfo.isMainFrame, let on = (message.body as? [String: Any])?["on"] as? Bool {
            do { try LoginItem.set(on) } catch { failed = error.localizedDescription }
            ShellLog.write("login item \(on ? "on" : "off"): \(failed ?? LoginItem.state)")
        }
        replyHandler(["state": LoginItem.state, "error": failed as Any? ?? NSNull()], nil)
    }
}
