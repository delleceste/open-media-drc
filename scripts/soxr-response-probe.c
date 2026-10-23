/*
 * Measure the installed libsoxr with MPD's "very high", one-thread recipe.
 * Uses normalized FLOAT32 interleaved stereo, as MPD 0.24.13 does.
 * The impulse is exactly representable in 24-bit PCM. Output is never played.
 * This isolates the library; it does not exercise MPD streaming or hardware.
 * Build/run instructions: doc/SOXR-44100-192000-INVESTIGATION.md
 */
#include <math.h>
#include <soxr.h>
#include <stdio.h>
#include <stdlib.h>
int main(void) {
  const size_t ni = 44100, no = 192000;
  float *in = calloc(ni * 2, sizeof(float)),
        *out = calloc((no + 1024) * 2, sizeof(float));
  if (!in || !out)
    return 2;
  in[22050 * 2] = in[22050 * 2 + 1] = 0.5f;
  soxr_quality_spec_t q = soxr_quality_spec(SOXR_VHQ, 0);
  soxr_runtime_spec_t r = soxr_runtime_spec(1);
  soxr_error_t err;
  size_t used, done;
  soxr_t s = soxr_create(44100, 192000, 2, &err, NULL, &q, &r);
  if (!s) {
    fprintf(stderr, "%s\n", err);
    return 2;
  }
  printf("version=%s engine=%s precision=%.0f phase=%.6f passband=%.9f "
         "stopband=%.9f\n",
         soxr_version(), soxr_engine(s), q.precision, q.phase_response,
         q.passband_end, q.stopband_begin);
  soxr_delete(s);
  err = soxr_oneshot(44100, 192000, 2, in, ni, &used, out, no + 1024, &done,
                     NULL, &q, &r);
  if (err || used != ni || done != no) {
    fprintf(stderr, "resample: %s used=%zu done=%zu\n", soxr_strerror(err),
            used, done);
    return 2;
  }
  size_t different = 0;
  for (size_t n = 0; n < done; n++)
    different += out[2 * n] != out[2 * n + 1];
  printf("frames=%zu identical_channel_differences=%zu\n", done, different);
  const double f[] = {1000,  4000,  8000,  12000, 16000,
                      19000, 20000, 21000, 22000};
  for (size_t j = 0; j < sizeof(f) / sizeof(f[0]); j++) {
    double re = 0, im = 0;
    for (size_t n = 0; n < done; n++) {
      double a = 2 * acos(-1) * f[j] * ((double)n - 96000) / 192000;
      re += out[2 * n] * cos(a);
      im -= out[2 * n] * sin(a);
    }
    printf("hz=%.0f gain_db=%.9f phase_deg=%.9f\n", f[j],
           20 * log10(hypot(re, im) / (0.5 * 192000 / 44100)),
           atan2(im, re) * 180 / acos(-1));
  }
  in[22050 * 2 + 1] = 0;
  err = soxr_oneshot(44100, 192000, 2, in, ni, &used, out, no + 1024, &done,
                     NULL, &q, &r);
  if (err)
    return 2;
  double leakage = 0;
  for (size_t n = 0; n < done; n++)
    if (fabs(out[2 * n + 1]) > leakage)
      leakage = fabs(out[2 * n + 1]);
  printf("left_only_right_peak=%.12g\n", leakage);
  free(in);
  free(out);
  return different || leakage != 0;
}
