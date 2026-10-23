#ifndef OMDRC_PCM_QUEUE_H
#define OMDRC_PCM_QUEUE_H
#include <stddef.h>
#include <stdint.h>
struct pcm_queue {
    unsigned char *data;
    size_t capacity, head, used, high_water;
    uint64_t accepted, delivered, discarded;
};
int pcm_queue_init(struct pcm_queue *, size_t);
void pcm_queue_free(struct pcm_queue *);
size_t pcm_queue_write(struct pcm_queue *, const void *, size_t);
size_t pcm_queue_peek(const struct pcm_queue *, void *, size_t);
void pcm_queue_consume(struct pcm_queue *, size_t);
void pcm_queue_reset(struct pcm_queue *);
#endif
