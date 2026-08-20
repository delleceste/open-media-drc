/*
 * omdrc-cdin — OSS device open / configure / transfer.
 *
 * The ioctl order and the SETFRAGMENT log2 encoding follow BruteFIR's
 * bfio_oss.c (see ../../../brutefir/src/bfio_oss.c:66): the low 16 bits of the
 * SNDCTL_DSP_SETFRAGMENT argument are log2 of the fragment size in BYTES, not
 * the byte count.  Passing the raw count is misread by the OSS API and is a
 * bug worth not rediscovering.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <sys/types.h>

struct ossdev {
	int    fd;
	char   path[1024];
	bool   capture;
	int    rate;
	int    channels;
	int    bits;
	size_t frame_bytes;
	size_t period_frames;	/* requested */
	size_t hw_period_frames;	/* what the device reported back */
};

/*
 * Open and fully configure a device.  OSS fragments are powers of two, but
 * application periods need not be: packed 24-bit stereo has six-byte frames,
 * so requiring both would make that advertised format impossible.
 * On failure returns -1, leaves d->fd == -1, and fills err.
 */
int  ossdev_open(struct ossdev *d, const char *path, bool capture, int rate,
	    int channels, int bits, size_t period_frames, char *err,
	    size_t errsz);

void ossdev_close(struct ossdev *d);

/*
 * Transfer exactly n bytes, retrying partial transfers.  Return n on success,
 * 0 if interrupted by a signal with nothing transferred (shutdown), or -1 on a
 * device error with errno set.
 */
ssize_t ossdev_read_full(struct ossdev *d, void *buf, size_t n);
ssize_t ossdev_write_full(struct ossdev *d, const void *buf, size_t n);

/* Pure helper exposed for fragment-size regression tests. */
size_t ossdev_fragment_bytes(size_t period_bytes);
