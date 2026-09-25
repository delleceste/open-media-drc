package com.omdrc.widget

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.ViewConfiguration
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import kotlin.math.abs

/**
 * Pull-to-reload that only claims clearly vertical, downward drags.  The stock layout
 * takes any drag with enough vertical travel, which would steal the kiosk's
 * horizontal page swipes whenever the finger also moved down a little.
 */
class VerticalSwipeRefreshLayout @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : SwipeRefreshLayout(context, attrs) {

    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var startX = 0f
    private var startY = 0f
    private var horizontal = false

    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean {
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> { startX = ev.x; startY = ev.y; horizontal = false }
            MotionEvent.ACTION_MOVE -> {
                val dx = abs(ev.x - startX)
                val dy = ev.y - startY
                if (!horizontal && dx > slop && dx > dy * 0.8f) horizontal = true
                if (horizontal) return false
            }
        }
        return super.onInterceptTouchEvent(ev)
    }
}
