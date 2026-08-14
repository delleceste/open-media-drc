/* Display-only fractional-octave smoothing for the verified response page.
 *
 * The source arrays are never modified.  Dense traces are resampled on a
 * logarithmic frequency grid before smoothing so the browser cost is bounded
 * and the displayed resolution remains much finer than a chart pixel.
 */
(function (root) {
    'use strict';

    const MAX_DISPLAY_POINTS = 1600;
    const FWHM_TO_SIGMA = 1 / (2 * Math.sqrt(2 * Math.log(2)));
    const VALID_KINDS = new Set(['none', 'variable', 'psychoacoustic', 'octave-6', 'octave-3']);

    function interpolate(a, b, position) {
        return a + (b - a) * position;
    }

    function logPosition(frequency, low, high) {
        return Math.log(frequency / low) / Math.log(high / low);
    }

    function bandwidthOctaves(kind, frequency) {
        if (kind === 'octave-6') return 1 / 6;
        if (kind === 'octave-3') return 1 / 3;
        if (kind === 'psychoacoustic') {
            if (frequency <= 100) return 1 / 3;
            if (frequency >= 1000) return 1 / 6;
            return interpolate(1 / 3, 1 / 6, logPosition(frequency, 100, 1000));
        }
        if (kind === 'variable') {
            if (frequency <= 100) return 1 / 48;
            if (frequency < 1000) {
                return interpolate(1 / 48, 1 / 6, logPosition(frequency, 100, 1000));
            }
            if (frequency < 10000) {
                return interpolate(1 / 6, 1 / 3, logPosition(frequency, 1000, 10000));
            }
            return 1 / 3;
        }
        return 0;
    }

    function wrappedDeltaDegrees(from, to) {
        return ((to - from + 540) % 360) - 180;
    }

    function cleanSeries(frequencies, values) {
        const cleanFrequencies = [];
        const cleanValues = [];
        const count = Math.min(frequencies.length, values.length);
        for (let index = 0; index < count; index += 1) {
            if (frequencies[index] === null || values[index] === null) continue;
            const frequency = Number(frequencies[index]);
            const value = Number(values[index]);
            if (frequency > 0 && Number.isFinite(frequency) && Number.isFinite(value)) {
                cleanFrequencies.push(frequency);
                cleanValues.push(value);
            }
        }
        return {frequencies:cleanFrequencies, values:cleanValues};
    }

    function resampleLog(frequencies, values, phase) {
        const clean = cleanSeries(frequencies, values);
        if (clean.frequencies.length <= MAX_DISPLAY_POINTS) return clean;

        const outputFrequencies = [];
        const outputValues = [];
        const low = Math.log(clean.frequencies[0]);
        const high = Math.log(clean.frequencies[clean.frequencies.length - 1]);
        let upper = 1;
        for (let index = 0; index < MAX_DISPLAY_POINTS; index += 1) {
            const position = index / (MAX_DISPLAY_POINTS - 1);
            const frequency = Math.exp(interpolate(low, high, position));
            while (upper < clean.frequencies.length - 1 && clean.frequencies[upper] < frequency) {
                upper += 1;
            }
            const lower = Math.max(0, upper - 1);
            const f0 = clean.frequencies[lower];
            const f1 = clean.frequencies[upper];
            const fraction = f1 === f0 ? 0 : Math.log(frequency / f0) / Math.log(f1 / f0);
            const v0 = clean.values[lower];
            const v1 = clean.values[upper];
            const value = phase
                ? v0 + wrappedDeltaDegrees(v0, v1) * fraction
                : interpolate(v0, v1, fraction);
            outputFrequencies.push(frequency);
            outputValues.push(value);
        }
        return {frequencies:outputFrequencies, values:outputValues};
    }

    function lowerBound(values, target) {
        let low = 0;
        let high = values.length;
        while (low < high) {
            const middle = Math.floor((low + high) / 2);
            if (values[middle] < target) low = middle + 1;
            else high = middle;
        }
        return low;
    }

    function upperBound(values, target) {
        let low = 0;
        let high = values.length;
        while (low < high) {
            const middle = Math.floor((low + high) / 2);
            if (values[middle] <= target) low = middle + 1;
            else high = middle;
        }
        return low;
    }

    function smoothTrace(frequencies, values, valueKind, smoothingKind) {
        if (!VALID_KINDS.has(smoothingKind)) throw new Error(`Unknown smoothing: ${smoothingKind}`);
        if (smoothingKind === 'none') return {frequencies, values};

        const phase = valueKind === 'phase_deg';
        const series = resampleLog(frequencies, values, phase);
        const logFrequencies = series.frequencies.map(frequency => Math.log2(frequency));
        const output = [];

        for (let index = 0; index < series.frequencies.length; index += 1) {
            const bandwidth = bandwidthOctaves(smoothingKind, series.frequencies[index]);
            const sigma = bandwidth * FWHM_TO_SIGMA;
            const radius = 3 * sigma;
            const center = logFrequencies[index];
            const first = lowerBound(logFrequencies, center - radius);
            const last = upperBound(logFrequencies, center + radius);
            let totalWeight = 0;
            let accumulator = 0;
            let sine = 0;
            let cosine = 0;

            for (let sample = first; sample < last; sample += 1) {
                const distance = (logFrequencies[sample] - center) / sigma;
                const weight = Math.exp(-0.5 * distance * distance);
                totalWeight += weight;
                if (phase) {
                    const radians = series.values[sample] * Math.PI / 180;
                    sine += weight * Math.sin(radians);
                    cosine += weight * Math.cos(radians);
                } else if (smoothingKind === 'psychoacoustic') {
                    const amplitude = Math.pow(10, series.values[sample] / 20);
                    accumulator += weight * amplitude * amplitude * amplitude;
                } else {
                    const power = Math.pow(10, series.values[sample] / 10);
                    accumulator += weight * power;
                }
            }

            if (phase) {
                output.push(Math.atan2(sine, cosine) * 180 / Math.PI);
            } else if (smoothingKind === 'psychoacoustic') {
                output.push(20 * Math.log10(Math.cbrt(accumulator / totalWeight)));
            } else {
                output.push(10 * Math.log10(accumulator / totalWeight));
            }
        }
        return {frequencies:series.frequencies, values:output};
    }

    root.FilterResponseSmoothing = Object.freeze({smoothTrace, bandwidthOctaves});
}(typeof globalThis === 'undefined' ? window : globalThis));
