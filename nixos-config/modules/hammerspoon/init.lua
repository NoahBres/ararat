-- 3-finger trackpad swipe left/right cycles Ghostty tabs.
-- Uses the vendored Swipe.spoon (https://github.com/mogenson/Swipe.spoon) for
-- raw gesture detection; see its README for avoiding conflicts with macOS's
-- own System Settings -> Trackpad gestures.

local GHOSTTY_BUNDLE_ID = "com.mitchellh.ghostty"
local SWIPE_THRESHOLD = 0.15 -- fraction of trackpad width before triggering

local Swipe = hs.loadSpoon("Swipe")

local currentSwipeId, triggered

Swipe:start(3, function(direction, distance, id)
    if id ~= currentSwipeId then
        currentSwipeId = id
        triggered = false
    end

    if triggered then return end
    if direction ~= "left" and direction ~= "right" then return end
    if distance < SWIPE_THRESHOLD then return end

    local frontApp = hs.application.frontmostApplication()
    if not frontApp or frontApp:bundleID() ~= GHOSTTY_BUNDLE_ID then return end

    triggered = true -- only fire once per physical swipe

    if direction == "right" then
        hs.eventtap.keyStroke({ "cmd", "shift" }, "]") -- next tab
    else
        hs.eventtap.keyStroke({ "cmd", "shift" }, "[") -- previous tab
    end
end)
