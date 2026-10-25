import Foundation

/// A captions Mac found over Bonjour (_liveroen._tcp, advertised by src/live/remote.py).
struct FoundMac: Equatable {
    var name: String         // "Live RO2EN (church-mac)"
    var host: String         // "church-mac.local."
    var port: Int
}

/// Browses the local network for captions Macs and resolves each to a host and port.
final class Discovery: NSObject, NetServiceBrowserDelegate, NetServiceDelegate {
    static let type = "_liveroen._tcp."
    private let browser = NetServiceBrowser()
    private var resolving: [NetService] = []
    private(set) var found: [FoundMac] = [] { didSet { if found != oldValue { onChange?(found) } } }
    var onChange: (([FoundMac]) -> Void)?

    func start() {
        browser.delegate = self
        browser.searchForServices(ofType: Self.type, inDomain: "local.")
    }

    func netServiceBrowser(_ b: NetServiceBrowser, didFind s: NetService, moreComing: Bool) {
        resolving.append(s)
        s.delegate = self
        s.resolve(withTimeout: 5)
    }

    func netServiceBrowser(_ b: NetServiceBrowser, didRemove s: NetService, moreComing: Bool) {
        found.removeAll { $0.name == s.name }
        resolving.removeAll { $0 === s }
    }

    func netServiceDidResolveAddress(_ s: NetService) {
        guard let host = s.hostName, s.port > 0 else { return }
        let mac = FoundMac(name: s.name, host: host, port: s.port)
        if let i = found.firstIndex(where: { $0.name == s.name }) { found[i] = mac } else { found.append(mac) }
    }

    func netService(_ s: NetService, didNotResolve errorDict: [String: NSNumber]) {
        resolving.removeAll { $0 === s }
    }

    func netServiceBrowserDidStopSearch(_ b: NetServiceBrowser) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in self?.start() }
    }
}
