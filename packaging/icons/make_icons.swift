// Draws the two app icons from the operator UI's look (src/live/ui/styles/tokens.css) and
// writes the .icns files:   swift packaging/icons/make_icons.swift
//   macos/AppIcon.icns    Live RO2EN: the AirPlay screen with RO2EN on it (lime triangle, as when live)
//   remote/AppIcon.icns   Live Remote: a smaller AirPlay screen over the toolbar's keys, Hide lit amber
// plus packaging/icons/preview.png (both side by side).
import AppKit

let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

func hex(_ v: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((v >> 16) & 0xff) / 255, green: CGFloat((v >> 8) & 0xff) / 255, blue: CGFloat(v & 0xff) / 255, alpha: a)
}
let bg0 = hex(0x060d12), bg1 = hex(0x0a1a22), bg2 = hex(0x10262f), bg3 = hex(0x16323d)
let edge = hex(0x2b6f7e), cyan = hex(0x46d6ec), cyanHi = hex(0x9df1ff), cyanDim = hex(0x1f7f92)
let lime = hex(0xa3e635), amber = hex(0xf5b83d), label = hex(0xb9cbd0), mute = hex(0x6f8b93)

/// macOS icon grid: an 824-pt rounded square centred on 1024, with a soft drop shadow.
func plate(_ ctx: CGContext) -> CGRect {
    let r = CGRect(x: 100, y: 100, width: 824, height: 824)
    let path = CGPath(roundedRect: r, cornerWidth: 186, cornerHeight: 186, transform: nil)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: NSColor.black.withAlphaComponent(0.45).cgColor)
    ctx.addPath(path); ctx.setFillColor(bg1.cgColor); ctx.fillPath()
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addPath(path); ctx.clip()
    let g = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: [bg3.cgColor, bg2.cgColor, bg1.cgColor, bg0.cgColor] as CFArray,
                       locations: [0, 0.35, 0.8, 1])!
    ctx.drawRadialGradient(g, startCenter: CGPoint(x: 512, y: 560), startRadius: 0, endCenter: CGPoint(x: 512, y: 560), endRadius: 620, options: [.drawsAfterEndLocation])
    ctx.restoreGState()
    // the panel's cyan edge and inner hairline
    ctx.addPath(CGPath(roundedRect: r.insetBy(dx: 6, dy: 6), cornerWidth: 180, cornerHeight: 180, transform: nil))
    ctx.setStrokeColor(edge.cgColor); ctx.setLineWidth(10); ctx.strokePath()
    ctx.addPath(CGPath(roundedRect: r.insetBy(dx: 16, dy: 16), cornerWidth: 170, cornerHeight: 170, transform: nil))
    ctx.setStrokeColor(cyan.withAlphaComponent(0.10).cgColor); ctx.setLineWidth(4); ctx.strokePath()
    return r
}

func glow(_ ctx: CGContext, _ color: NSColor, _ blur: CGFloat, _ draw: () -> Void) {
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: blur, color: color.cgColor)
    draw()
    ctx.restoreGState()
}

/// The AirPlay mark: a screen with a triangle rising into its bottom edge, RO2EN on the screen
/// in the caption colour. The frame is cut around the triangle, as in the system symbol.
func airplay(_ ctx: CGContext, screen r: CGRect, line: CGFloat, triHalf: CGFloat, triBase: CGFloat, triTip: CGFloat, text: CGFloat) {
    let corner = r.height * 0.13
    let frame = CGPath(roundedRect: r, cornerWidth: corner, cornerHeight: corner, transform: nil)
    let tri = CGMutablePath()
    tri.move(to: CGPoint(x: r.midX, y: triTip))
    tri.addLine(to: CGPoint(x: r.midX - triHalf, y: triBase))
    tri.addLine(to: CGPoint(x: r.midX + triHalf, y: triBase))
    tri.closeSubpath()
    // the screen's glass (not under the gap round the triangle)
    let gap = tri.copy(strokingWithWidth: line * 3.2, lineCap: .round, lineJoin: .round, miterLimit: 10)
    ctx.saveGState()
    ctx.addRect(CGRect(x: 0, y: 0, width: 1024, height: 1024)); ctx.addPath(gap); ctx.addPath(tri)
    ctx.clip(using: .evenOdd)
    ctx.addPath(frame); ctx.clip()
    let g = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: [hex(0x0f2731).cgColor, hex(0x08161c).cgColor] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(g, start: CGPoint(x: r.midX, y: r.maxY), end: CGPoint(x: r.midX, y: r.minY), options: [])
    ctx.restoreGState()
    // the frame, cut around the triangle
    ctx.saveGState()
    ctx.addRect(CGRect(x: 0, y: 0, width: 1024, height: 1024)); ctx.addPath(gap); ctx.addPath(tri)
    ctx.clip(using: .evenOdd)
    glow(ctx, cyan.withAlphaComponent(0.75), line * 1.2) {
        ctx.addPath(frame); ctx.setStrokeColor(cyan.cgColor); ctx.setLineWidth(line); ctx.strokePath()
    }
    ctx.restoreGState()
    // the triangle, lime as the dial's while captioning
    glow(ctx, lime.withAlphaComponent(0.7), line * 0.9) {
        ctx.addPath(tri); ctx.setFillColor(lime.cgColor); ctx.fillPath()
    }
    // RO2EN on the screen, above the triangle
    let font = NSFont.systemFont(ofSize: text, weight: .heavy)
    let rounded = font.fontDescriptor.withDesign(.rounded).flatMap { NSFont(descriptor: $0, size: text) } ?? font
    let str = NSAttributedString(string: "RO2EN", attributes: [.font: rounded, .foregroundColor: cyanHi, .kern: text * 0.02])
    let ts = str.size()
    let top = r.maxY - line / 2, bottom = max(r.minY + line / 2, triTip)
    let y = bottom + (top - bottom - ts.height) / 2 + text * 0.04
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
    glow(ctx, cyan.withAlphaComponent(0.8), text * 0.2) {
        str.draw(at: CGPoint(x: r.midX - ts.width / 2, y: y))
    }
    NSGraphicsContext.restoreGraphicsState()
}

/// Live RO2EN: the AirPlay screen showing RO2EN.
func drawDial(_ ctx: CGContext) {
    _ = plate(ctx)
    airplay(ctx, screen: CGRect(x: 202, y: 360, width: 620, height: 410), line: 30,
            triHalf: 150, triBase: 236, triTip: 452, text: 150)
}

enum Glyph { case eyeSlash, pause, clear, undo }

/// One toolbar key: gradient tile, edge, and its glyph drawn as vectors (crisp at 16 px).
func key(_ ctx: CGContext, _ r: CGRect, glyph: Glyph, lit: NSColor?) {
    let path = CGPath(roundedRect: r, cornerWidth: 44 * r.width / 230, cornerHeight: 44 * r.width / 230, transform: nil)
    let (top, bottom, stroke, ink): (NSColor, NSColor, NSColor, NSColor) = lit == amber
        ? (hex(0x5a4012), hex(0x2a1e08), amber, hex(0xffe1a6))
        : (hex(0x1d2428), hex(0x0f1316), hex(0x34404a), label)
    if lit == amber {
        glow(ctx, amber.withAlphaComponent(0.7), 40 * r.width / 230) { ctx.addPath(path); ctx.setFillColor(bottom.cgColor); ctx.fillPath() }
    }
    ctx.saveGState()
    ctx.addPath(path); ctx.clip()
    let g = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: [top.cgColor, bottom.cgColor] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(g, start: CGPoint(x: r.midX, y: r.maxY), end: CGPoint(x: r.midX, y: r.minY), options: [])
    ctx.restoreGState()
    ctx.addPath(path); ctx.setStrokeColor(stroke.cgColor); ctx.setLineWidth((lit == amber ? 9 : 6) * max(0.75, r.width / 230)); ctx.strokePath()

    let c = CGPoint(x: r.midX, y: r.midY), u = r.width / 230      // glyphs are drawn for a 230-pt key
    ctx.saveGState()
    ctx.setStrokeColor(ink.cgColor); ctx.setFillColor(ink.cgColor)
    ctx.setLineWidth(15 * u); ctx.setLineCap(.round); ctx.setLineJoin(.round)
    switch glyph {
    case .eyeSlash:
        let w = 74 * u, h = 44 * u
        ctx.move(to: CGPoint(x: c.x - w, y: c.y))
        ctx.addQuadCurve(to: CGPoint(x: c.x + w, y: c.y), control: CGPoint(x: c.x, y: c.y + 2 * h))
        ctx.addQuadCurve(to: CGPoint(x: c.x - w, y: c.y), control: CGPoint(x: c.x, y: c.y - 2 * h))
        ctx.strokePath()
        ctx.fillEllipse(in: CGRect(x: c.x - 22 * u, y: c.y - 22 * u, width: 44 * u, height: 44 * u))
        // the slash, with a gap cut in the tile colour
        ctx.setStrokeColor(bottom.cgColor); ctx.setLineWidth(34 * u)
        ctx.move(to: CGPoint(x: c.x - 66 * u, y: c.y + 66 * u)); ctx.addLine(to: CGPoint(x: c.x + 66 * u, y: c.y - 66 * u)); ctx.strokePath()
        ctx.setStrokeColor(ink.cgColor); ctx.setLineWidth(15 * u)
        ctx.move(to: CGPoint(x: c.x - 66 * u, y: c.y + 66 * u)); ctx.addLine(to: CGPoint(x: c.x + 66 * u, y: c.y - 66 * u)); ctx.strokePath()
    case .pause:
        for dx in [-30.0, 30.0] {
            ctx.addPath(CGPath(roundedRect: CGRect(x: c.x + CGFloat(dx) * u - 17 * u, y: c.y - 62 * u, width: 34 * u, height: 124 * u),
                               cornerWidth: 9 * u, cornerHeight: 9 * u, transform: nil))
            ctx.fillPath()
        }
    case .clear:
        ctx.addPath(CGPath(roundedRect: CGRect(x: c.x - 78 * u, y: c.y - 56 * u, width: 156 * u, height: 112 * u),
                           cornerWidth: 18 * u, cornerHeight: 18 * u, transform: nil))
        ctx.strokePath()
        ctx.move(to: CGPoint(x: c.x - 26 * u, y: c.y + 26 * u)); ctx.addLine(to: CGPoint(x: c.x + 26 * u, y: c.y - 26 * u))
        ctx.move(to: CGPoint(x: c.x + 26 * u, y: c.y + 26 * u)); ctx.addLine(to: CGPoint(x: c.x - 26 * u, y: c.y - 26 * u))
        ctx.strokePath()
    case .undo:
        // ↶: a hook from the top left round to the bottom, arrowhead on the left
        ctx.move(to: CGPoint(x: c.x - 60 * u, y: c.y + 30 * u))
        ctx.addLine(to: CGPoint(x: c.x + 20 * u, y: c.y + 30 * u))
        ctx.addArc(center: CGPoint(x: c.x + 20 * u, y: c.y - 15 * u), radius: 45 * u, startAngle: .pi / 2, endAngle: -.pi / 2, clockwise: true)
        ctx.addLine(to: CGPoint(x: c.x - 20 * u, y: c.y - 60 * u))
        ctx.strokePath()
        ctx.move(to: CGPoint(x: c.x - 26 * u, y: c.y + 66 * u))
        ctx.addLine(to: CGPoint(x: c.x - 62 * u, y: c.y + 30 * u))
        ctx.addLine(to: CGPoint(x: c.x - 26 * u, y: c.y - 6 * u))
        ctx.strokePath()
    }
    ctx.restoreGState()
}

/// Live Remote: a smaller AirPlay screen over three of the toolbar's keys (Hide lit amber, Hold, Clear).
func drawRemote(_ ctx: CGContext) {
    _ = plate(ctx)
    airplay(ctx, screen: CGRect(x: 262, y: 500, width: 500, height: 300), line: 24,
            triHalf: 112, triBase: 408, triTip: 568, text: 112)
    let k: CGFloat = 168, gap: CGFloat = 26, x0: CGFloat = 512 - (3 * k + 2 * gap) / 2, y0: CGFloat = 190
    for (i, (g, lit)) in [(Glyph.eyeSlash, amber), (.pause, nil), (.clear, nil)].enumerated() {
        key(ctx, CGRect(x: x0 + CGFloat(i) * (k + gap), y: y0, width: k, height: k), glyph: g, lit: lit)
    }
}

func render(_ size: Int, _ draw: (CGContext) -> Void) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    ctx.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)
    ctx.interpolationQuality = .high
    draw(ctx)
    return rep
}

func icns(_ out: URL, _ draw: (CGContext) -> Void) throws {
    let set = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".iconset")
    try FileManager.default.createDirectory(at: set, withIntermediateDirectories: true)
    for base in [16, 32, 128, 256, 512] {
        for scale in [1, 2] {
            let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
            try render(base * scale, draw).representation(using: .png, properties: [:])!.write(to: set.appendingPathComponent(name))
        }
    }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
    p.arguments = ["-c", "icns", set.path, "-o", out.path]
    try p.run(); p.waitUntilExit()
    try? FileManager.default.removeItem(at: set)
    guard p.terminationStatus == 0 else { throw NSError(domain: "iconutil", code: Int(p.terminationStatus)) }
    print("wrote \(out.path)")
}

try icns(repo.appendingPathComponent("macos/AppIcon.icns"), drawDial)
try icns(repo.appendingPathComponent("remote/AppIcon.icns"), drawRemote)

// preview: both at 512, side by side, and at 64 / 32 under them
let W = 1100, H = 700
let prev = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: W, pixelsHigh: H, bitsPerSample: 8, samplesPerPixel: 4,
                            hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
let pc = NSGraphicsContext(bitmapImageRep: prev)!.cgContext
pc.setFillColor(hex(0x1c1e21).cgColor); pc.fill(CGRect(x: 0, y: 0, width: W, height: H))
for (i, d) in [drawDial, drawRemote].enumerated() {
    let x = 30 + i * 540
    pc.draw(render(512, d).cgImage!, in: CGRect(x: x, y: 160, width: 512, height: 512))
    pc.draw(render(128, d).cgImage!, in: CGRect(x: x + 120, y: 20, width: 128, height: 128))
    pc.draw(render(64, d).cgImage!, in: CGRect(x: x + 280, y: 52, width: 64, height: 64))
    pc.draw(render(32, d).cgImage!, in: CGRect(x: x + 370, y: 68, width: 32, height: 32))
    pc.draw(render(16, d).cgImage!, in: CGRect(x: x + 420, y: 76, width: 16, height: 16))
}
try prev.representation(using: .png, properties: [:])!.write(to: repo.appendingPathComponent("packaging/icons/preview.png"))
print("wrote packaging/icons/preview.png")
