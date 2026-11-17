#!/usr/bin/env bash
# Virtual microphone for Meet (Linux, PipeWire or PulseAudio).
#
#   mpv ──> meetbot_sink (null sink) ──monitor──> meetbot_mic (remapped source) ──> Chrome/Meet
#
# Chrome doesn't offer monitor sources as microphones, hence the remapped source.
# Safe to run repeatedly: only missing pieces are created.
#
# Usage: scripts/setup-audio.sh [up|down|status]   (default: up)
set -euo pipefail

SINK=meetbot_sink
SOURCE=meetbot_mic
export LC_ALL=C # stable `pactl info` field names

command -v pactl >/dev/null || {
	echo "error: pactl not found. Install it with: sudo apt install pulseaudio-utils" >&2
	exit 1
}

# awk reads all input (no early exit), so pactl never gets SIGPIPE under pipefail.
has_sink() { pactl list short sinks | awk -v n="$SINK" '$2 == n {f = 1} END {exit !f}'; }
has_source() { pactl list short sources | awk -v n="$SOURCE" '$2 == n {f = 1} END {exit !f}'; }
default_sink() { pactl info | sed -n 's/^Default Sink: //p'; }
default_source() { pactl info | sed -n 's/^Default Source: //p'; }

status() {
	local ok=0
	if has_sink; then s=present; else s=MISSING ok=1; fi
	if has_source; then m=present; else m=MISSING ok=1; fi
	echo "Playback target (mpv plays here): $SINK  [$s]"
	echo "Virtual mic (Chrome uses this):   $SOURCE  [$m]"
	echo "Default mic:    $(default_source)"
	echo "Default output: $(default_sink)"
	if [ "$(default_source)" != "$SOURCE" ]; then
		echo "warning: the default mic is not $SOURCE" >&2
		ok=1
	fi
	if [ "$(default_sink)" = "$SINK" ]; then
		echo "warning: the default output is $SINK, so meeting audio would loop into the mic" >&2
		ok=1
	fi
	return $ok
}

up() {
	if ! has_sink; then
		pactl load-module module-null-sink sink_name="$SINK" \
			sink_properties=device.description="$SINK" >/dev/null
	fi
	if ! has_source; then
		pactl load-module module-remap-source master="$SINK.monitor" source_name="$SOURCE" \
			source_properties=device.description="$SOURCE" >/dev/null
	fi
	pactl set-default-source "$SOURCE"

	# If the null sink became the default output, Chrome would play the meeting into the mic.
	if [ "$(default_sink)" = "$SINK" ]; then
		other=$(pactl list short sinks | awk -v n="$SINK" '$2 != n && !d {print $2; d = 1}')
		if [ -z "$other" ]; then
			echo "error: the default output is $SINK and there is no other output to switch to" >&2
			exit 1
		fi
		pactl set-default-sink "$other"
		echo "Default output was $SINK; switched it to $other."
	fi
	status
}

down() {
	# Match whole argument tokens on our two module types only, so e.g. a sink named
	# meetbot_sink_backup is left alone. Lines are: ID, module name, args.
	# Unload the remap source before the sink it reads from (higher module IDs first).
	pactl list short modules |
		awk -v s="sink_name=$SINK" -v m="source_name=$SOURCE" '
			{
				for (i = 3; i <= NF; i++) {
					t = $i
					gsub(/["\047]/, "", t)
					if (($2 == "module-null-sink" && t == s) || ($2 == "module-remap-source" && t == m)) {
						print $1
						break
					}
				}
			}' |
		sort -rn |
		while read -r id; do pactl unload-module "$id"; done
	echo "Removed $SOURCE and $SINK."
}

case "${1:-up}" in
up) up ;;
down) down ;;
status) status ;;
*)
	echo "usage: $0 [up|down|status]" >&2
	exit 2
	;;
esac
