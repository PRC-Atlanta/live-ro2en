import Darwin
import Foundation

/// Owns the orchestrator (app.py) and, through its process group, the engine it spawns.
///
/// No process is left behind (APP_PLAN §1.2 "Shutdown"):
/// - Quit: POST /api/shutdown, SIGTERM the group after 5 s (at once if shutdown isn't built
///   yet), SIGKILL it after 10 s.
/// - The shell dies (Force Quit, crash): a watchdog `sh` in its own group holds the read end of
///   a pipe only the shell can write; on EOF it SIGTERMs, then SIGKILLs, the group.
/// - Anything still there anyway: the pidfile is read at the next launch, and every process in
///   the recorded group whose command line is ours is killed.
/// - The orchestrator dies on its own: it is restarted (3 times in 2 minutes, then the operator
///   is asked).
/// All methods run on the main queue.
final class ProcessSupervisor {
    enum Status: Equatable {
        case launching(String)                 // what is happening, in operator words
        case ready(port: Int)
        case failed(String, logTail: String)
    }

    var onStatus: ((Status) -> Void)?
    /// Every /api/state poll (about 1 Hz) once ready.
    var onState: (([String: Any]) -> Void)?

    private(set) var status: Status = .launching("Starting…") { didSet { if status != oldValue { onStatus?(status) } } }
    private(set) var port = 0

    private var config: LaunchConfig?
    private var pid: pid_t = 0
    private var exitSource: DispatchSourceProcess?
    private var watchdog: (pid: pid_t, pipe: Int32)?
    private var stopping = false
    private var gaveUp: String?                // launch timed out: report instead of restarting
    private var generation = 0                 // invalidates timers/polls of an earlier child
    private var restarts: [Date] = []
    private var pollTimer: Timer?
    private var launchStarted = Date()
    private let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 2
        c.connectionProxyDictionary = [:]
        return URLSession(configuration: c)
    }()

    static let launchTimeout: TimeInterval = 120
    static let maxRestarts = 3
    static let restartWindow: TimeInterval = 120

    var logURL: URL { AppPaths.logs.appendingPathComponent("orchestrator.log") }
    private var pidfileURL: URL { AppPaths.run.appendingPathComponent("orchestrator.json") }
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    // MARK: start

    func start() {
        stopping = false
        ShellLog.rotate()
        do {
            config = try LaunchConfig.resolve()
        } catch {
            status = .failed(error.localizedDescription, logTail: "")
            return
        }
        Self.cleanupStale(pidfile: pidfileURL)
        launch()
    }

    /// Operator pressed "Try again" after a failure.
    func retry() {
        restarts.removeAll()
        if pid == 0 { start() }
    }

    private func launch() {
        guard let config, !stopping else { return }
        generation += 1
        port = Ports.choose()
        guard port > 0 else { status = .failed("No free network port on this Mac.", logTail: ""); return }
        launchStarted = Date()
        status = .launching(restarts.isEmpty ? "Starting…" : "Restarting after a problem…")

        let argv = config.arguments(port: port)
        do {
            appendLog("\n=== \(Date()) shell starting: \(argv.joined(separator: " "))\n")
            pid = try Spawn.spawn(argv, env: config.environment, cwd: config.workingDirectory.path,
                                  outputPath: logURL.path)
        } catch {
            status = .failed(error.localizedDescription, logTail: "")
            return
        }
        ShellLog.write("spawned orchestrator pid \(pid) port \(port) (\(config.mode))")
        startWatchdog(for: pid)
        writePidfile()
        watchExit(pid)
        pollReadiness(generation)
    }

    // MARK: watchdog

    private func startWatchdog(for pgid: pid_t) {
        var fds: [Int32] = [0, 0]
        guard pipe(&fds) == 0 else { return }
        _ = fcntl(fds[1], F_SETFD, FD_CLOEXEC)
        _ = fcntl(fds[1], F_SETNOSIGPIPE, 1)
        // $0 is a marker that stale cleanup recognises; $1 the group to kill on EOF
        let script = "trap '' HUP INT; read _; kill -TERM -$1 2>/dev/null; sleep 4; kill -KILL -$1 2>/dev/null; exit 0"
        let wpid = try? Spawn.spawn(["/bin/sh", "-c", script, Self.watchdogMarker, String(pgid)],
                                    env: ["PATH": "/usr/bin:/bin"], stdinFD: fds[0])
        close(fds[0])
        guard let wpid else { close(fds[1]); return }
        watchdog = (wpid, fds[1])
        reapWhenDone(wpid)
    }

    /// Stop the watchdog. `killGroup`: let it clean up the group (an orchestrator that died
    /// may have left the engine running); otherwise just stop it.
    private func endWatchdog(killGroup: Bool) {
        guard let w = watchdog else { return }
        watchdog = nil
        if !killGroup { kill(w.pid, SIGKILL) }
        close(w.pipe)
    }

    static let watchdogMarker = "live-ro2en-watchdog"

    private func reapWhenDone(_ p: pid_t) {
        let src = DispatchSource.makeProcessSource(identifier: p, eventMask: .exit, queue: .main)
        src.setEventHandler { var st: Int32 = 0; waitpid(p, &st, 0); src.cancel() }
        src.resume()
        var st: Int32 = 0
        if waitpid(p, &st, WNOHANG) == p { src.cancel() }
    }

    // MARK: exit and restart

    private func watchExit(_ p: pid_t) {
        let src = DispatchSource.makeProcessSource(identifier: p, eventMask: .exit, queue: .main)
        src.setEventHandler { [weak self] in self?.reap(p) }
        exitSource = src
        src.resume()
        reap(p, onlyIfExited: true)            // it may have died before the source was armed
    }

    private func reap(_ p: pid_t, onlyIfExited: Bool = false) {
        guard p == pid, p > 0 else { return }
        var st: Int32 = 0
        let r = waitpid(p, &st, onlyIfExited ? WNOHANG : 0)
        guard r == p || (r == -1 && errno == ECHILD) else { return }
        exitSource?.cancel(); exitSource = nil
        pid = 0
        let code = (st & 0x7f) == 0 ? "exit \((st >> 8) & 0xff)" : "signal \(st & 0x7f)"
        ShellLog.write("orchestrator \(p) ended (\(code))\(stopping ? "" : " unexpectedly")")
        if stopping { return }                 // shutdown() finishes the job
        pollTimer?.invalidate(); pollTimer = nil
        endWatchdog(killGroup: true)           // takes an orphaned engine with it
        removePidfile()

        if let why = gaveUp {
            gaveUp = nil
            status = .failed(why, logTail: logTail())
            return
        }
        let now = Date()
        restarts = restarts.filter { now.timeIntervalSince($0) < Self.restartWindow }
        if restarts.count >= Self.maxRestarts {
            status = .failed("The translator keeps stopping.", logTail: logTail())
            return
        }
        restarts.append(now)
        status = .launching("Restarting after a problem…")
        let gen = generation
        // give the watchdog's SIGTERM a moment so the old engine releases the mic first
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self, gen == self.generation, self.pid == 0 else { return }
            self.launch()
        }
    }

    // MARK: readiness and state

    private func pollReadiness(_ gen: Int) {
        guard gen == generation, pid != 0, !stopping else { return }
        if Date().timeIntervalSince(launchStarted) > Self.launchTimeout {
            gaveUp = "The translator did not start within \(Int(Self.launchTimeout)) s."
            status = .failed(gaveUp!, logTail: logTail())
            killpg(pid, SIGTERM)
            return
        }
        getJSON("/api/state") { [weak self] obj, code in
            guard let self, gen == self.generation, self.pid != 0 else { return }
            if code == 200, let obj {
                ShellLog.write("orchestrator ready on port \(self.port) after "
                    + String(format: "%.1f", Date().timeIntervalSince(self.launchStarted)) + " s")
                self.status = .ready(port: self.port)
                self.onState?(obj)
                self.startStatePolling(gen)
            } else {
                let secs = Int(Date().timeIntervalSince(self.launchStarted))
                self.status = .launching(secs < 5 ? (self.restarts.isEmpty ? "Starting…" : "Restarting after a problem…")
                                                  : "Starting the translator… (\(secs) s)")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.pollReadiness(gen) }
            }
        }
    }

    private func startStatePolling(_ gen: Int) {
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self, gen == self.generation, self.pid != 0 else { return }
            self.getJSON("/api/state") { obj, _ in
                if let obj, gen == self.generation { self.onState?(obj) }
            }
        }
    }

    private func getJSON(_ path: String, _ done: @escaping ([String: Any]?, Int) -> Void) {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.cachePolicy = .reloadIgnoringLocalCacheData
        session.dataTask(with: req) { data, resp, _ in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let obj = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async { done(obj, code) }
        }.resume()
    }

    /// Status code of a GET (0 if unreachable).
    func probe(_ path: String, _ done: @escaping (Int) -> Void) {
        var req = URLRequest(url: URL(string: path, relativeTo: baseURL)!)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        session.dataTask(with: req) { _, resp, _ in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            DispatchQueue.main.async { done(code) }
        }.resume()
    }

    // MARK: shutdown

    /// Stop everything; `done` runs once no process of ours is left (10.5 s at most).
    func shutdown(_ done: @escaping () -> Void) {
        stopping = true
        generation += 1
        pollTimer?.invalidate(); pollTimer = nil
        let leader = pid
        guard leader > 0 else {
            endWatchdog(killGroup: false); removePidfile(); done(); return
        }
        ShellLog.write("shutting down orchestrator \(leader)")
        let t0 = Date()
        var termSent = false, killSent = false, finished = false

        let finish = { [weak self] in
            guard !finished else { return }
            finished = true
            self?.endWatchdog(killGroup: false)
            self?.removePidfile()
            ShellLog.write("shutdown complete after " + String(format: "%.1f", Date().timeIntervalSince(t0)) + " s")
            done()
        }
        let term = {
            guard !termSent else { return }
            termSent = true
            ShellLog.write("SIGTERM group \(leader)")
            killpg(leader, SIGTERM)
        }

        // 1. ask nicely
        var req = URLRequest(url: baseURL.appendingPathComponent("/api/shutdown"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("http://127.0.0.1:\(port)", forHTTPHeaderField: "Origin")
        req.httpBody = "{}".data(using: .utf8)
        req.timeoutInterval = 2
        session.dataTask(with: req) { _, resp, _ in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            DispatchQueue.main.async {
                ShellLog.write("POST /api/shutdown -> \(code)")
                if !(200..<300).contains(code) { term() }   // not built yet (501) or unreachable
            }
        }.resume()

        // 2. wait for the whole group, escalating
        func tick() {
            if finished { return }
            let elapsed = Date().timeIntervalSince(t0)
            if pid == leader { reap(leader, onlyIfExited: true) }
            if !Spawn.groupAlive(leader) { finish(); return }
            if elapsed > 5 { term() }
            if elapsed > 10, !killSent {
                killSent = true
                ShellLog.write("SIGKILL group \(leader)")
                killpg(leader, SIGKILL)
            }
            if elapsed > 10.5 { finish(); return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: tick)
        }
        tick()
    }

    /// Synchronous last resort (e.g. the app is being killed by the system).
    func killNow() {
        guard pid > 0 else { return }
        killpg(pid, SIGKILL)
        endWatchdog(killGroup: false)
        removePidfile()
    }

    // MARK: pidfile and stale processes

    private func writePidfile() {
        let info: [String: Any] = ["pid": pid, "pgid": pid, "port": port, "shell": getpid(),
                                   "watchdog": watchdog?.pid ?? 0,
                                   "script": config?.script.path ?? "", "started": Date().timeIntervalSince1970]
        try? FileManager.default.createDirectory(at: AppPaths.run, withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: info) {
            try? data.write(to: pidfileURL, options: .atomic)
        }
    }

    private func removePidfile() { try? FileManager.default.removeItem(at: pidfileURL) }

    /// Kill what a previous run of the shell left behind (it was force-quit or crashed and its
    /// watchdog didn't finish). Only processes in the recorded group whose command line is the
    /// orchestrator, the engine or our watchdog are touched, so a dev orchestrator started by
    /// hand is never hit.
    static func cleanupStale(pidfile: URL) {
        guard let data = try? Data(contentsOf: pidfile),
              let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let pgid = pid_t((info["pgid"] as? Int) ?? 0)
        let wd = pid_t((info["watchdog"] as? Int) ?? 0)
        let ours = { (cmd: String) in
            cmd.contains("live/app.py") || cmd.contains("live/engine.py")
        }
        let table = Spawn.processTable()
        var victims = table.filter { pgid > 1 && $0.pgid == pgid && ours($0.command) }.map(\.pid)
        if let w = table.first(where: { $0.pid == wd && $0.command.contains(watchdogMarker) }) {
            victims.append(w.pid)
        }
        if !victims.isEmpty {
            ShellLog.write("stale processes from an earlier run: \(victims); stopping them")
            victims.forEach { kill($0, SIGTERM) }
            let deadline = Date().addingTimeInterval(4)
            while Date() < deadline, victims.contains(where: Spawn.processAlive) { usleep(100_000) }
            victims.filter(Spawn.processAlive).forEach {
                ShellLog.write("SIGKILL stale \($0)"); kill($0, SIGKILL)
            }
        }
        try? FileManager.default.removeItem(at: pidfile)
    }

    // MARK: logs

    private func appendLog(_ s: String) {
        guard let d = s.data(using: .utf8) else { return }
        if let h = try? FileHandle(forWritingTo: logURL) { h.seekToEndOfFile(); h.write(d); try? h.close() }
        else { try? d.write(to: logURL) }
    }

    func logTail(lines n: Int = 12) -> String {
        guard let s = try? String(contentsOf: logURL, encoding: .utf8) else { return "" }
        return s.split(separator: "\n", omittingEmptySubsequences: false).suffix(n).joined(separator: "\n")
    }
}
