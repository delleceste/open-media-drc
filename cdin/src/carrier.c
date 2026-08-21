#include "carrier.h"

void
carrier_init(struct carrier *c, int rate, int min_percent, int window_ms,
    double now)
{
	c->always = min_percent <= 0 || rate <= 0;
	c->min_hz = c->always ? 0.0
	    : (double)rate * (double)min_percent / 100.0;
	c->window_secs = window_ms > 0 ? (double)window_ms / 1000.0 : 2.0;
	c->last_hz = 0.0;
	carrier_reset(c, now);
}

void
carrier_reset(struct carrier *c, double now)
{
	c->window_start = now;
	c->frames = 0;
	c->live = c->always;
	c->judged = c->always;
}

enum carrier_verdict
carrier_feed(struct carrier *c, size_t frames, double now)
{
	double elapsed, hz;
	bool was;

	if (c->always)
		return CARRIER_SAME;

	c->frames += (uint64_t)frames;
	elapsed = now - c->window_start;
	if (elapsed < c->window_secs)
		return CARRIER_SAME;

	/*
	 * Close the window on the reading that completed it and open the next
	 * one from here, rather than from now + something: the periods are what
	 * drive this, and they are seconds apart precisely when the input is
	 * dead, which is the case that has to stay measurable.
	 */
	hz = elapsed > 0.0 ? (double)c->frames / elapsed : 0.0;
	c->last_hz = hz;
	c->window_start = now;
	c->frames = 0;

	was = c->live;
	c->live = hz >= c->min_hz;
	if (c->live == was && c->judged)
		return CARRIER_SAME;
	c->judged = true;
	return c->live ? CARRIER_BACK : CARRIER_LOST;
}

bool
carrier_live(const struct carrier *c)
{
	return c->live;
}

double
carrier_hz(const struct carrier *c)
{
	return c->last_hz;
}
