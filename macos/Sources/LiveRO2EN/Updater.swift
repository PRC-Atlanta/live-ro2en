import AppKit
import Sparkle

/// Software updates: Sparkle 2 (docs/release.md "Updates").
///
/// The feed (SUFeedURL) and the public key (SUPublicEDKey) are in Info.plist. The appcast is
/// attached to the newest GitHub release; Sparkle checks on its own about once a day, downloads
/// a new version quietly and installs it when the app quits (SUAutomaticallyUpdate).
///
/// Rules (APP_PLAN §1.1 "Updates"):
/// - never check for or download an update while live (`updaterMayCheck` is false then);
/// - install on quit, never in the middle of a service;
/// - model updates are a prompt at launch, never during a service.
protocol Updater: AnyObject {
    var isConfigured: Bool { get }
    func checkForUpdates()
    /// Called whenever live changes; scheduled checks wait while live.
    func setLive(_ live: Bool)
}

/// The updater to use: Sparkle in a packaged app (one that carries its runtime) with a feed and a
/// key; the stub in a dev build (macos/build.sh), which must not replace itself with a release.
/// `defaults write org.livero2en.LiveRO2EN LiveNoUpdates -bool YES` turns updates off.
func makeUpdater() -> Updater {
    let info = Bundle.main.infoDictionary ?? [:]
    let feed = (info["SUFeedURL"] as? String) ?? ""
    let key = (info["SUPublicEDKey"] as? String) ?? ""
    let packaged = FileManager.default.fileExists(
        atPath: Bundle.main.resourceURL?.appendingPathComponent("runtime/bin/python3.12").path ?? "")
    if !packaged || feed.isEmpty || key.isEmpty || UserDefaults.standard.bool(forKey: "LiveNoUpdates") {
        return StubUpdater()
    }
    return SparkleUpdater()
}

final class SparkleUpdater: NSObject, Updater, SPUUpdaterDelegate {
    private var live = false
    private var controller: SPUStandardUpdaterController!

    override init() {
        super.init()
        controller = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: self,
                                                  userDriverDelegate: nil)
        ShellLog.write("updates: Sparkle \(controller.updater.feedURL?.absoluteString ?? "no feed")")
        if Self.testFeed != nil { controller.updater.checkForUpdatesInBackground() }
    }

    var isConfigured: Bool { true }

    func setLive(_ live: Bool) { self.live = live }

    func checkForUpdates() {
        if live { return waitAlert() }
        controller.checkForUpdates(nil)
    }

    // MARK: SPUUpdaterDelegate

    /// `-LiveUpdateFeed <url>` on the command line (or as a user default): use that appcast
    /// instead of SUFeedURL and check at once. For trying a release's appcast before it is
    /// published (docs/release.md "Updates"). The public key still has to match.
    static var testFeed: String? {
        let f = UserDefaults.standard.string(forKey: "LiveUpdateFeed") ?? ""
        return f.isEmpty ? nil : f
    }

    func feedURLString(for updater: SPUUpdater) -> String? { Self.testFeed }

    /// No check, scheduled or not, while the captions are live.
    func updaterMayCheck(forUpdates updater: SPUUpdater) -> Bool { !live }

    func updater(_ updater: SPUUpdater, willInstallUpdateOnQuit item: SUAppcastItem,
                 immediateInstallationBlock: @escaping () -> Void) -> Bool {
        ShellLog.write("updates: \(item.displayVersionString) downloaded; installs when the app quits")
        return false          // keep Sparkle's default: install on quit
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        ShellLog.write("updates: \(error.localizedDescription)")
    }

    private func waitAlert() {
        let a = NSAlert()
        a.messageText = "Updates wait until the service is over"
        a.informativeText = "Stop the captions first."
        a.runModal()
    }
}

final class StubUpdater: Updater {
    var isConfigured: Bool { false }
    private var live = false

    func setLive(_ live: Bool) { self.live = live }

    func checkForUpdates() {
        let a = NSAlert()
        if live {
            a.messageText = "Updates wait until the service is over"
            a.informativeText = "Stop the captions first."
        } else {
            a.messageText = "Updates are not set up in this build"
            a.informativeText = "This is a test build. Ask whoever gave it to you for the newest version."
        }
        a.runModal()
    }
}
