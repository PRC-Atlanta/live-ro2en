import AppKit
import WebKit

/// Full view: the captions Mac's own Live screen (the meter, the captions, gain, speaker, the
/// start/stop dial, Hide and quality), without its toolbar strip. The captions Mac's network
/// server serves it under /remote/app/ with the PIN as a cookie (src/live/remote.py app_view).
/// The page reports the console's size; the window keeps that shape and scales the page, so
/// there is never blank space beside it and the layout never reflows.
final class FullWindow: NSWindow, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    static let cookie = "live_pin"                 // remote.py PIN_COOKIE
    private let web: WKWebView
    private var natural = NSSize(width: 990, height: 720)   // the console in page pixels, until it says
    private var loaded: String?                    // "host port pin" on show, so a reconnect reloads
    private let more = MoreButton()
    var menuProvider: (() -> NSMenu)? { didSet { more.menuProvider = menuProvider } }
    var onSmall: (() -> Void)?

    init() {
        let conf = WKWebViewConfiguration()
        conf.websiteDataStore = .nonPersistent()   // the PIN cookie never outlives the app
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 990, height: 720), configuration: conf)
        super.init(contentRect: NSRect(x: 0, y: 0, width: 990, height: 720),
                   styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        conf.userContentController.add(Relay(self), name: "size")
        title = "Live RO2EN"
        appearance = NSAppearance(named: .darkAqua)
        titlebarAppearsTransparent = true
        backgroundColor = .black
        isReleasedWhenClosed = false
        web.underPageBackgroundColor = .black
        web.navigationDelegate = self
        contentView = web
        delegate = self

        // the way back, and the toolbar's menu, in the title bar (Live Remote has no menu bar)
        let small = NSButton(title: "Small toolbar", target: self, action: #selector(goSmall))
        small.bezelStyle = .recessed
        small.controlSize = .small
        small.font = Theme.font(11, .medium)
        more.frame = NSRect(x: 0, y: 0, width: 22, height: 22)
        let bar = NSStackView(views: [small, more])
        bar.spacing = 6
        bar.edgeInsets = NSEdgeInsets(top: 0, left: 0, bottom: 0, right: 8)
        more.widthAnchor.constraint(equalToConstant: 22).isActive = true
        more.heightAnchor.constraint(equalToConstant: 22).isActive = true
        bar.frame = NSRect(x: 0, y: 0, width: bar.fittingSize.width, height: 28)
        let acc = NSTitlebarAccessoryViewController()
        acc.view = bar
        acc.layoutAttribute = .trailing
        addTitlebarAccessoryViewController(acc)

        setFrameAutosaveName("LiveRemoteFull")
        fit(scale: min(1, contentLayoutRect.width / natural.width))
    }

    @objc private func goSmall() { onSmall?() }

    /// Show the captions Mac's Live screen; nothing happens if it is already the one on show.
    func load(host: String, port: Int, pin: String) {
        let key = "\(host) \(port) \(pin)"
        guard key != loaded else { return }
        loaded = key
        let h = host.hasSuffix(".") ? String(host.dropLast()) : host
        guard let url = URL(string: "http://\(h):\(port)/remote/app/ui/#live"),
              let c = HTTPCookie(properties: [.domain: h, .path: "/remote/app", .name: Self.cookie,
                                              .value: pin, .discard: "TRUE"]) else { return say("Not a valid address") }
        web.configuration.websiteDataStore.httpCookieStore.setCookie(c) { [weak self] in
            self?.web.load(URLRequest(url: url))
        }
    }

    /// Not connected: say why instead of an empty page.
    func say(_ why: String) {
        loaded = nil
        let esc = why.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        web.loadHTMLString("""
            <body style="margin:0;height:100vh;display:grid;place-items:center;background:#000;
            color:#9fb4bb;font:15px -apple-system,sans-serif;text-align:center">\(esc)</body>
            """, baseURL: nil)
    }

    // MARK: size: the window has the console's shape; its width sets the page's scale
    private func fit(scale z: CGFloat) {
        let vis = (screen ?? NSScreen.main)?.visibleFrame.size ?? NSSize(width: 1440, height: 900)
        let chrome = frame.height - contentLayoutRect.height
        let most = min(1.5, vis.width / natural.width, (vis.height - chrome) / natural.height)
        let z = max(min(z, most), min(0.5, most))
        contentAspectRatio = natural
        contentMinSize = NSSize(width: natural.width * min(0.5, most), height: natural.height * min(0.5, most))
        contentMaxSize = NSSize(width: natural.width * most, height: natural.height * most)
        let top = frame.maxY                        // keep the top-left corner where it is
        setContentSize(NSSize(width: (natural.width * z).rounded(), height: (natural.height * z).rounded()))
        setFrameTopLeftPoint(NSPoint(x: frame.minX, y: top))
        web.pageZoom = z
    }

    func windowDidResize(_ n: Notification) {
        web.pageZoom = contentLayoutRect.width / natural.width
    }

    func windowDidChangeScreen(_ n: Notification) { fit(scale: web.pageZoom) }

    fileprivate func console(_ size: NSSize) {
        guard size.width > 100, size.height > 100, size != natural else { return }
        let z = contentLayoutRect.width / natural.width
        natural = size
        fit(scale: z)
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let d = m.body as? [String: Any], let w = d["w"] as? Double, let h = d["h"] as? Double else { return }
        console(NSSize(width: w, height: h))
    }

    // MARK: the page's answer: a refusal is said in words, not shown as JSON
    func webView(_ v: WKWebView, decidePolicyFor r: WKNavigationResponse,
                 decisionHandler done: @escaping (WKNavigationResponsePolicy) -> Void) {
        let code = (r.response as? HTTPURLResponse)?.statusCode ?? 200
        guard r.isForMainFrame, code != 200 else { return done(.allow) }
        done(.cancel)
        say([401: "Wrong PIN: type the new one (⋯ → Enter PIN…).",
             403: "The captions Mac refused this address.",
             404: "Live Remote is off on the captions Mac, or it runs an older version without the full view.",
             429: "Too many wrong PINs: wait a minute."][code] ?? "The captions Mac answered \(code).")
    }

    func webView(_ v: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        let ns = e as NSError                       // cancelled by us (a refusal above, or a new load)
        if ns.code == NSURLErrorCancelled || (ns.domain == "WebKitErrorDomain" && ns.code == 102) { return }
        say("Can't reach the captions Mac: \(e.localizedDescription)")
    }
}

/// WKUserContentController keeps its handler: this one holds the window weakly.
private final class Relay: NSObject, WKScriptMessageHandler {
    weak var to: FullWindow?
    init(_ to: FullWindow) { self.to = to }
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        to?.userContentController(c, didReceive: m)
    }
}
