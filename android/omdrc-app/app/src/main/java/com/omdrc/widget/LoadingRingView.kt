package com.omdrc.widget

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.SweepGradient
import android.util.AttributeSet
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import kotlin.math.min

/**
 * The page-load indicator: a large ring in the middle of the screen whose arc fills
 * with the load progress, with the percentage in the centre.  The arc eases towards
 * the reported value (WebView reports progress in coarse jumps) and its gradient
 * slowly rotates so a load that stalls still looks alive.
 */
class LoadingRingView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val density = resources.displayMetrics.density
    private val stroke = 10f * density
    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = stroke; color = Color.argb(60, 255, 255, 255)
    }
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = stroke * 2.4f; strokeCap = Paint.Cap.ROUND
        alpha = 50
    }
    private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = stroke; strokeCap = Paint.Cap.ROUND
    }
    private val percent = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE; textAlign = Paint.Align.CENTER; textSize = 40f * density
        isFakeBoldText = true
    }
    private val caption = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(170, 255, 255, 255); textAlign = Paint.Align.CENTER; textSize = 13f * density
        letterSpacing = 0.15f
    }
    private val bounds = RectF()
    private val colors = intArrayOf(
        Color.parseColor("#1F6FEB"), Color.parseColor("#58A6FF"), Color.parseColor("#3FB950"),
        Color.parseColor("#D8C23A"), Color.parseColor("#1F6FEB"),
    )

    private var shown = 0f          // eased value drawn, 0..100
    private var spin = 0f           // gradient rotation, degrees
    var label: String = "LOADING"

    private val easer = ValueAnimator.ofFloat(0f, 0f).apply {
        duration = 350; interpolator = DecelerateInterpolator()
        addUpdateListener { shown = it.animatedValue as Float; invalidate() }
    }
    private val spinner = ValueAnimator.ofFloat(0f, 360f).apply {
        duration = 2400; repeatCount = ValueAnimator.INFINITE; interpolator = LinearInterpolator()
        addUpdateListener { spin = it.animatedValue as Float; invalidate() }
    }

    fun setProgress(value: Int) {
        val target = value.coerceIn(0, 100).toFloat()
        easer.cancel()
        easer.setFloatValues(shown, target)
        easer.start()
    }

    /** Show from zero (a new load). */
    fun start() {
        easer.cancel(); shown = 0f
        animate().cancel(); alpha = 1f; visibility = VISIBLE
        if (!spinner.isStarted) spinner.start()
        invalidate()
    }

    /** Fill to 100 and fade away. */
    fun finish() {
        if (visibility != VISIBLE) return
        setProgress(100)
        animate().alpha(0f).setStartDelay(250).setDuration(300).withEndAction {
            visibility = GONE; spinner.cancel()
        }.start()
    }

    /** Hide at once (a failed load: the error page must stay readable). */
    fun hide() {
        animate().cancel(); easer.cancel(); spinner.cancel(); visibility = GONE
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val radius = min(width, height) / 2f - stroke * 1.4f
        bounds.set(cx - radius, cy - radius, cx + radius, cy + radius)
        canvas.drawCircle(cx, cy, radius, track)

        val shader = SweepGradient(cx, cy, colors, null)
        val m = android.graphics.Matrix()
        m.setRotate(spin - 90f, cx, cy)
        shader.setLocalMatrix(m)
        arc.shader = shader
        glow.shader = shader
        val sweep = 360f * shown / 100f
        if (sweep > 0.5f) {
            canvas.drawArc(bounds, -90f, sweep, false, glow)
            canvas.drawArc(bounds, -90f, sweep, false, arc)
        }
        canvas.drawText("${shown.toInt()}%", cx, cy + percent.textSize * 0.35f, percent)
        canvas.drawText(label, cx, cy + percent.textSize * 0.35f + caption.textSize * 1.9f, caption)
    }

    override fun onDetachedFromWindow() {
        easer.cancel(); spinner.cancel()
        super.onDetachedFromWindow()
    }
}
