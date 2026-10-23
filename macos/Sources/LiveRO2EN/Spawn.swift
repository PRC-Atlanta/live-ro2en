import Darwin
import Foundation

/// posix_spawn, because Foundation.Process cannot put the child in its own process group.
/// The orchestrator gets a new group (pgid == pid) so that the engine it spawns lands in the
/// same group and one killpg reaches both. Only fds 0-2 are inherited (CLOEXEC_DEFAULT), so
/// no child ever holds the shell's watchdog pipe open.
enum Spawn {
    struct Failure: LocalizedError {
        let what: String; let code: Int32
        var errorDescription: String? { "\(what): \(String(cString: strerror(code)))" }
    }

    static func spawn(_ argv: [String], env: [String: String], cwd: String? = nil,
                      stdinFD: Int32? = nil, outputPath: String? = nil) throws -> pid_t {
        var fa: posix_spawn_file_actions_t?
        var attr: posix_spawnattr_t?
        posix_spawn_file_actions_init(&fa)
        posix_spawnattr_init(&attr)
        defer { posix_spawn_file_actions_destroy(&fa); posix_spawnattr_destroy(&attr) }

        if let fd = stdinFD {
            posix_spawn_file_actions_adddup2(&fa, fd, 0)
        } else {
            posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0)
        }
        let out = outputPath ?? "/dev/null"
        posix_spawn_file_actions_addopen(&fa, 1, out, O_WRONLY | O_CREAT | O_APPEND, 0o644)
        posix_spawn_file_actions_adddup2(&fa, 1, 2)
        if let cwd { posix_spawn_file_actions_addchdir_np(&fa, cwd) }

        var all = sigset_t(); sigfillset(&all)
        var none = sigset_t(); sigemptyset(&none)
        posix_spawnattr_setsigdefault(&attr, &all)
        posix_spawnattr_setsigmask(&attr, &none)
        posix_spawnattr_setpgroup(&attr, 0)
        let flags = POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK
            | POSIX_SPAWN_CLOEXEC_DEFAULT
        posix_spawnattr_setflags(&attr, Int16(flags))

        let cArgs = argv.map { strdup($0) } + [nil]
        let cEnv = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { (cArgs + cEnv).forEach { free($0) } }

        var pid: pid_t = 0
        let rc = posix_spawn(&pid, argv[0], &fa, &attr, cArgs, cEnv)
        guard rc == 0 else { throw Failure(what: "Couldn't start \(argv[0])", code: rc) }
        return pid
    }

    /// True while any process is left in the group (a zombie leader counts until reaped).
    static func groupAlive(_ pgid: pid_t) -> Bool {
        guard pgid > 1 else { return false }
        return killpg(pgid, 0) == 0 || errno == EPERM
    }

    static func processAlive(_ pid: pid_t) -> Bool {
        guard pid > 1 else { return false }
        return kill(pid, 0) == 0 || errno == EPERM
    }

    /// Every process as (pid, pgid, command line), from ps.
    static func processTable() -> [(pid: pid_t, pgid: pid_t, command: String)] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/ps")
        p.arguments = ["-axww", "-o", "pid=,pgid=,command="]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return [] }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(decoding: data, as: UTF8.self).split(separator: "\n").compactMap { line in
            let parts = line.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: true)
            guard parts.count == 3, let pid = pid_t(parts[0]), let pgid = pid_t(parts[1]) else { return nil }
            return (pid, pgid, String(parts[2]))
        }
    }
}

/// A port the orchestrator can listen on: the last one used if still free (so the page's
/// origin, and with it any per-origin browser storage, stays the same), else a fresh one.
enum Ports {
    static let avoid: Set<Int> = [8765]      // the dev orchestrator's default port

    /// Free = nobody is listening and uvicorn (which sets SO_REUSEADDR) could bind it, even
    /// with connections from the previous run still in TIME_WAIT.
    static func canBind(_ port: Int) -> Bool { !listening(port) && bind(port, reuse: true) != nil }

    static func choose() -> Int {
        let key = "LivePort"
        let last = UserDefaults.standard.integer(forKey: key)
        if (1024...65535).contains(last), !avoid.contains(last), canBind(last) { return last }
        for _ in 0..<20 {
            if let p = bind(0), !avoid.contains(p) {
                UserDefaults.standard.set(p, forKey: key)
                return p
            }
        }
        return 0
    }

    /// Bind 127.0.0.1:port, return the bound port, close.
    private static func loopback(_ port: Int) -> sockaddr_in {
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port).bigEndian)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        return addr
    }

    /// Something accepts connections on 127.0.0.1:port.
    private static func listening(_ port: Int) -> Bool {
        let s = socket(AF_INET, SOCK_STREAM, 0)
        guard s >= 0 else { return false }
        defer { close(s) }
        var addr = loopback(port)
        return withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(s, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0
            }
        }
    }

    private static func bind(_ port: Int, reuse: Bool = false) -> Int? {
        let s = socket(AF_INET, SOCK_STREAM, 0)
        guard s >= 0 else { return nil }
        defer { close(s) }
        if reuse {
            var one: Int32 = 1
            setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, socklen_t(MemoryLayout<Int32>.size))
        }
        var addr = loopback(port)
        let ok = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(s, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0
            }
        }
        guard ok else { return nil }
        var bound = sockaddr_in()
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &bound) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(s, $0, &len) }
        }
        return Int(UInt16(bigEndian: bound.sin_port))
    }
}
