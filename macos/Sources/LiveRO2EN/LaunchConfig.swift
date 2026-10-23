import Foundation

/// How to start the orchestrator: the bundled runtime (docs/app_contract.md "Bundle layout")
/// when the .app carries one, otherwise the repo's venvs (dev).
///
/// Dev overrides, first match wins (each is also a user default, so
/// `open "Live RO2EN.app" --args -LiveRepoPath /path` or `defaults write` work too):
///   LIVE_REPO / LiveRepoPath                  repo with src/live/app.py
///                                             (default: found by walking up from the .app)
///   LIVE_PYTHON / LivePythonPath              orchestrator interpreter (default <repo>/.venv-mt/bin/python)
///   LIVE_ENGINE_PYTHON / LiveEnginePythonPath engine interpreter (default: the orchestrator
///                                             decides; if the repo has no .venv-nemo but the
///                                             python's sibling venv does, that one)
struct LaunchConfig {
    enum Mode { case bundle, dev }

    let mode: Mode
    let python: URL
    let script: URL
    let workingDirectory: URL
    let preArgs: [String]          // interpreter flags before the script
    let environment: [String: String]

    static let defaultRepoFallback = "/Users/bentarcau/Code/Live RO2EN"

    /// The marker every orchestrator command line contains; stale-process cleanup checks it.
    static let scriptMarker = "src/live/app.py"

    func arguments(port: Int) -> [String] {
        [python.path] + preArgs + [script.path, "--port", String(port)]
    }

    static func setting(_ env: String, _ key: String) -> String? {
        if let v = ProcessInfo.processInfo.environment[env], !v.isEmpty { return v }
        if let v = UserDefaults.standard.string(forKey: key), !v.isEmpty { return v }
        return nil
    }

    static func resolve() throws -> LaunchConfig {
        let fm = FileManager.default
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env.removeValue(forKey: "PYTHONHOME")

        // Packaged app
        if let res = Bundle.main.resourceURL {
            let py = res.appendingPathComponent("runtime/bin/python3.12")
            let script = res.appendingPathComponent("app/src/live/app.py")
            if fm.isExecutableFile(atPath: py.path), fm.fileExists(atPath: script.path) {
                let site = res.appendingPathComponent("site")
                let s = { (n: String) in site.appendingPathComponent(n).path }
                env["PYTHONPATH"] = [s("common"), s("mt")].joined(separator: ":")
                env["LIVE_ENGINE_PYTHON"] = py.path
                env["LIVE_ENGINE_PYTHONPATH"] = [s("common"), s("nemo")].joined(separator: ":")
                env["LIVE_HOME"] = AppPaths.support.path
                env["LIVE_LOG_DIR"] = AppPaths.logs.path
                env["LIVE_CACHE_DIR"] = AppPaths.caches.path
                env["HF_HOME"] = AppPaths.support.appendingPathComponent("hf").path
                // Nothing may be written into the signed bundle: the engine and the model download
                // run without -s -B, and library caches (numba via librosa) go to Caches.
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                env["PYTHONNOUSERSITE"] = "1"
                env["NUMBA_CACHE_DIR"] = AppPaths.caches.appendingPathComponent("numba").path
                env.removeValue(forKey: "LIVE_ALLOW_NETWORK")
                return LaunchConfig(mode: .bundle, python: py, script: script,
                                    workingDirectory: res.appendingPathComponent("app"),
                                    preArgs: ["-s", "-B"], environment: env)
            }
        }

        // Dev: the repo's venvs
        guard let repo = findRepo() else {
            throw LaunchError.missing("Can't find the Live RO2EN code (src/live/app.py). "
                + "Set LIVE_REPO or the LiveRepoPath user default to the repo folder.")
        }
        let pyPath = setting("LIVE_PYTHON", "LivePythonPath")
            ?? repo.appendingPathComponent(".venv-mt/bin/python").path
        guard fm.isExecutableFile(atPath: pyPath) else {
            throw LaunchError.missing("Can't find Python at \(pyPath). "
                + "Set LIVE_PYTHON or the LivePythonPath user default.")
        }
        if let eng = setting("LIVE_ENGINE_PYTHON", "LiveEnginePythonPath") {
            env["LIVE_ENGINE_PYTHON"] = eng
        } else if !fm.fileExists(atPath: repo.appendingPathComponent(".venv-nemo").path) {
            // e.g. a git worktree using the main checkout's venvs
            let sibling = URL(fileURLWithPath: pyPath).deletingLastPathComponent()   // bin
                .deletingLastPathComponent().deletingLastPathComponent()             // venv's parent
                .appendingPathComponent(".venv-nemo/bin/python")
            if fm.isExecutableFile(atPath: sibling.path) { env["LIVE_ENGINE_PYTHON"] = sibling.path }
        }
        return LaunchConfig(mode: .dev, python: URL(fileURLWithPath: pyPath),
                            script: repo.appendingPathComponent(scriptMarker),
                            workingDirectory: repo, preArgs: [], environment: env)
    }

    static func findRepo() -> URL? {
        let fm = FileManager.default
        let isRepo = { (u: URL) in fm.fileExists(atPath: u.appendingPathComponent(scriptMarker).path) }
        if let p = setting("LIVE_REPO", "LiveRepoPath") {
            let u = URL(fileURLWithPath: (p as NSString).expandingTildeInPath)
            return isRepo(u) ? u : nil
        }
        var dir = Bundle.main.bundleURL.deletingLastPathComponent()      // e.g. <repo>/macos/build
        for _ in 0..<6 {
            if isRepo(dir) { return dir }
            let up = dir.deletingLastPathComponent()
            if up.path == dir.path { break }
            dir = up
        }
        let fallback = URL(fileURLWithPath: defaultRepoFallback)
        return isRepo(fallback) ? fallback : nil
    }
}

enum LaunchError: LocalizedError {
    case missing(String)
    var errorDescription: String? { if case .missing(let m) = self { return m }; return nil }
}

/// The app's own folders (docs/app_contract.md "Bundle layout").
enum AppPaths {
    static let name = "Live RO2EN"
    static var support: URL { lib("Application Support") }
    static var logs: URL { lib("Logs") }
    static var caches: URL { lib("Caches") }
    /// The shell's pidfile lives here.
    static var run: URL { support.appendingPathComponent("run") }

    private static func lib(_ sub: String) -> URL {
        let u = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library").appendingPathComponent(sub).appendingPathComponent(name)
        try? FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
        return u
    }
}

/// A small append-only log of what the shell did (spawns, exits, kills).
enum ShellLog {
    private static let url = AppPaths.logs.appendingPathComponent("shell.log")
    private static let fmt: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f
    }()

    static func rotate() {
        let fm = FileManager.default
        for u in [url, AppPaths.logs.appendingPathComponent("orchestrator.log")] {
            if let size = (try? fm.attributesOfItem(atPath: u.path))?[.size] as? Int, size > 5_000_000 {
                let old = u.appendingPathExtension("1")
                try? fm.removeItem(at: old)
                try? fm.moveItem(at: u, to: old)
            }
        }
    }

    static func write(_ msg: String) {
        let line = "\(fmt.string(from: Date())) \(msg)\n"
        FileHandle.standardError.write(line.data(using: .utf8)!)
        guard let data = line.data(using: .utf8) else { return }
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile(); h.write(data); try? h.close()
        } else {
            try? data.write(to: url)
        }
    }
}
