import AppKit
import WebKit

/// The captions display: ui/display.html in its own window, to drag onto a second screen or
/// projector and make full screen (double-click; Esc leaves full screen). One window at most;
/// it remembers where it was, so it comes back on the same screen next time.
/// It never stays blank on the projector: a crashed web content process is reloaded, and a page
/// that fails to load (the orchestrator restarting) is tried again every second.
final class DisplayWindowController: NSObject, NSWindowDelegate, WKNavigationDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?
    /// The page the display should show. webView.url is nil or stale after a failed load or a
    /// web content crash, so retarget and the retries use this instead.
    private var target: URL?
    private var retry: DispatchWorkItem?

    /// Open (or bring forward) the display at `url` (http://127.0.0.1:<port>/ui/display.html).
    func show(_ url: URL) {
        if let window, webView != nil {
            if target != url { load(url) }
            window.makeKeyAndOrderFront(nil)
            return
        }
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(MessageProxy(self), name: "display")
        let web = WKWebView(frame: .zero, configuration: cfg)
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
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
        load(url)
        w.makeKeyAndOrderFront(nil)
    }

    /// The orchestrator came back on another port: point an open display at it.
    func retarget(origin: URL) {
        guard webView != nil, target != nil else { return }
        let url = origin.appendingPathComponent("ui/display.html")
        if target != url { load(url) }
    }

    func close() { window?.close() }

    func windowWillClose(_ notification: Notification) {
        retry?.cancel(); retry = nil
        webView?.navigationDelegate = nil
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "display")
        webView = nil
        window = nil
        target = nil
    }

    private func load(_ url: URL) {
        retry?.cancel(); retry = nil
        target = url
        webView?.load(URLRequest(url: url))
    }

    /// Load the intended page again in 1 s (unless something else loads first).
    private func retryLater() {
        retry?.cancel()
        let item = DispatchWorkItem { [weak self] in
            guard let self, let url = self.target else { return }
            self.load(url)
        }
        retry = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 1, execute: item)
    }

    // MARK: WKNavigationDelegate

    /// The web content process crashed (or was killed for memory): the view is blank now.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        ShellLog.write("captions display: web content process ended; reloading")
        guard let url = target else { return }
        load(url)
    }

    /// The page could not be reached (e.g. the orchestrator is restarting): try again.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }   // replaced by a newer load
        retryLater()
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
