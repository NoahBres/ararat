#!/usr/bin/env expect
# Using local patched telegram plugin (fixes fire-and-forget mcp.notification bug)
# See: https://github.com/anthropics/claude-code/issues/42037
set script_dir [file dirname [file normalize [info script]]]
cd $script_dir
spawn claude --remote-control --name Ararat --dangerously-skip-permissions --dangerously-load-development-channels "server:telegram"
expect -re {Enter}
send "\r"
interact
