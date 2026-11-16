// swift-tools-version:5.10
// Live Remote: the toolbar for the ProPresenter Mac. `./build.sh` builds "Live Remote.app".
import PackageDescription

let package = Package(
    name: "LiveRemote",
    platforms: [.macOS(.v14)],
    // Sparkle 2: software updates (docs/release.md "Updates"). Pinned exactly; the .app carries
    // Sparkle.framework in Contents/Frameworks (packaging/embed_sparkle.sh).
    dependencies: [.package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1")],
    targets: [
        .executableTarget(
            name: "LiveRemote",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/LiveRemote",
            linkerSettings: [.linkedFramework("AppKit"), .linkedFramework("Carbon"), .linkedFramework("WebKit"),
                             .unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        )
    ],
    swiftLanguageVersions: [.v5]
)
