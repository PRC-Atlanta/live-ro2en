import Foundation

/// What the captions Mac sends (GET /remote/state, WS /remote/ws; src/live/remote.py remote_feed).
struct Feed: Decodable, Equatable {
    struct PP: Decodable, Equatable { var enabled: Bool; var connected: Bool }
    var name: String
    var phase: String?
    var running: Bool
    var paused: Bool
    var held: Bool
    var song: Bool
    var screen: String
    var lag_s: Double?
    var terms_n: Int
    var pp: PP
}

struct Terms: Decodable { var lines: [String]; var n: Int }

/// Where the remote stands with the captions Mac.
enum Link: Equatable {
    case notSet                 // no captions Mac chosen yet
    case connecting
    case connected(Feed)
    case wrongPin
    case remoteOff              // the captions Mac has Live Remote switched off
    case lockedOut
    case unreachable(String)
}

/// The connection: a feed over WebSocket, commands over HTTP, the PIN on every request.
/// Before each WebSocket it asks GET /remote/state, whose status says why it can't connect
/// (401 wrong PIN, 404 remote off, 429 locked out): a WebSocket close code can't be read
/// back reliably from URLSession. Everything is called and reported on the main queue.
final class RemoteClient: NSObject, URLSessionWebSocketDelegate {
    static let pinHeader = "X-Live-Pin"

    private(set) var host: String?
    private(set) var port: Int = 8766
    private(set) var pin: String = ""
    private(set) var link: Link = .notSet { didSet { if link != oldValue { onChange?(link) } } }
    var onChange: ((Link) -> Void)?

    private lazy var session = URLSession(configuration: {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 4
        c.waitsForConnectivity = false
        return c
    }(), delegate: self, delegateQueue: .main)
    private var ws: URLSessionWebSocketTask?
    private var gen = 0                  // a callback from an older connection is ignored
    private var retry: Timer?
    private var heartbeat: Timer?

    var feed: Feed? { if case .connected(let f) = link { return f }; return nil }

    func configure(host: String?, port: Int, pin: String) {
        self.host = host?.trimmingCharacters(in: .whitespaces).nilIfEmpty
        self.port = (1...65535).contains(port) ? port : 8766
        self.pin = pin
        reconnect()
    }

    /// "church-mac.local", "192.168.1.20:8766", "http://church-mac.local:8766/" → (host, port);
    /// nil for anything else, IPv6 included (the captions Mac's server is IPv4 only).
    static func parseAddress(_ raw: String, defaultPort: Int = 8766) -> (host: String, port: Int)? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        for scheme in ["http://", "https://", "ws://"] where s.lowercased().hasPrefix(scheme) { s.removeFirst(scheme.count) }
        if let slash = s.firstIndex(of: "/") { s = String(s[..<slash]) }
        guard !s.isEmpty else { return nil }
        var host = s, port = defaultPort
        if s.hasPrefix("[") {                                   // [v6] or [v6]:port
            guard let close = s.firstIndex(of: "]") else { return nil }
            host = String(s[s.index(after: s.startIndex)..<close])
            let rest = s[s.index(after: close)...]
            if !rest.isEmpty {
                guard rest.hasPrefix(":"), let n = Int(rest.dropFirst()) else { return nil }
                port = n
            }
        } else if s.filter({ $0 == ":" }).count == 1 {          // name:port or v4:port
            let parts = s.split(separator: ":", omittingEmptySubsequences: false)
            guard let n = Int(parts[1]) else { return nil }
            host = String(parts[0]); port = n
        } else if s.contains(":") {
            return nil                                          // IPv6: the captions Mac listens on IPv4 only
        }
        guard !host.contains(":"), (1...65535).contains(port), !host.isEmpty,
              host.allSatisfy({ $0.isLetter || $0.isNumber || ".-:%_".contains($0) }) else { return nil }
        return (host, port)
    }

    func reconnect() {
        gen += 1
        retry?.invalidate(); retry = nil
        heartbeat?.invalidate(); heartbeat = nil
        ws?.cancel(with: .goingAway, reason: nil); ws = nil
        guard host != nil else { link = .notSet; return }
        if feed == nil { link = .connecting }
        probe(gen)
    }

    private func url(_ path: String, scheme: String = "http") -> URL? {
        guard let host, (1...65535).contains(port) else { return nil }
        var h = host.hasSuffix(".") ? String(host.dropLast()) : host
        if h.contains(":") {                                     // IPv6: brackets, zone as %25
            h = "[" + h.replacingOccurrences(of: "%", with: "%25") + "]"
        }
        return URL(string: "\(scheme)://\(h):\(port)\(path)")
    }

    private func request(_ path: String, method: String = "GET", body: Any? = nil) -> URLRequest? {
        guard let u = url(path) else { return nil }
        var r = URLRequest(url: u)
        r.httpMethod = method
        r.setValue(pin, forHTTPHeaderField: Self.pinHeader)
        if let body {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        return r
    }

    private func later(_ s: TimeInterval, _ g: Int) {
        retry?.invalidate()
        retry = Timer.scheduledTimer(withTimeInterval: s, repeats: false) { [weak self] _ in
            guard let self, g == self.gen else { return }
            self.probe(g)
        }
    }

    private func probe(_ g: Int) {
        guard let r = request("/remote/state") else { link = .unreachable("Not a valid address"); return }
        session.dataTask(with: r) { [weak self] data, resp, err in
            guard let self, g == self.gen else { return }
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            switch code {
            case 200:
                if let data, let f = try? JSONDecoder().decode(Feed.self, from: data) { self.link = .connected(f) }
                self.openSocket(g)
            case 401: self.link = .wrongPin                       // waits for a new PIN
            case 403: self.link = .unreachable("The captions Mac refused this address")
            case 404: self.link = .remoteOff; self.later(5, g)
            case 429: self.link = .lockedOut; self.later(30, g)
            default:
                self.link = .unreachable(err?.localizedDescription ?? "HTTP \(code)")
                self.later(2, g)
            }
        }.resume()
    }

    private func openSocket(_ g: Int) {
        guard let u = url("/remote/ws", scheme: "ws") else { return }
        var r = URLRequest(url: u)
        r.setValue(pin, forHTTPHeaderField: Self.pinHeader)
        let t = session.webSocketTask(with: r)
        ws = t
        t.resume()
        receive(t, g)
        // the server only sends on change: a ping every 4 s notices a Mac that went to sleep
        heartbeat?.invalidate()
        heartbeat = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self, weak t] timer in
            guard let self, let t, g == self.gen else { timer.invalidate(); return }
            var answered = false
            t.sendPing { err in
                DispatchQueue.main.async {
                    answered = true
                    if err != nil, g == self.gen { self.dropped(t, g) }
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                if !answered, g == self.gen { self.dropped(t, g) }
            }
        }
    }

    private func dropped(_ t: URLSessionWebSocketTask, _ g: Int) {
        guard g == gen, ws === t else { return }
        heartbeat?.invalidate(); heartbeat = nil
        t.cancel(with: .goingAway, reason: nil)
        ws = nil
        link = .unreachable("The connection was lost")
        later(1, g)
    }

    private func receive(_ t: URLSessionWebSocketTask, _ g: Int) {
        t.receive { [weak self] result in
            guard let self, g == self.gen else { return }
            switch result {
            case .success(.string(let s)):
                if let f = try? JSONDecoder().decode(Feed.self, from: Data(s.utf8)) { self.link = .connected(f) }
                self.receive(t, g)
            case .success:
                self.receive(t, g)
            case .failure:
                guard self.ws === t else { return }
                self.heartbeat?.invalidate(); self.heartbeat = nil
                self.ws = nil
                self.later(1, g)                                   // the probe says why
            }
        }
    }

    // MARK: commands
    /// POST /remote/<action>. `done` gets nil on success, else a short message.
    func send(_ action: String, _ body: [String: Any] = [:], done: ((String?) -> Void)? = nil) {
        guard feed != nil, let r = request("/remote/\(action)", method: "POST", body: body) else {
            done?("Not connected"); return                         // a stale PIN must not add to a lockout
        }
        let g = gen
        session.dataTask(with: r) { [weak self] data, resp, err in
            guard let self, g == self.gen else { return }          // an answer from an older connection
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200, let data, let f = try? JSONDecoder().decode(Feed.self, from: data) {
                self.link = .connected(f)
                done?(nil)
            } else if code == 200 {
                done?(nil)
            } else {
                done?(Self.message(code, data, err))
                switch code {
                case 401: self.dropAll(.wrongPin)                  // no probe: it would count again
                case 429: self.dropAll(.lockedOut); self.later(30, self.gen)
                case 404, 0: self.reconnect()
                default: break
                }
            }
        }.resume()
    }

    private func dropAll(_ l: Link) {
        gen += 1
        retry?.invalidate(); retry = nil
        heartbeat?.invalidate(); heartbeat = nil
        ws?.cancel(with: .goingAway, reason: nil); ws = nil
        link = l
    }

    func getTerms(_ done: @escaping (Terms?, String?) -> Void) {
        guard feed != nil, let r = request("/remote/terms") else { done(nil, "Not connected"); return }
        let g = gen
        session.dataTask(with: r) { [weak self] data, resp, err in
            guard let self, g == self.gen else { done(nil, "Connected to another Mac since"); return }
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200, let data, let t = try? JSONDecoder().decode(Terms.self, from: data) { done(t, nil) }
            else { done(nil, Self.message(code, data, err)) }
        }.resume()
    }

    func sendTerms(_ lines: [String], keep: Bool, _ done: @escaping (Terms?, String?) -> Void) {
        guard feed != nil, let r = request("/remote/terms", method: "POST", body: ["lines": lines, "save": keep]) else {
            done(nil, "Not connected"); return
        }
        let g = gen
        session.dataTask(with: r) { [weak self] data, resp, err in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200, let data, let t = try? JSONDecoder().decode(Terms.self, from: data) {
                done(t, nil)                                       // they reached the Mac they were sent to
                if let self, g == self.gen { self.refresh() }
            } else { done(nil, Self.message(code, data, err)) }
        }.resume()
    }

    /// One GET /remote/state, so a badge updates before the next feed message.
    func refresh() {
        guard let r = request("/remote/state") else { return }
        let g = gen
        session.dataTask(with: r) { [weak self] data, resp, _ in
            guard let self, g == self.gen, (resp as? HTTPURLResponse)?.statusCode == 200, let data,
                  let f = try? JSONDecoder().decode(Feed.self, from: data) else { return }
            self.link = .connected(f)
        }.resume()
    }

    static func message(_ code: Int, _ data: Data?, _ err: Error?) -> String {
        switch code {
        case 401: return "Wrong PIN"
        case 404: return "Live Remote is off on the captions Mac"
        case 403: return "The captions Mac refused this address"
        case 409: return "There is no caption to remove"
        case 429: return "Too many wrong PINs: try again in a minute"
        case 0: return err?.localizedDescription ?? "Can't reach the captions Mac"
        default:
            if let data, let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let d = o["detail"] as? String { return d }
            return "The captions Mac answered \(code)"
        }
    }
}

extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
