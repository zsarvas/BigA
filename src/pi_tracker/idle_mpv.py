"""
Fullscreen idle highlight clips (mpv) were **removed** from the main app loop to keep
Pi / fbcon + pygame stable. Restore the previous implementation from git history if you
revisit mpv once the display stack is solid.
"""
