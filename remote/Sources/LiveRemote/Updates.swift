import AppKit
import Sparkle

/// Software updates: Sparkle 2 (docs/release.md "Updates"). The feed (SUFeedURL, appcast-remote.xml
/// on the newest GitHub release) and the public key (SUPublicEDKey) are in Info.plist. Sparkle
/// checks on its own about once a day, downloads a new version quietly and installs it when Live
/// Remote quits. `defaults write org.livero2en.LiveRemote LiveNoUpdates -bool YES` turns it off
/// (e.g. for a build run from remote/build/).
final class Updates: NSObject, SPUUpdaterDelegate {
    private var controller: SPUStandardUpdaterController?

    func start() {
        let info = Bundle.main.infoDictionary ?? [:]
        guard !((info["SUFeedURL"] as? String) ?? "").isEmpty,
              !((info["SUPublicEDKey"] as? String) ?? "").isEmpty,
              !UserDefaults.standard.bool(forKey: "LiveNoUpdates") else { return }
        controller = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: self,
                                                  userDriverDelegate: nil)
        if Self.testFeed != nil { controller?.updater.checkForUpdatesInBackground() }
    }

    /// `-LiveUpdateFeed <url>` on the command line (or as a user default): use that appcast
    /// instead of SUFeedURL and check at once. For trying a release's appcast before it is
    /// published (docs/release.md "Updates"). The public key still has to match.
    static var testFeed: String? {
        let f = UserDefaults.standard.string(forKey: "LiveUpdateFeed") ?? ""
        return f.isEmpty ? nil : f
    }

    func feedURLString(for updater: SPUUpdater) -> String? { Self.testFeed }

    var isOn: Bool { controller != nil }

    func check() { controller?.checkForUpdates(nil) }
}
