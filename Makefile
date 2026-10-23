# Release pipeline for the Live RO2EN Mac app (docs/release.md).
#
#   make dist        runtime + app + dmg, then verify      (the one command)
#   make runtime     embedded Python (packaging/build_runtime.sh; rebuilt when the pins change)
#   make app         assemble and sign build/dist/Live RO2EN.app
#   make dmg         build/dist/LiveRO2EN-<version>.dmg
#   make verify      signature, Gatekeeper, Mach-O audit, smoke launch (on the DMG's copy)
#   make notarize    notarize + staple the app and the DMG (needs Developer ID + notary profile)
#   make clean       remove build/dist (keeps the runtime and the download cache)
#
# Signing: SIGN_IDENTITY (default: the first "Developer ID Application" identity in the
# keychain, else ad hoc). Notarization: NOTARY_PROFILE (default live-ro2en).
SHELL     := /bin/bash
BUILD     := build
RUNTIME   := $(BUILD)/runtime
DIST      := $(BUILD)/dist
APP       := $(DIST)/Live RO2EN.app
VERSION   := $(shell /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' macos/Info.plist)
DMG       := $(DIST)/LiveRO2EN-$(VERSION).dmg
STAMP     := $(RUNTIME)/.stamp
RUNTIME_INPUTS := requirements/nemo.txt requirements/mt.txt packaging/build_runtime.sh \
                  packaging/prune.txt packaging/audit_macho.sh

.PHONY: all dist runtime app dmg verify notarize clean
.DELETE_ON_ERROR:

all: dist

dist: dmg
	packaging/verify.sh "$(APP)" "$(DMG)"
	@echo; echo "dist: $(DMG)"

runtime: $(STAMP)

$(STAMP): $(RUNTIME_INPUTS)
	packaging/build_runtime.sh "$(RUNTIME)"
	@touch "$@"

app: $(STAMP)
	packaging/assemble_app.sh "$(RUNTIME)" "$(APP)"
	packaging/sign.sh "$(APP)"

dmg: app
	packaging/make_dmg.sh "$(APP)" "$(DIST)"

verify:
	@# the DMG only when it is newer than the app (after `make app` alone it holds the old bytes)
	packaging/verify.sh "$(APP)" $(if $(shell [ "$(DMG)" -nt "$(APP)" ] && echo y),"$(DMG)")

notarize:
	packaging/notarize.sh "$(APP)" "$(DIST)"
	REQUIRE_NOTARIZED=1 packaging/verify.sh "$(APP)" "$(DMG)"

clean:
	rm -rf "$(DIST)"
