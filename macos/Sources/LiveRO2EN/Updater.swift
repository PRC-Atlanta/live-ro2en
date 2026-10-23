import AppKit

/// Software updates. Stubbed until S10 adds Sparkle 2 (Frameworks/Sparkle.framework, an
/// EdDSA-signed appcast on the public releases feed, SUFeedURL/SUPublicEDKey in Info.plist).
///
/// Rules S10 must keep (APP_PLAN §1.1 "Updates"):
/// - never check for or install an update while live (`canCheck` is false then);
/// - install on quit (`SPUUpdater` with automatic install on termination);
/// - model updates are a prompt at launch, never during a service.
protocol Updater: AnyObject {
    var isConfigured: Bool { get }
    func checkForUpdates()
    /// Called whenever live changes; a real updater pauses its scheduled checks while live.
    func setLive(_ live: Bool)
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
            a.messageText = "Updates are not set up yet"
            a.informativeText = "This is a test build. Ask whoever gave it to you for the newest version."
        }
        a.runModal()
    }
}
