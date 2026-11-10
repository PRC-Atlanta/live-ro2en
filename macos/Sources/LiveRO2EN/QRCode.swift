import AppKit
import CoreImage
import WebKit

/// The listeners' address as a QR code (Settings → Live Voice). Drawn here with CoreImage, so the
/// page needs no library and nothing leaves the Mac.
/// The page's side (screens/settings/index.js): `await webkit.messageHandlers.qrCode.postMessage({text})`
/// answers a "data:image/png;base64,…" string, or null.
final class QRCodeBridge: NSObject, WKScriptMessageHandlerWithReply {
    static let maxLength = 300                     // an address, never a document

    static func png(_ text: String, side: CGFloat = 672) -> Data? {
        guard !text.isEmpty, text.utf8.count <= maxLength,
              let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        filter.setValue(Data(text.utf8), forKey: "inputMessage")
        filter.setValue("M", forKey: "inputCorrectionLevel")
        guard let out = filter.outputImage, out.extent.width > 0 else { return nil }
        let scale = max(1, (side / out.extent.width).rounded(.down))      // whole pixels per module: sharp edges
        let big = out.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let cg = CIContext().createCGImage(big, from: big.extent) else { return nil }
        return NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:])
    }

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.frameInfo.isMainFrame,
              let text = (message.body as? [String: Any])?["text"] as? String,
              text.hasPrefix("http://"),
              let data = Self.png(text) else { replyHandler(NSNull(), nil); return }
        replyHandler("data:image/png;base64," + data.base64EncodedString(), nil)
    }
}

/// "Print the card" (Settings → Live Voice): WKWebView does not act on window.print() by itself, so
/// the page asks the app: `webkit.messageHandlers.printPage.postMessage({})`. The page's own print
/// stylesheet leaves only the card.
final class PrintBridge: NSObject, WKScriptMessageHandler {
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let web = message.webView, let window = web.window else { return }
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isHorizontallyCentered = true
        let op = web.printOperation(with: info)
        op.view?.frame = web.bounds                    // without a frame the operation prints blank pages
        op.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }
}
