LABEL       := com.magpie.stash-server
SERVER      := $(CURDIR)/magpie_server.py
PLIST_NAME  := $(LABEL).plist
PLIST_DIR   := $(HOME)/Library/LaunchAgents
PLIST_DEST  := $(PLIST_DIR)/$(PLIST_NAME)
LOG         := /tmp/magpie-server.log

.PHONY: install uninstall reinstall start stop restart status check logs help \
        ibroadcast-login ibroadcast-status

help: ## Show available targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | column -t -s '	'

check: ## Verify dependencies (python3, yt-dlp, ffmpeg)
	@echo "Checking dependencies..."
	@command -v python3  >/dev/null || { echo "  MISSING: python3";  exit 1; }
	@command -v yt-dlp   >/dev/null || { echo "  MISSING: yt-dlp";   exit 1; }
	@command -v ffmpeg   >/dev/null || { echo "  MISSING: ffmpeg";   exit 1; }
	@command -v ffprobe  >/dev/null || { echo "  MISSING: ffprobe";  exit 1; }
	@echo "  python3:  $$(command -v python3)"
	@echo "  yt-dlp:   $$(command -v yt-dlp)"
	@echo "  ffmpeg:   $$(command -v ffmpeg)"
	@echo "  ffprobe:  $$(command -v ffprobe)"
	@echo "All dependencies found."

install: check ## Install and start the LaunchAgent
	@chmod +x "$(SERVER)"
	@mkdir -p "$(PLIST_DIR)"
	@echo '<?xml version="1.0" encoding="UTF-8"?>'                                          >  "$(PLIST_DEST)"
	@echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'                             >> "$(PLIST_DEST)"
	@echo '  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'                             >> "$(PLIST_DEST)"
	@echo '<plist version="1.0">'                                                            >> "$(PLIST_DEST)"
	@echo '<dict>'                                                                           >> "$(PLIST_DEST)"
	@echo '  <key>Label</key>'                                                               >> "$(PLIST_DEST)"
	@echo '  <string>$(LABEL)</string>'                                                      >> "$(PLIST_DEST)"
	@echo '  <key>ProgramArguments</key>'                                                    >> "$(PLIST_DEST)"
	@echo '  <array>'                                                                        >> "$(PLIST_DEST)"
	@echo '    <string>/usr/bin/env</string>'                                                >> "$(PLIST_DEST)"
	@echo '    <string>python3</string>'                                                     >> "$(PLIST_DEST)"
	@echo '    <string>$(SERVER)</string>'                                                   >> "$(PLIST_DEST)"
	@echo '  </array>'                                                                       >> "$(PLIST_DEST)"
	@echo '  <key>RunAtLoad</key>'                                                           >> "$(PLIST_DEST)"
	@echo '  <true/>'                                                                        >> "$(PLIST_DEST)"
	@echo '  <key>KeepAlive</key>'                                                           >> "$(PLIST_DEST)"
	@echo '  <true/>'                                                                        >> "$(PLIST_DEST)"
	@echo '  <key>StandardOutPath</key>'                                                     >> "$(PLIST_DEST)"
	@echo '  <string>$(LOG)</string>'                                                        >> "$(PLIST_DEST)"
	@echo '  <key>StandardErrorPath</key>'                                                   >> "$(PLIST_DEST)"
	@echo '  <string>$(LOG)</string>'                                                        >> "$(PLIST_DEST)"
	@echo '</dict>'                                                                          >> "$(PLIST_DEST)"
	@echo '</plist>'                                                                         >> "$(PLIST_DEST)"
	@launchctl bootout gui/$$(id -u) "$(PLIST_DEST)" 2>/dev/null || true
	@launchctl bootstrap gui/$$(id -u) "$(PLIST_DEST)"
	@echo "Installed and started: $(PLIST_DEST)"
	@echo "Server script: $(SERVER)"
	@echo "Logs: $(LOG)"

uninstall: ## Stop server and remove LaunchAgent
	@launchctl bootout gui/$$(id -u) "$(PLIST_DEST)" 2>/dev/null || true
	@rm -f "$(PLIST_DEST)"
	@echo "Uninstalled: $(PLIST_DEST)"

reinstall: uninstall install ## Uninstall then install fresh

start: ## Start the server
	@launchctl kickstart gui/$$(id -u)/$(LABEL)
	@echo "Started."

stop: ## Stop the server
	@launchctl kill SIGTERM gui/$$(id -u)/$(LABEL) 2>/dev/null || true
	@echo "Stopped."

restart: stop start ## Restart the server

status: ## Check if server is running
	@if curl -sf http://127.0.0.1:7865/health >/dev/null 2>&1; then \
		echo "Server is running (port 7865)."; \
	else \
		echo "Server is NOT responding."; \
		launchctl print gui/$$(id -u)/$(LABEL) 2>/dev/null | grep -E 'state|pid' || echo "LaunchAgent not loaded."; \
	fi

logs: ## Tail the server log
	@tail -f "$(LOG)"

ibroadcast-login: ## Authorize iBroadcast backup (one time, opens a code prompt)
	@python3 -u "$(CURDIR)/magpie_ibroadcast.py" login

ibroadcast-status: ## Check whether iBroadcast backup is connected
	@python3 "$(CURDIR)/magpie_ibroadcast.py" status || true
