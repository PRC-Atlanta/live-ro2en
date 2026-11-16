// swift-tools-version:5.10
// The Live RO2EN Mac shell. `./build.sh` builds it and assembles the ad-hoc signed .app.
import PackageDescription

let package = Package(
    name: "LiveRO2EN",
    platforms: [.macOS(.v14)],
    // Sparkle 2: software updates (docs/release.md "Updates"). Pinned exactly; the .app carries
    // Sparkle.framework in Contents/Frameworks (packaging/embed_sparkle.sh).
    dependencies: [.package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1")],
    targets: [
        .executableTarget(
            name: "LiveRO2EN",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/LiveRO2EN",
            linkerSettings: [.linkedFramework("WebKit"), .linkedFramework("AppKit"),
                             .unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        )
    ],
    swiftLanguageVersions: [.v5]
)
