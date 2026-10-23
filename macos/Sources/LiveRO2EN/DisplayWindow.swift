import AppKit
import WebKit

/// The captions display: ui/display.html in its own window, to drag onto a second screen or
/// projector and make full screen (double-click; Esc leaves full screen). One window at most;
/// it remembers where it was, so it comes back on the same screen next time.
final class DisplayWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?

    var isOpen: Bool { window?.isVisible == true }

    /// Open (or bring forward) the display at `url` (http://127.0.0.1:<port>/ui/display.html).
    func show(_ url: URL) {
        if let window, let webView {
            if webView.url != url { webView.load(URLRequest(url: url)) }
            window.makeKeyAndOrderFront(nil)
            return
        }
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(MessageProxy(self), name: "display")
        let web = WKWebView(frame: .zero, configuration: cfg)
        web.setValue(false, forKey: "drawsBackground")
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 720),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable],
                         backing: .buffered, defer: false)
        w.title = "Captions display"
        w.backgroundColor = .black
        w.collectionBehavior = [.fullScreenPrimary, .managed]
        w.isReleasedWhenClosed = false
        w.delegate = self
        w.contentView = web
        w.center()
        w.setFrameAutosaveName("LiveCaptionsDisplay")        // the same screen and size next time
        window = w
        webView = web
        web.load(URLRequest(url: url))
        w.makeKeyAndOrderFront(nil)
    }

    /// The orchestrator came back on another port: point an open display at it.
    func retarget(origin: URL) {
        guard let webView, let old = webView.url else { return }
        let url = origin.appendingPathComponent("ui/display.html")
        if old != url { webView.load(URLRequest(url: url)) }
    }

    func close() { window?.close() }

    func windowWillClose(_ notification: Notification) {
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "display")
        webView = nil
        window = nil
    }

    fileprivate func received(_ body: Any) {
        guard let window, let msg = body as? String else { return }
        let full = window.styleMask.contains(.fullScreen)
        switch msg {
        case "toggleFullScreen": window.toggleFullScreen(nil)
        case "exitFullScreen": if full { window.toggleFullScreen(nil) }
        default: break
        }
    }
}

/// WKUserContentController keeps its handler alive: a weak hop avoids keeping the window's
/// controller (and its web view) alive forever.
private final class MessageProxy: NSObject, WKScriptMessageHandler {
    weak var owner: DisplayWindowController?
    init(_ owner: DisplayWindowController) { self.owner = owner }
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        owner?.received(message.body)
    }
}
