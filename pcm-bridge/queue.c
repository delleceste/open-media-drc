#include "queue.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
int pcm_queue_init(struct pcm_queue *q, size_t capacity)
{
    memset(q, 0, sizeof(*q));
    if (!capacity || !(q->data = malloc(capacity))) return -1;
    q->capacity = capacity;
    return 0;
}
void pcm_queue_free(struct pcm_queue *q) { free(q->data); memset(q, 0, sizeof(*q)); }
size_t pcm_queue_write(struct pcm_queue *q, const void *src, size_t n)
{
    if (n > q->capacity - q->used) n = q->capacity - q->used;
    size_t tail = (q->head + q->used) % q->capacity;
    size_t first = n < q->capacity - tail ? n : q->capacity - tail;
    memcpy(q->data + tail, src, first);
    memcpy(q->data, (const unsigned char *)src + first, n - first);
    q->used += n; q->accepted += n;
    if (q->used > q->high_water) q->high_water = q->used;
    return n;
}
size_t pcm_queue_peek(const struct pcm_queue *q, void *dst, size_t n)
{
    if (n > q->used) n = q->used;
    size_t first = n < q->capacity - q->head ? n : q->capacity - q->head;
    memcpy(dst, q->data + q->head, first);
    memcpy((unsigned char *)dst + first, q->data, n - first);
    return n;
}
void pcm_queue_consume(struct pcm_queue *q, size_t n)
{
    assert(n <= q->used);
    q->head = (q->head + n) % q->capacity;
    q->used -= n; q->delivered += n;
}
void pcm_queue_reset(struct pcm_queue *q)
{
    q->discarded += q->used; q->used = q->head = 0;
}
