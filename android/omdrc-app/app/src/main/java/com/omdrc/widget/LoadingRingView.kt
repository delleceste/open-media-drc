package com.omdrc.widget

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.graphics.RectF
import android.graphics.SweepGradient
import android.util.AttributeSet
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import kotlin.math.min

/**
 * The splash while the page loads: the whole screen black, lit from the middle by a
 * soft white glow (with the phone in light mode: pale, shaded towards the edges), and on it a ring whose arc fills with the load progress, with the
 * percentage in the centre.  It covers the page until the page is ready, then fades.  The arc eases towards
 * the reported value (WebView reports progress in coarse jumps) and its gradient
 * slowly rotates so a load that stalls still looks alive.
 */
class LoadingRingView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val density = resources.displayMetrics.density
    /** The phone's dark mode, as the kiosk page follows it (values-night). */
    private val night = (resources.configuration.uiMode and
        android.content.res.Configuration.UI_MODE_NIGHT_MASK) == android.content.res.Configuration.UI_MODE_NIGHT_YES
    private val ink = if (night) Color.WHITE else Color.rgb(31, 35, 40)
    private val stroke = 10f * density
    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = stroke; color = if (night) Color.argb(60, 255, 255, 255) else Color.argb(40, 31, 35, 40)
    }
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = stroke * 2.4f; strokeCap = Paint.Cap.ROUND
        alpha = 50
    }
    private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = stroke; strokeCap = Paint.Cap.ROUND
    }
    private val percent = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ink; textAlign = Paint.Align.CENTER; textSize = 40f * density
        isFakeBoldText = true
    }
    private val caption = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(170, Color.red(ink), Color.green(ink), Color.blue(ink)); textAlign = Paint.Align.CENTER; textSize = 13f * density
        letterSpacing = 0.15f
    }
    private val ringRadius = 85f * density - stroke * 1.4f
    private val backdrop = Paint()
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

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w <= 0 || h <= 0) return
        backdrop.shader = RadialGradient(w / 2f, h / 2f, min(w, h) * 0.75f,
            if (night) intArrayOf(Color.rgb(92, 96, 102), Color.rgb(38, 40, 44), Color.rgb(10, 10, 12), Color.BLACK)
            else intArrayOf(Color.WHITE, Color.rgb(246, 248, 250), Color.rgb(228, 232, 238), Color.rgb(208, 215, 222)),
            floatArrayOf(0f, 0.3f, 0.7f, 1f), Shader.TileMode.CLAMP)
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawPaint(backdrop)
        val cx = width / 2f
        val cy = height / 2f
        val radius = min(ringRadius, min(width, height) / 2f - stroke * 1.4f)
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
