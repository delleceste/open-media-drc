package com.omdrc.widget

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.View
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** Lightweight animated stereo RMS meter suitable for a PiP surface. */
class LevelsView(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(35, 37, 43) }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textAlign = Paint.Align.CENTER
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }
    private val displayed = doubleArrayOf(FLOOR_DB, FLOOR_DB)
    private val target = doubleArrayOf(FLOOR_DB, FLOOR_DB)
    private val peaks = doubleArrayOf(FLOOR_DB, FLOOR_DB)
    private var lastFrame = 0L
    private var animating = false
    var compact = false
        set(value) { field = value; invalidate() }

    init {
        setBackgroundColor(Color.rgb(15, 16, 20))
    }

    fun setLevels(left: Double, right: Double, leftPeak: Double, rightPeak: Double) {
        target[0] = finiteOrFloor(left)
        target[1] = finiteOrFloor(right)
        peaks[0] = finiteOrFloor(leftPeak)
        peaks[1] = finiteOrFloor(rightPeak)
        if (!animating) {
            animating = true
            lastFrame = 0L
            postInvalidateOnAnimation()
        }
    }

    fun setDisconnected() {
        target.fill(FLOOR_DB)
        if (!animating) {
            animating = true
            lastFrame = 0L
            postInvalidateOnAnimation()
        }
    }

    fun stop() {
        animating = false
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val now = SystemClock.uptimeMillis()
        val dt = if (lastFrame == 0L) 0.0 else min(0.1, (now - lastFrame) / 1000.0)
        lastFrame = now
        var settled = true
        for (i in displayed.indices) {
            val from = displayed[i]
            val to = target[i]
            displayed[i] = if (to >= from) to else max(to, from - fallRate(from - to) * dt)
            if (abs(displayed[i] - to) > 0.1) settled = false
        }

        val pad = width * 0.045f
        val labelWidth = width * 0.10f
        val rightPad = width * 0.04f
        val rowGap = height * 0.10f
        val rowHeight = (height - pad * 2f - rowGap) / 2f
        label.textSize = min(rowHeight * 0.56f, width * 0.07f)
        drawMeter(canvas, 0, "L", pad, pad, width - rightPad, pad + rowHeight, labelWidth)
        val secondTop = pad + rowHeight + rowGap
        drawMeter(canvas, 1, "R", pad, secondTop, width - rightPad, secondTop + rowHeight, labelWidth)

        animating = !settled
        if (animating) postInvalidateOnAnimation()
    }

    private fun drawMeter(
        canvas: Canvas,
        channel: Int,
        name: String,
        left: Float,
        top: Float,
        right: Float,
        bottom: Float,
        labelWidth: Float,
    ) {
        val radius = (bottom - top) * 0.18f
        val trackLeft = left + labelWidth
        canvas.drawText(name, left + labelWidth * 0.38f,
            (top + bottom) / 2f - (label.ascent() + label.descent()) / 2f, label)
        canvas.drawRoundRect(RectF(trackLeft, top, right, bottom), radius, radius, track)
        val fraction = ((displayed[channel] - DISPLAY_FLOOR_DB) / -DISPLAY_FLOOR_DB)
            .coerceIn(0.0, 1.0).toFloat()
        val fillRight = trackLeft + (right - trackLeft) * fraction
        paint.color = when {
            displayed[channel] >= -3.0 -> Color.rgb(239, 83, 80)
            displayed[channel] >= -12.0 -> Color.rgb(255, 193, 7)
            else -> Color.rgb(55, 200, 113)
        }
        if (fillRight > trackLeft) {
            canvas.drawRoundRect(RectF(trackLeft, top, fillRight, bottom), radius, radius, paint)
        }

        // A thin instantaneous peak marker makes brief transients visible even
        // when the RMS fill is already falling between SSE frames.
        val peakFraction = ((peaks[channel] - DISPLAY_FLOOR_DB) / -DISPLAY_FLOOR_DB)
            .coerceIn(0.0, 1.0).toFloat()
        val peakX = trackLeft + (right - trackLeft) * peakFraction
        paint.color = Color.WHITE
        paint.strokeWidth = max(2f, width / 300f)
        canvas.drawLine(peakX, top, peakX, bottom, paint)
    }

    private fun fallRate(delta: Double) = if (delta <= 8.0) 120.0 else 45.0

    private fun finiteOrFloor(value: Double) = if (value.isFinite()) value else FLOOR_DB

    companion object {
        const val FLOOR_DB = -120.0
        private const val DISPLAY_FLOOR_DB = -60.0
    }
}
