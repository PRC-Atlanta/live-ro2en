import Foundation

/// Reads "are captions live?" from /api/state and, while they are, keeps the Mac awake and the
/// app out of App Nap (a sleeping display would also black out ProPresenter's output).
final class LiveActivity {
    private var token: NSObjectProtocol?
    private(set) var isLive = false
    var onChange: ((Bool) -> Void)?

    /// Live = the engine is listening or captioning. Uses `phase` (S1) when the orchestrator
    /// sends it, else the built `running` flag.
    static func isLive(_ state: [String: Any]) -> Bool {
        if let phase = state["phase"] as? String {
            return ["starting", "listening", "captioning"].contains(phase)
        }
        return (state["running"] as? Bool) ?? false
    }

    func update(_ state: [String: Any]) { set(Self.isLive(state)) }

    func set(_ live: Bool) {
        guard live != isLive else { return }
        isLive = live
        if live {
            token = ProcessInfo.processInfo.beginActivity(
                options: [.userInitiated, .idleSystemSleepDisabled, .idleDisplaySleepDisabled,
                          .suddenTerminationDisabled, .automaticTerminationDisabled],
                reason: "Live captions are running")
            ShellLog.write("live: sleep and App Nap blocked")
        } else if let t = token {
            ProcessInfo.processInfo.endActivity(t)
            token = nil
            ShellLog.write("not live: sleep allowed again")
        }
        onChange?(live)
    }
}
