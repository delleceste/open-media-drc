#include "queue.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
/* Independent linear FIFO oracle; tiny odd capacities force wraparound. */
int main(void)
{
    unsigned char oracle[1031], input[149], output[149];
    uint32_t rng = 917;
    for (size_t cap = 1; cap <= sizeof(oracle); cap += 7) {
        struct pcm_queue q;
        assert(pcm_queue_init(&q, cap) == 0);
        size_t used = 0;
        for (unsigned step = 0; step < 10000; ++step) {
            rng = rng * 1664525u + 1013904223u;
            size_t n = (rng >> 8) % sizeof(input);
            if ((rng & 31) == 0) {
                pcm_queue_reset(&q); used = 0;
            } else if (rng & 1) {
                for (size_t i = 0; i < n; ++i) input[i] = (unsigned char)(step + i);
                size_t expected = n < cap - used ? n : cap - used;
                assert(pcm_queue_write(&q, input, n) == expected);
                memcpy(oracle + used, input, expected); used += expected;
            } else {
                size_t expected = n < used ? n : used;
                assert(pcm_queue_peek(&q, output, n) == expected);
                assert(memcmp(output, oracle, expected) == 0);
                /* Peek must not consume, including before an aborted copy. */
                assert(q.used == used);
                pcm_queue_consume(&q, expected);
                used -= expected; memmove(oracle, oracle + expected, used);
            }
            assert(q.used == used && q.high_water <= cap);
            assert(q.accepted == q.delivered + q.used + q.discarded);
        }
        pcm_queue_free(&q);
    }
    puts("queue: random FIFO oracle, wraparound, reset and conservation passed");
    return 0;
}
