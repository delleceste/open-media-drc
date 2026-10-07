package it.giacomos.omdrc.app

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.SeekBar
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import it.giacomos.omdrc.app.work.WidgetRefreshWorker
import kotlin.math.roundToInt

// Slider ranges: icon and label scale in percent, offset in half dp.
private const val ICON_MIN_PERCENT = 60
private const val ICON_MAX_PERCENT = 140
private const val LABEL_MIN_PERCENT = 60
private const val LABEL_MAX_PERCENT = 160
private const val OFFSET_MAX_HALF_DP = 32

class WidgetConfigureActivity : Activity() {

    private var appWidgetId = AppWidgetManager.INVALID_APPWIDGET_ID

    // The tuning saved before this session, restored if it's abandoned:
    // slider changes are written through as they happen, so the placed
    // widget shows them.
    private lateinit var savedTuning: TileTuning
    private var tuning = TileTuning()
    private var saved = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Standard widget-config boilerplate: if the user backs out without
        // saving, the host must see RESULT_CANCELED or it won't remove the
        // half-added widget.
        setResult(RESULT_CANCELED)
        setContentView(R.layout.activity_configure)

        appWidgetId = intent?.extras?.getInt(
            AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID,
        ) ?: AppWidgetManager.INVALID_APPWIDGET_ID

        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish()
            return
        }

        val hostInput = findViewById<EditText>(R.id.host_input)
        val portInput = findViewById<EditText>(R.id.port_input)

        val existing = WidgetPrefs.load(this, appWidgetId)
        hostInput.setText(existing?.first ?: AppPrefs.defaultHost(this) ?: "")
        portInput.setText((existing?.second ?: AppPrefs.defaultPort(this)).toString())

        savedTuning = WidgetPrefs.loadTileTuning(this, appWidgetId)
        tuning = savedTuning
        setUpTileSection()

        findViewById<Button>(R.id.save_button).setOnClickListener {
            val host = hostInput.text.toString().trim()
            val port = portInput.text.toString().trim().toIntOrNull()
            if (host.isEmpty() || port == null || port !in 1..65535) {
                Toast.makeText(this, "Enter a valid host and port", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            save(host, port)
        }
    }

    override fun onDestroy() {
        if (isFinishing && !saved && ::savedTuning.isInitialized && tuning != savedTuning) {
            WidgetPrefs.saveTileTuning(this, appWidgetId, savedTuning)
            RefreshEngine.renderCached(this, appWidgetId)
        }
        super.onDestroy()
    }

    /** Only shown for the 1x1 tile - the other sizes are text layouts the
     *  launcher's icons say nothing about. */
    private fun setUpTileSection() {
        val section = findViewById<View>(R.id.tile_section)
        val manager = AppWidgetManager.getInstance(this)
        if (RefreshEngine.sizeFor(manager, appWidgetId) != WidgetSize.TINY) {
            section.visibility = View.GONE
            return
        }

        val iconLabel = findViewById<TextView>(R.id.icon_size_label)
        val iconSeek = findViewById<SeekBar>(R.id.icon_size_seek)
        val labelLabel = findViewById<TextView>(R.id.label_size_label)
        val labelSeek = findViewById<SeekBar>(R.id.label_size_seek)
        val offsetLabel = findViewById<TextView>(R.id.offset_label)
        val offsetSeek = findViewById<SeekBar>(R.id.offset_seek)
        val fontSpinner = findViewById<Spinner>(R.id.font_spinner)
        val outlineSwitch = findViewById<Switch>(R.id.outline_switch)

        iconSeek.max = ICON_MAX_PERCENT - ICON_MIN_PERCENT
        labelSeek.max = LABEL_MAX_PERCENT - LABEL_MIN_PERCENT
        offsetSeek.max = 2 * OFFSET_MAX_HALF_DP

        // null: the launcher's font, whatever it resolves to.
        val families = listOf<String?>(null) + RemoteViewsBuilder.availableLabelFonts()
        val launcherFamily = RemoteViewsBuilder.launcherFontFamily(this)
        val names = families.map { family ->
            if (family == null) {
                getString(R.string.tile_font_launcher, launcherFamily?.let(::fontName) ?: getString(R.string.tile_font_system))
            } else {
                fontName(family)
            }
        }
        // Each entry in its own face, to choose by eye.
        fun styled(view: View, position: Int): View {
            val family = families[position] ?: launcherFamily
            (view as TextView).typeface = family?.let { Typeface.create(it, Typeface.NORMAL) } ?: Typeface.DEFAULT
            return view
        }
        val adapter = object : ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, names) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup) =
                styled(super.getView(position, convertView, parent), position)

            override fun getDropDownView(position: Int, convertView: View?, parent: ViewGroup) =
                styled(super.getDropDownView(position, convertView, parent), position)
        }
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        fontSpinner.adapter = adapter

        fun show() {
            iconSeek.progress = (tuning.iconScale * 100).roundToInt() - ICON_MIN_PERCENT
            labelSeek.progress = (tuning.labelScale * 100).roundToInt() - LABEL_MIN_PERCENT
            offsetSeek.progress = (tuning.offsetDp * 2).roundToInt() + OFFSET_MAX_HALF_DP
            fontSpinner.setSelection(families.indexOf(tuning.fontFamily).coerceAtLeast(0))
            iconLabel.text = getString(R.string.tile_icon_size, (tuning.iconScale * 100).roundToInt())
            labelLabel.text = getString(R.string.tile_label_size, (tuning.labelScale * 100).roundToInt())
            offsetLabel.text = getString(R.string.tile_offset, tuning.offsetDp)
            outlineSwitch.isChecked = tuning.outline
        }

        fun apply(changed: TileTuning) {
            if (changed == tuning) return
            tuning = changed
            show()
            WidgetPrefs.saveTileTuning(this, appWidgetId, tuning)
            RefreshEngine.renderCached(this, appWidgetId)
        }

        show()
        val listener = object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
                if (!fromUser) return
                apply(
                    when (seekBar) {
                        iconSeek -> tuning.copy(iconScale = (progress + ICON_MIN_PERCENT) / 100f)
                        labelSeek -> tuning.copy(labelScale = (progress + LABEL_MIN_PERCENT) / 100f)
                        else -> tuning.copy(offsetDp = (progress - OFFSET_MAX_HALF_DP) / 2f)
                    },
                )
            }

            override fun onStartTrackingTouch(seekBar: SeekBar) {}
            override fun onStopTrackingTouch(seekBar: SeekBar) {}
        }
        iconSeek.setOnSeekBarChangeListener(listener)
        labelSeek.setOnSeekBarChangeListener(listener)
        offsetSeek.setOnSeekBarChangeListener(listener)
        fontSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>, view: View?, position: Int, id: Long) {
                apply(tuning.copy(fontFamily = families[position]))
            }

            override fun onNothingSelected(parent: AdapterView<*>) {}
        }
        outlineSwitch.setOnCheckedChangeListener { _, checked -> apply(tuning.copy(outline = checked)) }
        findViewById<Button>(R.id.tile_reset_button).setOnClickListener { apply(TileTuning(outline = tuning.outline)) }
    }

    /** "google-sans-text" -> "Google Sans Text". */
    private fun fontName(family: String) =
        family.split('-').joinToString(" ") { it.replaceFirstChar(Char::uppercase) }

    private fun save(host: String, port: Int) {
        saved = true
        WidgetPrefs.save(this, appWidgetId, host, port)
        WidgetPrefs.saveTileTuning(this, appWidgetId, tuning)
        AppPrefs.setDefault(this, host, port)

        // Paint from cache immediately (there is none yet, so this shows
        // the "loading" state) and kick the real fetch right away so the
        // widget has live data within a couple seconds of being added,
        // rather than waiting for the first alarm tick.
        RefreshEngine.renderCached(this, appWidgetId)
        WidgetRefreshWorker.enqueueOneTime(this, appWidgetId)

        val resultValue = Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
        setResult(RESULT_OK, resultValue)
        finish()
    }
}
