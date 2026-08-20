#include "gate.h"

void
gate_init(struct silence_gate *g, int idle_after_ms, int rate)
{
	g->idle_after_frames = idle_after_ms > 0 && rate > 0
	    ? (uint64_t)idle_after_ms * (uint64_t)rate / 1000u : 0;
	g->silent_frames = 0;
	g->idle = false;
}

void
gate_reset(struct silence_gate *g)
{
	g->silent_frames = 0;
	g->idle = false;
}

uint64_t
gate_silent_frames(const struct silence_gate *g)
{
	return g->silent_frames;
}

enum gate_verdict
gate_feed(struct silence_gate *g, bool silent, size_t frames)
{
	if (!silent) {
		g->silent_frames = 0;
		g->idle = false;
		return GATE_AUDIO;
	}

	g->silent_frames += frames;
	/* Edge, not level: the caller acts on the transition, and reporting it
	   again every period afterwards would make it re-act every 23 ms. */
	if (g->idle_after_frames != 0 && !g->idle &&
	    g->silent_frames >= g->idle_after_frames) {
		g->idle = true;
		return GATE_IDLE;
	}
	return GATE_SILENT;
}
