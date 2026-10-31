/* Benchmark-tool instrumentation only. Never linked into an Axon executable.
 * wrk 4.1 time_us() uses gettimeofday(); make its durations monotonic even if
 * the VM wall clock is corrected. The preload is set only on the wrk process.
 */
#include <sys/time.h>
#include <time.h>
int gettimeofday(struct timeval *restrict tv, void *restrict timezone) {
    (void)timezone;
    struct timespec now;
    int result = clock_gettime(CLOCK_MONOTONIC, &now);
    if (result == 0) { tv->tv_sec = now.tv_sec; tv->tv_usec = now.tv_nsec / 1000; }
    return result;
}
