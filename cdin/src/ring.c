#include <pthread.h>
#include <stdlib.h>
#include <string.h>

#include "ring.h"

struct ring {
	unsigned char  *buf;
	size_t          cap;		/* frame-aligned */
	size_t          frame_bytes;
	size_t          head;		/* write offset */
	size_t          tail;		/* read offset */
	size_t          fill;
	bool            down;
	bool            eof;		/* producer finished; buffered data still valid */
	pthread_mutex_t mu;
	pthread_cond_t  cv;		/* signalled on fill change and on shutdown */
};

struct ring *
ring_new(size_t capacity_bytes, size_t frame_bytes)
{
	struct ring *r;

	if (frame_bytes == 0)
		return NULL;
	capacity_bytes -= capacity_bytes % frame_bytes;
	if (capacity_bytes == 0)
		return NULL;

	if ((r = calloc(1, sizeof(*r))) == NULL)
		return NULL;
	if ((r->buf = malloc(capacity_bytes)) == NULL) {
		free(r);
		return NULL;
	}
	r->cap = capacity_bytes;
	r->frame_bytes = frame_bytes;
	pthread_mutex_init(&r->mu, NULL);
	pthread_cond_init(&r->cv, NULL);
	return r;
}

void
ring_free(struct ring *r)
{
	if (r == NULL)
		return;
	pthread_cond_destroy(&r->cv);
	pthread_mutex_destroy(&r->mu);
	free(r->buf);
	free(r);
}

static void
copy_in(struct ring *r, const unsigned char *src, size_t n)
{
	size_t first = r->cap - r->head;

	if (first > n)
		first = n;
	memcpy(r->buf + r->head, src, first);
	if (n > first)
		memcpy(r->buf, src + first, n - first);
	r->head = (r->head + n) % r->cap;
	r->fill += n;
}

static void
copy_out(struct ring *r, unsigned char *dst, size_t n)
{
	size_t first = r->cap - r->tail;

	if (first > n)
		first = n;
	memcpy(dst, r->buf + r->tail, first);
	if (n > first)
		memcpy(dst + first, r->buf, n - first);
	r->tail = (r->tail + n) % r->cap;
	r->fill -= n;
}

size_t
ring_write(struct ring *r, const void *buf, size_t n)
{
	size_t dropped = 0;

	if (n > r->cap) {		/* pathological: keep only the newest */
		dropped = n - r->cap;
		buf = (const unsigned char *)buf + dropped;
		n = r->cap;
	}

	pthread_mutex_lock(&r->mu);
	if (r->fill + n > r->cap) {
		size_t need = r->fill + n - r->cap;

		r->tail = (r->tail + need) % r->cap;
		r->fill -= need;
		dropped += need;
	}
	copy_in(r, buf, n);
	pthread_cond_broadcast(&r->cv);
	pthread_mutex_unlock(&r->mu);
	return dropped;
}

size_t
ring_write_full(struct ring *r, const void *buf, size_t n)
{
	const unsigned char *src = buf;
	size_t done = 0;

	pthread_mutex_lock(&r->mu);
	while (done < n && !r->down) {
		size_t space = r->cap - r->fill, chunk;

		if (space == 0) {
			pthread_cond_wait(&r->cv, &r->mu);
			continue;
		}
		chunk = n - done;
		if (chunk > space)
			chunk = space;
		copy_in(r, src + done, chunk);
		done += chunk;
		pthread_cond_broadcast(&r->cv);
	}
	pthread_mutex_unlock(&r->mu);
	return done;
}

size_t
ring_read(struct ring *r, void *buf, size_t n)
{
	pthread_mutex_lock(&r->mu);
	while (r->fill < n && !r->down)
		pthread_cond_wait(&r->cv, &r->mu);
	if (r->down && r->fill < n) {
		pthread_mutex_unlock(&r->mu);
		return 0;
	}
	copy_out(r, buf, n);
	pthread_cond_broadcast(&r->cv);
	pthread_mutex_unlock(&r->mu);
	return n;
}

size_t
ring_read_some(struct ring *r, void *buf, size_t n)
{
	size_t got;

	if (n > r->cap)			/* would otherwise wait for the impossible */
		n = r->cap;
	pthread_mutex_lock(&r->mu);
	while (r->fill < n && !r->down && !r->eof)
		pthread_cond_wait(&r->cv, &r->mu);
	got = r->fill < n ? r->fill : n;
	got -= got % r->frame_bytes;
	if (got > 0) {
		copy_out(r, buf, got);
		pthread_cond_broadcast(&r->cv);
	}
	pthread_mutex_unlock(&r->mu);
	return got;
}

void
ring_set_eof(struct ring *r)
{
	pthread_mutex_lock(&r->mu);
	r->eof = true;
	pthread_cond_broadcast(&r->cv);
	pthread_mutex_unlock(&r->mu);
}

bool
ring_is_eof(struct ring *r)
{
	bool v;

	pthread_mutex_lock(&r->mu);
	v = r->eof;
	pthread_mutex_unlock(&r->mu);
	return v;
}

bool
ring_wait_fill(struct ring *r, size_t n)
{
	bool ok;

	if (n > r->cap)
		n = r->cap;
	pthread_mutex_lock(&r->mu);
	while (r->fill < n && !r->down)
		pthread_cond_wait(&r->cv, &r->mu);
	ok = !r->down;
	pthread_mutex_unlock(&r->mu);
	return ok;
}

size_t
ring_fill(struct ring *r)
{
	size_t n;

	pthread_mutex_lock(&r->mu);
	n = r->fill;
	pthread_mutex_unlock(&r->mu);
	return n;
}

size_t
ring_capacity(struct ring *r)
{
	return r->cap;
}

void
ring_shutdown(struct ring *r)
{
	pthread_mutex_lock(&r->mu);
	r->down = true;
	pthread_cond_broadcast(&r->cv);
	pthread_mutex_unlock(&r->mu);
}

void
ring_reset(struct ring *r)
{
	pthread_mutex_lock(&r->mu);
	r->head = r->tail = r->fill = 0;
	r->down = r->eof = false;
	pthread_cond_broadcast(&r->cv);
	pthread_mutex_unlock(&r->mu);
}
